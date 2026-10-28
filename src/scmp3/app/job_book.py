"""The jobs in the queue, the one lock that guards them, and the bookkeeping every step shares.

Every method here expects the caller to hold `lock`. A task claims its step with a ticket, does
its slow work outside the lock, then applies the result under it, but only while its ticket is
still the step's. Skipping, removing, cancelling or retrying replaces or drops the ticket, so a
result that came too late is dropped instead of undoing what the user did.

Every change ends with `publish`, which tells the UI and queue.json. Their dicts are built later
from a snapshot, outside the lock, so a queue of thousands never holds it for long. A set's tracks
are records of their own, outside the queue's order, and each change to one publishes its set.
"""

import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime

from scmp3.app.events import Deferred, EventBus
from scmp3.app.job_record import JobRecord, Step, Ticket
from scmp3.app.job_steps import Steps
from scmp3.app.set_tracks import set_state
from scmp3.app.workers import Pool, Timers
from scmp3.core import download
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.inputs import ParsedInput
from scmp3.core.models import JobError, JobState, Settings

# The automatic retries the error messages promise, in seconds: the first wait, the second and
# so on, with the last one repeating. A rate limit backs off, since YouTube's can last an hour.
AUTO_RETRY_WAITS: dict[ErrorKind, tuple[float, ...]] = {
    ErrorKind.offline: (30.0,),
    ErrorKind.rate_limited: (60.0, 120.0, 300.0, 600.0, 1800.0),
}

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
    return {url for url in (queue_key(record.parsed), source.url if source else None) if url}


def queue_key(parsed: ParsedInput) -> str | None:
    """What a queued job is found by: its normalized link, or a file's full path.

    A file's source has its path as its url, so the key stays the same once it's read.
    """
    if parsed.url is not None:
        return parsed.url
    return str(parsed.path) if parsed.path is not None else None


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
        # The time on the wall, in seconds, for when an automatic retry will run.
        self._clock: Callable[[], float] = time.time
        # Covers, deleting staging folders, and refreshing the jobs after a settings change.
        self.misc_pool = misc_pool
        # Reentrant, since the helpers that change a job call each other.
        self.lock = threading.RLock()
        self.records: dict[str, JobRecord] = {}
        # Every set's tracks by id, so the calls that name one find it.
        self.set_tracks: dict[str, JobRecord] = {}
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
        """Put a job at the end of the queue, with its tracks if it's a set."""
        self.records[record.id] = record
        self.links.add(queue_key(record.parsed), record)
        if record.job.source is not None:
            self.links.add(record.job.source.url, record)
        self.add_tracks(record.children)

    def add_tracks(self, tracks: Iterable[JobRecord]) -> None:
        for track in tracks:
            self.set_tracks[track.id] = track

    def find(self, job_id: str) -> JobRecord | None:
        """A queued job, or a track of a queued set, by its id."""
        return self.records.get(job_id) or self.set_tracks.get(job_id)

    def tracks(self) -> Iterator[JobRecord]:
        """Every track: the queued ones that aren't sets, and each set's own."""
        for record in list(self.records.values()):
            if record.job.kind == "set":
                yield from record.children
            else:
                yield record

    def every_record(self) -> list[JobRecord]:
        """The queued jobs and every set's tracks."""
        return [*self.records.values(), *self.set_tracks.values()]

    # Telling the UI

    def publish(self, record: JobRecord) -> None:
        """Send the job to the UI as it is now, and have queue.json written soon.

        A set's track goes out as part of its set, and a listed set's state follows its tracks.
        """
        if record.removed:
            return
        record.changed()
        if record.parent is not None:
            self.publish(record.parent)
            return
        if record.prepared_set is not None and not record.closed:
            record.job.state = set_state(record.children)
        snapshot = record.snapshot()
        settings = self.settings
        # Built on the event bus's thread, and only if no newer change replaced it by then. A set's
        # event carries only the tracks that changed since the last one.
        if record.job.kind == "set":
            sent = record.sent_tracks
            self._events.emit(
                Deferred(
                    "job",
                    record.id,
                    lambda: sent.event(snapshot.view(settings)),
                    on_lost=sent.forget,
                )
            )
        else:
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
        waits = AUTO_RETRY_WAITS.get(error.kind)
        if waits is None:
            record.auto_retries = 0
            return
        delay = waits[min(record.auto_retries, len(waits) - 1)]
        record.auto_retries += 1
        record.retry_at = datetime.fromtimestamp(self._clock() + delay, UTC)
        record.retry_timer = self._timers.call_later(delay, lambda: self._auto_retry(record, error))

    def _auto_retry(self, record: JobRecord, error: JobError) -> None:
        with self.lock:
            # Anything that happened to the job since then makes this retry stale.
            if record.closed or record.job.error is not error:
                return
            record.retry_timer = None
            record.retry_at = None
            self.retry(record)

    def _step_worked(self, record: JobRecord) -> None:
        """A step went through, so the next failure's automatic retry starts at the first wait."""
        record.auto_retries = 0

    # Skipping and removing

    def close(self, record: JobRecord) -> None:
        """Stop everything in flight for a job that's skipped or removed, and delete its audio.

        A set's tracks are closed with it.
        """
        for track in record.children:
            self.close(track)
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
        # A set's track reaches the UI with its set, which reuses an unchanged copy.
        record.changed()
        if record.busy == 0:
            self._discard_later(record)

    def remove(self, record: JobRecord) -> None:
        self.close(record)
        self.links.drop(record)
        record.removed = True
        del self.records[record.id]
        for track in record.children:
            track.removed = True
            self.set_tracks.pop(track.id, None)
        self._events.emit({"type": "job_removed", "job_id": record.id})
        self.on_change()

    def _cancel_auto_retry(self, record: JobRecord) -> None:
        record.retry_at = None
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
        if record.discarded:
            return
        if prepared is not None:
            record.discarded = True
            self.misc_pool.submit(lambda: self._steps.discard(prepared))
        elif record.job.kind == "set":
            # A set's own folder holds only the album's cover.
            record.discarded = True
            self.misc_pool.submit(lambda: download.cleanup(record.id))
