"""How each job moves through the pipeline: resolving, downloading, saving, failing and retrying.

Every method here expects the caller to hold `lock`, except the tasks, which take it themselves,
as job_book.py describes. The cover tasks are in job_cover_tasks.py.
"""

import logging
import uuid
from collections.abc import Callable
from pathlib import Path

from scmp3.app.events import EventBus
from scmp3.app.input_reasons import reason_code
from scmp3.app.job_book import unexpected_error
from scmp3.app.job_cover_tasks import CoverTasks
from scmp3.app.job_covers import CoverReader, CoverView
from scmp3.app.job_record import JobRecord, Ticket, rebuilt_suggestion
from scmp3.app.job_saving import CollisionQuestion, save, save_order
from scmp3.app.job_steps import Steps
from scmp3.app.workers import Pool, Timers
from scmp3.core import pipeline
from scmp3.core.errors import Cancelled, ErrorKind, ScmpError
from scmp3.core.inputs import ParsedInput
from scmp3.core.models import Job, JobState, Progress, Settings
from scmp3.core.suggest import InvalidOverride
from scmp3.core.urls import UnsupportedInput

RESOLVE_WORKERS = 4
# Saves run one at a time, so two can't pick the same free file name (T-016).
SAVE_WORKERS = 1
MISC_WORKERS = 2

PoolMaker = Callable[[str, int], Pool]
SaveOutcome = pipeline.Saved | ScmpError | None

log = logging.getLogger(__name__)


class JobFlow(CoverTasks):
    """The jobs and the pools that run their steps."""

    def __init__(
        self,
        settings: Settings,
        events: EventBus,
        *,
        steps: Steps,
        covers: CoverReader,
        make_pool: PoolMaker,
        timers: Timers,
    ) -> None:
        misc_pool = make_pool("misc", MISC_WORKERS)
        super().__init__(settings, events, steps=steps, timers=timers, misc_pool=misc_pool)
        self._covers = covers
        self._resolve_pool = make_pool("resolve", RESOLVE_WORKERS)
        self._download_pool = make_pool("download", settings.concurrent_downloads)
        self.save_pool = make_pool("save", SAVE_WORKERS)

    def pools(self) -> list[Pool]:
        return [self._resolve_pool, self._download_pool, self.save_pool, self.misc_pool]

    def resize_downloads(self, size: int) -> None:
        self._download_pool.resize(size)

    # Adding and resolving

    def queue(self, parsed: ParsedInput) -> JobRecord:
        """Add a job for a supported input and start resolving it."""
        job = Job(id=uuid.uuid4().hex, kind="track", input=parsed.url or parsed.raw)
        record = JobRecord(job=job, parsed=parsed)
        self.records[job.id] = record
        self.publish(record)
        self._start_resolving(record)
        return record

    def _start_resolving(self, record: JobRecord) -> None:
        record.job.state = JobState.queued
        ticket = self._new_ticket(record, "resolve")
        self._resolve_pool.submit(lambda: self._resolve(record, ticket))

    def _resolve(self, record: JobRecord, ticket: Ticket) -> None:
        with self.lock:
            if not self._claim(record, "resolve", ticket):
                return
            record.job.state = JobState.resolving
            self.publish(record)
            settings = self.settings
        outcome: pipeline.Prepared | ScmpError | UnsupportedInput
        try:
            outcome = self._steps.prepare(record.parsed, settings=settings, job_id=record.id)
        except (ScmpError, UnsupportedInput) as error:
            outcome = error
        except Exception as error:
            outcome = unexpected_error("resolving", error)
        with self.lock:
            if not self._release(record, "resolve", ticket):
                return
            if isinstance(outcome, UnsupportedInput):
                self._reject_after_resolving(record, outcome)
            elif isinstance(outcome, ScmpError):
                self._fail(record, "resolve", outcome)
            else:
                self._resolved(record, outcome)

    def _resolved(self, record: JobRecord, prepared: pipeline.Prepared) -> None:
        job = record.job
        record.prepared = prepared
        job.source = prepared.source
        job.suggestion = prepared.suggestion
        if prepared.settings is not self.settings:
            # The settings changed while it resolved, so its cleanups follow the new ones.
            job.suggestion = rebuilt_suggestion(job.suggestion, job.source, {}, self.settings)
        job.duplicate = prepared.duplicate
        job.state = JobState.needs_review
        # The page shows the link at once, and the size follows once the image is read.
        record.source_cover = CoverView(url=prepared.source.artwork_url)
        self.start_source_cover(record)
        if self.settings.download_while_reviewing:
            self.start_download(record)
        if not self.settings.review_before_saving:
            self._save_without_review(record)
        self.publish(record)

    def _reject_after_resolving(self, record: JobRecord, error: UnsupportedInput) -> None:
        """A short link that led to something sc-mp3 can't save leaves the queue, with its code."""
        rejected = {"input": record.parsed.raw, "reason": reason_code(str(error))}
        self.remove(record)
        self._events.emit({"type": "rejected", "rejected": [rejected]})

    def _save_without_review(self, record: JobRecord) -> None:
        if record.job.duplicate is not None:
            self._fail(record, "save", ScmpError(ErrorKind.duplicate, "saved before"))
            return
        self.start_saving(record)

    # Downloading

    def start_download(self, record: JobRecord) -> None:
        if "download" in record.tickets or record.staged is not None:
            return
        ticket = self._new_ticket(record, "download")
        if record.download_running:
            # A cancelled download is still stopping, and two mustn't share the staging folder.
            record.download_deferred = True
            return
        self._download_pool.submit(lambda: self._download(record, ticket))

    def cancel_download(self, record: JobRecord) -> None:
        ticket = record.tickets.pop("download", None)
        if ticket is None:
            return
        ticket.cancel.set()
        record.job.progress = None

    def _download(self, record: JobRecord, ticket: Ticket) -> None:
        with self.lock:
            if not self._claim(record, "download", ticket):
                return
            record.download_running = True
            prepared = record.prepared
            assert prepared is not None
            # The format is fixed now, from the settings at this moment (TECH_SPEC §9).
            prepared.settings = self.settings
            stale, record.stale_audio = record.stale_audio, None
            record.job.progress = Progress(pct=0.0)
            self.publish(record)
        outcome: pipeline.Staged | ScmpError | Cancelled
        try:
            if stale is not None:
                # Done here, so it can't delete the file this download is about to write.
                _delete_file(stale)
            outcome = self._steps.fetch(
                prepared,
                on_progress=lambda progress: self._progress(record, ticket, progress),
                cancel=ticket.cancel,
            )
        except (ScmpError, Cancelled) as error:
            outcome = error
        except Exception as error:
            outcome = unexpected_error("downloading", error)
        with self.lock:
            record.download_running = False
            current = self._release(record, "download", ticket)
            self._start_deferred_download(record)
            if not current:
                return
            record.job.progress = None
            if isinstance(outcome, ScmpError):
                self._fail(record, "download", outcome)
            elif isinstance(outcome, pipeline.Staged):
                record.staged = outcome
                record.job.staged_audio = outcome.path
                self.save_if_ready(record)
            self.publish(record)

    def _start_deferred_download(self, record: JobRecord) -> None:
        """Start the download that waited for a cancelled one to stop."""
        if not record.download_deferred:
            return
        record.download_deferred = False
        ticket = record.tickets.get("download")
        if ticket is not None:
            self._download_pool.submit(lambda: self._download(record, ticket))

    def _progress(self, record: JobRecord, ticket: Ticket, progress: Progress) -> None:
        """Show a download's progress. It runs inside yt-dlp, so it never raises."""
        try:
            with self.lock:
                if record.closed or record.tickets.get("download") is not ticket:
                    return
                record.job.progress = progress
                self._events.emit(
                    {
                        "type": "progress",
                        "job_id": record.id,
                        "pct": progress.pct,
                        "speed": progress.speed,
                        "eta": progress.eta,
                    }
                )
        except Exception:
            # Raising here would fail the download as an internal error.
            log.exception("Showing a download's progress failed")

    # Saving

    def start_saving(self, record: JobRecord) -> None:
        """Save the job once its audio is in, downloading it first if it isn't."""
        job = record.job
        self._cancel_auto_retry(record)
        record.wants_save = True
        record.download_cancelled = False
        record.collision = None
        job.state = JobState.saving
        job.error = None
        if record.staged is None:
            self.start_download(record)
        self.save_if_ready(record)
        self.publish(record)

    def stop_saving(self, record: JobRecord) -> bool:
        """Put a job waiting to save back in review. False once finalize is running."""
        if record.saving_now:
            return False
        record.tickets.pop("save", None)
        record.wants_save = False
        record.job.state = JobState.needs_review
        return True

    def save_if_ready(self, record: JobRecord) -> None:
        """Send the job to the save worker once its audio and its cover are ready.

        A cover still being read or checked is waited for, so the save uses it rather than
        downloading it again, and nothing writes into staging once finalize has deleted it.
        """
        if record.job.state != JobState.saving or record.staged is None:
            return
        if any(step in record.tickets for step in ("cover", "source_cover", "save")):
            return
        ticket = self._new_ticket(record, "save")
        self.save_pool.submit(lambda: self._save(record, ticket))

    def _cover_settled(self, record: JobRecord) -> None:
        self.save_if_ready(record)

    def _save(self, record: JobRecord, ticket: Ticket) -> None:
        with self.lock:
            if not self._claim(record, "save", ticket):
                return
            record.saving_now = True
            order = save_order(record, self.settings)
        outcome: SaveOutcome | CollisionQuestion
        try:
            outcome = save(order, self._steps)
        except ScmpError as error:
            outcome = error
        except InvalidOverride as error:
            outcome = unexpected_error("rendering the tags", error)
        except Exception as error:
            outcome = unexpected_error("saving", error)
        with self.lock:
            record.saving_now = False
            if not self._release(record, "save", ticket):
                return
            self._saved(record, outcome)
            if record.remove_when_saved:
                self.remove(record)

    def _saved(self, record: JobRecord, outcome: SaveOutcome | CollisionQuestion) -> None:
        job = record.job
        if isinstance(outcome, ScmpError):
            self._fail(record, "save", outcome)
        elif isinstance(outcome, CollisionQuestion):
            log.info("The file name is taken, so the user is asked what to do")
            record.collision = outcome.existing
            record.wants_save = False
            job.state = JobState.needs_review
        else:
            # finalize deletes the staging folder, whether it saved or skipped.
            record.staged = None
            job.staged_audio = None
            record.collision_answer = None
            job.output_path = outcome.path if outcome else None
            record.cover_error = outcome.cover_error if outcome else None
            job.state = JobState.saved if outcome is not None else JobState.skipped
        self.publish(record)

    # Retrying

    def retry(self, record: JobRecord) -> None:
        """Run the failed step again, then carry on to where the job was going (TECH_SPEC §15)."""
        job = record.job
        kind = job.error.kind if job.error else None
        self._cancel_auto_retry(record)
        job.error = None
        if kind == ErrorKind.convert_failed and record.staged is not None:
            # The staged audio may be what failed, so it's downloaded again.
            record.stale_audio = record.staged.path
            record.staged = None
            job.staged_audio = None
        if record.failed_step == "resolve" or record.prepared is None:
            self._start_resolving(record)
        elif record.failed_step == "download" and not record.wants_save:
            job.state = JobState.needs_review
            self.start_download(record)
        else:
            self.start_saving(record)
        self.publish(record)


def _delete_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        log.warning("Couldn't delete the staged audio: %s", error.strerror or error)
