"""How each job moves through the pipeline: resolving, saving, failing and retrying.

Every method here expects the caller to hold `lock`, except the tasks, which take it themselves,
as job_book.py describes. The download tasks are in job_downloads.py, the cover tasks in
job_cover_tasks.py, and listing a set in job_set_tasks.py.
"""

import logging
import uuid
from collections.abc import Callable, Collection
from typing import Protocol

from scmp3.app.events import EventBus
from scmp3.app.input_reasons import reason_code
from scmp3.app.job_book import unexpected_error
from scmp3.app.job_covers import CoverReader, CoverView
from scmp3.app.job_downloads import DOWNLOAD_SPACING
from scmp3.app.job_record import JobRecord, Ticket, rebuilt_suggestion
from scmp3.app.job_saving import CollisionQuestion, save, save_order
from scmp3.app.job_set_tasks import SetTasks
from scmp3.app.job_steps import Steps
from scmp3.app.workers import Pool, Spacing, Timers
from scmp3.core import pipeline
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.inputs import ParsedInput
from scmp3.core.models import Job, JobState, Settings
from scmp3.core.suggest import InvalidOverride
from scmp3.core.urls import UnsupportedInput

RESOLVE_WORKERS = 4
# Sources whose links resolve one at a time, this many seconds after the last one ended. Each
# YouTube resolve asks for a player page, and asking too fast brings on a bot check.
RESOLVE_SPACING: dict[str, float] = {"youtube": 2.0}
# Saves run one at a time, so two can't pick the same free file name (T-016).
SAVE_WORKERS = 1
MISC_WORKERS = 2

SaveOutcome = pipeline.Saved | ScmpError | None

log = logging.getLogger(__name__)


class PoolMaker(Protocol):
    """Makes a named pool of `size` workers, like TaskPool."""

    def __call__(
        self,
        name: str,
        size: int,
        *,
        spacing: Spacing | None = None,
        serial: Collection[str] = (),
    ) -> Pool: ...


class JobFlow(SetTasks):
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
        self._resolve_pool = make_pool(
            "resolve", RESOLVE_WORKERS, spacing=RESOLVE_SPACING, serial=RESOLVE_SPACING.keys()
        )
        self._download_pool = make_pool(
            "download", settings.concurrent_downloads, spacing=DOWNLOAD_SPACING
        )
        self.save_pool = make_pool("save", SAVE_WORKERS)

    def pools(self) -> list[Pool]:
        return [self._resolve_pool, self._download_pool, self.save_pool, self.misc_pool]

    def resize_downloads(self, size: int) -> None:
        self._download_pool.resize(size)

    # Adding and resolving

    def queue(self, parsed: ParsedInput) -> JobRecord:
        """Add a job for a supported input and start resolving it, or listing it if it's a set."""
        kind = "set" if parsed.kind == "soundcloud_set" else "track"
        job = Job(id=uuid.uuid4().hex, kind=kind, input=parsed.url or parsed.raw)
        record = JobRecord(job=job, parsed=parsed)
        self.add_record(record)
        self.publish(record)
        self._start_resolving(record)
        return record

    def _start_resolving(self, record: JobRecord) -> None:
        if record.job.kind == "set":
            self._start_listing(record)
            return
        record.job.state = JobState.queued
        ticket = self._new_ticket(record, "resolve")
        self._resolve_pool.submit(
            lambda: self._resolve(record, ticket),
            pace=pipeline.LINK_SOURCES.get(record.parsed.kind),
            wanted=lambda: not record.closed and record.tickets.get("resolve") is ticket,
        )

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
                self.publish(record)
            else:
                self._resolved(record, outcome)
            if record.parent is not None:
                # The album's numbers are known once every track's info is in.
                self.save_if_ready(record.parent)

    def _resolved(self, record: JobRecord, prepared: pipeline.Prepared) -> None:
        self._step_worked(record)
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
        if record.parent is None:
            self.links.add(prepared.source.url, record)
            # A set's track saves with its album's cover, or else loads its own when it saves.
            self.start_source_cover(record)
        if self.settings.download_while_reviewing and job.included:
            self.start_download(record)
        if job.included and record.wants_save:
            # Save was pressed on its set while its info was still coming.
            self.start_saving(record)
        elif job.included and not self.settings.review_before_saving:
            self._save_without_review(record)
        self.publish(record)

    def _reject_after_resolving(self, record: JobRecord, error: UnsupportedInput) -> None:
        """A short link that led to something sc-mp3 can't save leaves the queue, with its code.

        A set's track can't leave its set, so it fails instead.
        """
        if record.parent is not None:
            self._fail(record, "resolve", ScmpError(ErrorKind.unavailable, str(error)))
            self.publish(record)
            return
        rejected = {"input": record.parsed.raw, "reason": reason_code(str(error))}
        self.remove(record)
        self._events.emit({"type": "rejected", "rejected": [rejected]})

    def _save_without_review(self, record: JobRecord) -> None:
        if record.job.duplicate is not None:
            self._fail(record, "save", ScmpError(ErrorKind.duplicate, "saved before"))
            return
        self.start_saving(record)

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
        """Send the job to the save worker once its audio and its cover are ready, or for a set,
        each of its tracks that's ready.

        A cover still being read or checked is waited for, so the save uses it rather than
        downloading it again, and nothing writes into staging once finalize has deleted it. A
        track of an album also waits for what the album waits for.
        """
        if record.job.kind == "set":
            for track in record.children:
                self.save_if_ready(track)
            return
        if record.job.state != JobState.saving or record.staged is None:
            return
        if any(step in record.tickets for step in ("cover", "source_cover", "save")):
            return
        if record.parent is not None and self.album_waits(record.parent):
            return
        ticket = self._new_ticket(record, "save")
        self.save_pool.submit(lambda: self._save(record, ticket))

    def _cover_settled(self, record: JobRecord) -> None:
        self.save_if_ready(record)

    def _save(self, record: JobRecord, ticket: Ticket) -> None:
        with self.lock:
            if not self._claim(record, "save", ticket):
                return
            if record.parent is not None and self.album_waits(record.parent):
                # A track put back in since this save was queued may change the numbers, so the
                # save waits again, and goes once the album stops waiting.
                self._release(record, "save", ticket)
                return
            record.saving_now = True
            run = self._save_run(record)
        outcome: SaveOutcome | CollisionQuestion
        try:
            outcome = run()
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
            self._remove_once_saved(record.parent or record)

    def _save_run(self, record: JobRecord) -> Callable[[], SaveOutcome | CollisionQuestion]:
        """The save, taken now and run on the save worker. A set's track saves with its set."""
        if record.parent is not None:
            return self._set_track_save(record)
        order = save_order(record, self.settings)
        return lambda: save(order, self._steps)

    def _remove_once_saved(self, record: JobRecord) -> None:
        """Remove a job that was removed while it placed a file, once no file is being placed."""
        placing = record.saving_now or any(track.saving_now for track in record.children)
        if record.remove_when_saved and not placing:
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
            self._step_worked(record)
            # finalize deletes the staging folder, whether it saved or skipped.
            record.staged = None
            job.staged_audio = None
            record.collision_answer = None
            job.output_path = outcome.path if outcome else None
            record.cover_error = outcome.cover_error if outcome else None
            job.state = JobState.saved if outcome is not None else JobState.skipped
        self.publish(record)

    # Bringing jobs back after a restart

    def restore(self, record: JobRecord) -> None:
        """Put a job from queue.json at the end of the queue, and start the work it waits for,
        and its tracks' if it's a set."""
        self.add_record(record)
        for job_record in (record, *record.children):
            self._resume(job_record)

    def _resume(self, record: JobRecord) -> None:
        job = record.job
        if job.state == JobState.queued:
            self._start_resolving(record)
        elif job.state == JobState.needs_review and record.prepared is not None:
            # A download the person stopped stays stopped, as it was before the restart.
            wanted = self.settings.download_while_reviewing and job.included
            if wanted and not record.download_cancelled:
                self.start_download(record)
        elif job.error is not None:
            self._schedule_auto_retry(record, job.error)

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
