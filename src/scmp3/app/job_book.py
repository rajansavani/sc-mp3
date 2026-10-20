"""The jobs in the queue, the one lock that guards them, and the bookkeeping every step shares.

Every method here expects the caller to hold `lock`. A task claims its step with a ticket, does
its slow work outside the lock, then applies the result under it, but only while its ticket is
still the step's. Skipping, removing, cancelling or retrying replaces or drops the ticket, so a
result that came too late is dropped instead of undoing what the user did.
"""

import logging
import threading
from typing import Any

from scmp3.app.events import EventBus
from scmp3.app.job_record import JobRecord, Step, Ticket, job_view
from scmp3.app.job_steps import Steps
from scmp3.app.workers import Pool, Timers
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import JobError, JobState, Settings

# The automatic retries the error messages promise, in seconds.
AUTO_RETRY_SECONDS: dict[ErrorKind, float] = {ErrorKind.offline: 30.0, ErrorKind.rate_limited: 60.0}

log = logging.getLogger(__name__)


def unexpected_error(step: str, error: Exception) -> ScmpError:
    """The error for a step that failed in sc-mp3's own code, logged with its traceback.

    The detail names only the exception's type, since its message can hold a full path.
    """
    log.error("%s failed unexpectedly", step, exc_info=error)
    return ScmpError(ErrorKind.internal, f"{step} failed with {type(error).__name__}")


class JobBook:
    """The jobs in queue order, with what tells the UI about them and tracks their tasks."""

    def __init__(
        self,
        settings: Settings,
        events: EventBus,
        *,
        steps: Steps,
        timers: Timers,
        misc_pool: Pool,
    ) -> None:
        self.settings = settings
        # The number of the last settings save followed, so a late older one is ignored.
        self.settings_version = 0
        self._events = events
        self._steps = steps
        self._timers = timers
        # Covers, deleting staging folders, and refreshing the jobs after a settings change.
        self.misc_pool = misc_pool
        # Reentrant, since the helpers that change a job call each other.
        self.lock = threading.RLock()
        self.records: dict[str, JobRecord] = {}

    def retry(self, record: JobRecord) -> None:
        """Run a failed job's failed step again. The flow, which runs the steps, defines it."""
        raise NotImplementedError

    def queued_with_link(self, url: str | None) -> JobRecord | None:
        """The job already queued for a normalized link, before or after it resolved."""
        for record in self.records.values():
            source = record.job.source
            if url in (record.parsed.url, source.url if source else None):
                return record
        return None

    # Telling the UI

    def publish(self, record: JobRecord) -> None:
        """Send the job to the UI as it is now."""
        if record.removed:
            return
        self._events.emit({"type": "job", "job": job_view(record, self.settings)})

    def views(self) -> list[dict[str, Any]]:
        return [job_view(record, self.settings) for record in self.records.values()]

    # Failing

    def _fail(self, record: JobRecord, step: Step, error: ScmpError) -> None:
        log.info("A job stopped at the %s step: %s", step, error)
        job = record.job
        job.state = JobState.error
        job.error = JobError(kind=error.kind, detail=error.detail)
        job.progress = None
        record.failed_step = step
        record.error_where = error.where
        self._schedule_auto_retry(record, job.error)

    def _schedule_auto_retry(self, record: JobRecord, error: JobError) -> None:
        delay = AUTO_RETRY_SECONDS.get(error.kind)
        if delay is None:
            return
        record.retry_timer = self._timers.call_later(delay, lambda: self._auto_retry(record, error))

    def _auto_retry(self, record: JobRecord, error: JobError) -> None:
        with self.lock:
            # Anything that happened to the job since then makes this retry stale.
            if record.closed or record.job.error is not error:
                return
            record.retry_timer = None
            self.retry(record)

    # Skipping and removing

    def close(self, record: JobRecord) -> None:
        """Stop everything in flight for a job that's skipped or removed, and delete its audio."""
        for ticket in record.tickets.values():
            ticket.cancel.set()
        record.tickets.clear()
        self._cancel_auto_retry(record)
        record.closed = True
        record.wants_save = False
        record.collision = None
        record.staged = None
        record.job.staged_audio = None
        record.job.progress = None
        if record.busy == 0:
            self._discard_later(record)

    def remove(self, record: JobRecord) -> None:
        self.close(record)
        record.removed = True
        del self.records[record.id]
        self._events.emit({"type": "job_removed", "job_id": record.id})

    def _cancel_auto_retry(self, record: JobRecord) -> None:
        if record.retry_timer is not None:
            record.retry_timer.cancel()
            record.retry_timer = None

    # Tickets

    def _new_ticket(self, record: JobRecord, step: Step) -> Ticket:
        ticket = Ticket()
        record.tickets[step] = ticket
        return ticket

    def _claim(self, record: JobRecord, step: Step, ticket: Ticket) -> bool:
        """Whether a task may start, which it may while its ticket is the step's."""
        if record.closed or record.tickets.get(step) is not ticket:
            return False
        record.busy += 1
        return True

    def _release(self, record: JobRecord, step: Step, ticket: Ticket) -> bool:
        """Count a task as finished. Returns whether its result still counts."""
        record.busy -= 1
        current = not record.closed and record.tickets.get(step) is ticket
        if current:
            del record.tickets[step]
        if record.closed and record.busy == 0:
            self._discard_later(record)
        return current

    def _discard_later(self, record: JobRecord) -> None:
        """Delete the job's staging folder on a worker, once nothing for it is running."""
        prepared = record.prepared
        if prepared is None or record.discarded:
            return
        record.discarded = True
        self.misc_pool.submit(lambda: self._steps.discard(prepared))
