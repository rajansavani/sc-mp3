"""The jobs in the queue, the one lock that guards them, and the bookkeeping every step shares.

Every method here expects the caller to hold `lock`. A task claims its step with a ticket, does
its slow work outside the lock, then applies the result under it, but only while its ticket is
still the step's. Skipping, removing, cancelling or retrying replaces or drops the ticket, so a
result that came too late is dropped instead of undoing what the user did.

Every change ends with `publish`, which tells the UI and queue.json. Their dicts are built later
from a snapshot, outside the lock, so a queue of thousands never holds it for long.
"""

import logging
import threading
from collections.abc import Callable

from scmp3.app.events import Deferred, EventBus
from scmp3.app.job_record import JobRecord, Step, Ticket
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


def _nothing() -> None:
    pass


class LinkIndex:
    """The queued jobs by link, so finding one stays quick with thousands queued."""

    def __init__(self) -> None:
        self._by_link: dict[str, list[JobRecord]] = {}

    def add(self, url: str | None, record: JobRecord) -> None:
        if url is None:
            return
        records = self._by_link.setdefault(url, [])
        if not any(known is record for known in records):
            records.append(record)

    def drop(self, record: JobRecord) -> None:
        for url in _links_of(record):
            records = [known for known in self._by_link.get(url, ()) if known is not record]
            if records:
                self._by_link[url] = records
            else:
                self._by_link.pop(url, None)

    def find(self, url: str | None) -> JobRecord | None:
        """The job queued for a normalized link, before or after it resolved."""
        for record in self._by_link.get(url or "", ()):
            # A job resolved again since it was added may have a new link by now.
            if not record.removed and url in _links_of(record):
                return record
        return None


def _links_of(record: JobRecord) -> set[str]:
    source = record.job.source
    return {url for url in (record.parsed.url, source.url if source else None) if url}


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
        self.links = LinkIndex()
        # Called after every change, so queue.json is written soon. The job manager sets it.
        self.on_change: Callable[[], None] = _nothing

    def retry(self, record: JobRecord) -> None:
        """Run a failed job's failed step again. The flow, which runs the steps, defines it."""
        raise NotImplementedError

    def queued_with_link(self, url: str | None) -> JobRecord | None:
        """The job already queued for a normalized link, before or after it resolved."""
        return self.links.find(url)

    def add_record(self, record: JobRecord) -> None:
        """Put a job at the end of the queue."""
        self.records[record.id] = record
        self.links.add(record.parsed.url, record)
        if record.job.source is not None:
            self.links.add(record.job.source.url, record)

    # Telling the UI

    def publish(self, record: JobRecord) -> None:
        """Send the job to the UI as it is now, and have queue.json written soon."""
        if record.removed:
            return
        record.changed()
        snapshot = record.snapshot()
        settings = self.settings
        # Built on the event bus's thread, and only if no newer change replaced it by then.
        self._events.emit(
            Deferred("job", record.id, lambda: {"type": "job", "job": snapshot.view(settings)})
        )
        self.on_change()

    # Failing

    def _fail(self, record: JobRecord, step: Step, error: ScmpError) -> None:
        log.info("A job stopped at the %s step: %s", step, error)
        job = record.job
        job.state = JobState.error
        job.error = JobError(kind=error.kind, detail=error.detail)
        job.progress = None
        record.failed_step = step
        record.error_where = error.where
        record.error_reason = error.reason
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
        self.links.drop(record)
        record.removed = True
        del self.records[record.id]
        self._events.emit({"type": "job_removed", "job_id": record.id})
        self.on_change()

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
