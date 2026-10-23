"""The job manager: the queue's calls for the bridge, plus the work done at startup and shutdown.

`JobFlow` runs each job's steps. Every call here takes the flow's one lock, changes what it needs
to, hands anything slow to a worker and returns at once. Answers are JSON-safe dicts, and the
error codes in them have text in web/strings. The jobs not saved yet are kept in queue.json and
come back at the next start.
"""

import logging
import time
from collections.abc import Iterable
from typing import Any

from scmp3.app import queue_file
from scmp3.app.events import EventBus
from scmp3.app.input_reasons import ALREADY_IN_QUEUE, reason_code
from scmp3.app.job_covers import CoverReader
from scmp3.app.job_editing import UNKNOWN_JOB, JobEditing
from scmp3.app.job_flow import JobFlow, PoolMaker
from scmp3.app.job_record import JobRecord
from scmp3.app.job_settings import SettingsFollowing
from scmp3.app.job_steps import PipelineSteps, Steps
from scmp3.app.queue_file import QueueFile
from scmp3.app.queue_keeper import QueueKeeper
from scmp3.app.workers import TaskPool, ThreadTimers, Timers
from scmp3.core import download, pipeline, placing
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.inputs import ParsedInput, parse_inputs
from scmp3.core.library import CollisionChoice
from scmp3.core.models import JobState
from scmp3.core.settings import SettingsStore
from scmp3.core.sources import soundcloud

# States a skip leaves alone, since nothing is left to skip.
FINISHED_STATES = frozenset({JobState.saved, JobState.skipped})
# Errors no retry can fix, so closing loses nothing a save could still make. With no sign-in in
# v0.1, a Go+ preview is one of them.
UNSAVABLE_KINDS = frozenset(
    {ErrorKind.drm, ErrorKind.preview_only, ErrorKind.unavailable, ErrorKind.geo_blocked}
)
# How long closing the window waits for a save that's placing its file, in seconds.
SHUTDOWN_SAVE_WAIT = 5.0
# How many jobs a call that names many, such as a paste or Save all, handles per turn of the lock.
JOBS_PER_TURN = 100

Answer = dict[str, Any]

log = logging.getLogger(__name__)


class JobManager(JobEditing, SettingsFollowing):
    """The queue. Bridge calls go through here, and so does every change the UI hears about."""

    def __init__(
        self,
        settings: SettingsStore,
        events: EventBus,
        *,
        steps: Steps,
        covers: CoverReader,
        make_pool: PoolMaker = TaskPool,
        timers: Timers | None = None,
        queue: QueueFile | None = None,
    ) -> None:
        """`queue` keeps the queue across restarts. Without it, the queue lasts one run."""
        self._flow = JobFlow(
            settings.load(),
            events,
            steps=steps,
            covers=covers,
            make_pool=make_pool,
            timers=timers or ThreadTimers(),
        )
        self._keeper = QueueKeeper(queue, self._flow, steps) if queue else None

    @classmethod
    def for_app(cls, settings: SettingsStore, history: History, events: EventBus) -> "JobManager":
        """The job manager the app runs, on the real pipeline and yt-dlp, kept in queue.json."""
        tools = pipeline.Tools(make_ydl=soundcloud.make_ydl, history=history)
        return cls(
            settings,
            events,
            steps=PipelineSteps(tools),
            covers=CoverReader(soundcloud.make_ydl),
            queue=QueueFile(queue_file.default_path()),
        )

    def views(self) -> list[dict[str, Any]]:
        """Every job as the UI gets it, in queue order. The dicts are built outside the lock."""
        with self._flow.lock:
            snapshots = [record.snapshot() for record in self._flow.records.values()]
            settings = self._flow.settings
        return [snapshot.view(settings) for snapshot in snapshots]

    def is_queued(self, url: str) -> bool:
        """Whether a job for this normalized link is in the queue already."""
        with self._flow.lock:
            return self._flow.queued_with_link(url) is not None

    def unsaved_count(self) -> int:
        """How many jobs in the queue could still be saved, which closing would lose."""
        with self._flow.lock:
            return sum(_could_still_save(record) for record in self._flow.records.values())

    # Adding

    def add_inputs(self, text: str) -> Answer:
        """Queue every supported link in pasted text. Rejected items come back with a code."""
        # Parsing may look at local files, so it happens before the lock is taken.
        parsed_items = parse_inputs(text)
        added: list[str] = []
        rejected: list[dict[str, str]] = []
        # A few at a time, so other calls never wait long for the lock during a paste of 2,000.
        for start in range(0, len(parsed_items), JOBS_PER_TURN):
            with self._flow.lock:
                for parsed in parsed_items[start : start + JOBS_PER_TURN]:
                    rejection = self._rejection(parsed)
                    if rejection is None:
                        added.append(self._flow.queue(parsed).id)
                    else:
                        rejected.append(rejection)
        return {"added": added, "rejected": rejected}

    def _rejection(self, parsed: ParsedInput) -> dict[str, str] | None:
        reason = pipeline.unsupported_reason(parsed)
        if reason is not None:
            return {"input": parsed.raw, "reason": reason_code(reason)}
        queued = self._flow.queued_with_link(parsed.url)
        if queued is not None:
            return {"input": parsed.raw, "reason": ALREADY_IN_QUEUE, "job_id": queued.id}
        return None

    # Actions

    def save(self, job_ids: Iterable[str]) -> Answer:
        """Save each job that's waiting for review, or that's a confirmed repeat of a saved one."""
        ids = list(job_ids)
        # A few at a time, as when adding, since Save all can name the whole queue.
        for start in range(0, len(ids), JOBS_PER_TURN):
            with self._flow.lock:
                for job_id in ids[start : start + JOBS_PER_TURN]:
                    record = self._flow.records.get(job_id)
                    if record is not None and self._can_save(record):
                        self._flow.start_saving(record)
        return {}

    def _can_save(self, record: JobRecord) -> bool:
        job = record.job
        if job.state == JobState.needs_review:
            return True
        if job.state != JobState.error or job.error is None:
            return False
        # Saving a track that stopped as a repeat of a saved one is the user confirming it.
        return job.error.kind == ErrorKind.duplicate

    def skip(self, job_id: str) -> Answer:
        """Leave a job unsaved and delete its staged audio."""
        with self._flow.lock:
            record = self._flow.records.get(job_id)
            if record is None:
                return {"error": UNKNOWN_JOB}
            if record.job.state in FINISHED_STATES or record.saving_now:
                return {}
            self._skip(record)
        return {}

    def _skip(self, record: JobRecord) -> None:
        self._flow.close(record)
        record.job.state = JobState.skipped
        self._flow.publish(record)

    def retry(self, job_id: str) -> Answer:
        """Run a failed job's failed step again and carry on from there."""
        with self._flow.lock:
            record = self._flow.records.get(job_id)
            if record is None:
                return {"error": UNKNOWN_JOB}
            if record.job.state == JobState.error:
                self._flow.retry(record)
        return {}

    def cancel(self, job_id: str) -> Answer:
        """Put a save waiting for its audio back in review, or stop a download."""
        with self._flow.lock:
            record = self._flow.records.get(job_id)
            if record is None:
                return {"error": UNKNOWN_JOB}
            state = record.job.state
            # The download carries on, so the audio is ready when the job is saved again.
            if state == JobState.saving and self._flow.stop_saving(record):
                self._flow.publish(record)
            elif state == JobState.needs_review and "download" in record.tickets:
                self._flow.cancel_download(record)
                record.download_cancelled = True
                self._flow.publish(record)
        return {}

    def remove(self, job_id: str) -> Answer:
        """Take a job out of the queue, stopping its work and deleting its staged audio."""
        with self._flow.lock:
            record = self._flow.records.get(job_id)
            if record is None:
                return {"error": UNKNOWN_JOB}
            self._remove(record)
        return {}

    def remove_jobs(self, job_ids: Iterable[str]) -> Answer:
        """Take many jobs out of the queue in one call, as Clear finished does. Unknown ids are
        left out, since a job may have left meanwhile."""
        ids = list(job_ids)
        for start in range(0, len(ids), JOBS_PER_TURN):
            with self._flow.lock:
                for job_id in ids[start : start + JOBS_PER_TURN]:
                    record = self._flow.records.get(job_id)
                    if record is not None:
                        self._remove(record)
        return {}

    def _remove(self, record: JobRecord) -> None:
        if record.saving_now:
            # The file is being placed, so the save finishes and then the job leaves.
            record.remove_when_saved = True
            return
        self._flow.remove(record)

    def answer_collision(self, job_id: str, choice: CollisionChoice) -> Answer:
        """Save a job whose file name was taken, with the user's answer to the question."""
        with self._flow.lock:
            record = self._flow.records.get(job_id)
            if record is None:
                return {"error": UNKNOWN_JOB}
            if record.collision is None:
                return {}
            if choice == "skip":
                self._skip(record)
                return {}
            record.collision_answer = (record.collision, choice)
            self._flow.start_saving(record)
        return {}

    # Starting and stopping

    def start_up(self) -> None:
        """Start the pools' threads, bring back the queue, and clear what the last run left behind.

        The app calls this before its window opens, so no bridge call waits for a thread to start,
        and the window's first snapshot has the queue.
        """
        for pool in self._flow.pools():
            pool.start_threads()
        if self._keeper is not None:
            self._keeper.restore()
            self._keeper.start()
        # After the restore, so the staging folders of the jobs it brought back are kept.
        self._flow.misc_pool.submit(self._clean_up_last_run)

    def keep_queue(self) -> bool:
        """Write queue.json now. Returns whether the queue will come back at the next start."""
        return self._keeper is not None and self._keeper.write_now()

    def _clean_up_last_run(self) -> None:
        with self._flow.lock:
            keep = set(self._flow.records)
            library_dir = self._flow.settings.library_dir
        try:
            download.purge_old(keep=keep)
            placing.remove_leftovers(library_dir)
        except ScmpError as error:
            log.warning("Couldn't clear what the last run left behind: %s", error)
        except OSError as error:
            # The OS's message names the folder, so only its reason goes above DEBUG.
            log.warning("Couldn't clear what the last run left behind: %s", error.strerror or error)
            log.debug("Clearing what the last run left behind failed", exc_info=error)

    def shut_down(self) -> None:
        """Stop downloads and timers as the window closes, and keep the queue in queue.json.

        The staging folders of jobs not saved yet stay, so their audio comes back with them.
        """
        flow = self._flow
        with flow.lock:
            for record in flow.records.values():
                for ticket in record.tickets.values():
                    ticket.cancel.set()
                if record.retry_timer is not None:
                    record.retry_timer.cancel()
        for pool in flow.pools():
            pool.close()
        # A save placing its file gets to finish, so its history entry is written too.
        if not flow.save_pool.wait_until_idle(SHUTDOWN_SAVE_WAIT):
            log.warning("A save was still running when sc-mp3 closed")
        # Last, so queue.json leaves out the saves that just finished.
        if self._keeper is not None:
            self._keeper.close()

    def wait_until_idle(self, timeout: float) -> bool:
        """Wait until no task is waiting or running in any pool, as tests and shutdown need."""
        deadline = time.monotonic() + timeout
        pools = self._flow.pools()
        while time.monotonic() < deadline:
            for pool in pools:
                pool.wait_until_idle(max(0.0, deadline - time.monotonic()))
            # A task can queue another in a pool already waited on, so all are checked again.
            if all(pool.wait_until_idle(0) for pool in pools):
                return True
        return False


def _could_still_save(record: JobRecord) -> bool:
    """Whether the job is still on its way to a save, or failed in a way Retry can fix."""
    job = record.job
    if job.state in FINISHED_STATES:
        return False
    return job.error is None or job.error.kind not in UNSAVABLE_KINDS
