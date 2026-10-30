"""The job manager: the queue's calls for the bridge. job_lifecycle.py has the work done at
startup and shutdown.

`JobFlow` runs each job's steps. Every call here takes the flow's one lock, changes what it needs
to, hands anything slow to a worker and returns at once. Answers are JSON-safe dicts, and the
error codes in them have text in web/strings. The jobs not saved yet are kept in queue.json and
come back at the next start. A call that names a set acts on the set's tracks, and the calls
that act on one track also take a set's track by its own id.
"""

import logging
from collections.abc import Iterable
from typing import Any

from scmp3.app import queue_file
from scmp3.app.events import EventBus
from scmp3.app.input_reasons import ALREADY_IN_QUEUE, reason_code
from scmp3.app.job_artists import NO_MAIN_ARTIST, needs_main_artist
from scmp3.app.job_book import queue_key
from scmp3.app.job_covers import CoverReader
from scmp3.app.job_editing import NOT_EDITABLE, UNKNOWN_JOB, JobEditing
from scmp3.app.job_flow import JobFlow, PoolMaker
from scmp3.app.job_lifecycle import QueueLifecycle
from scmp3.app.job_record import JobRecord
from scmp3.app.job_renames import (
    RENAMES_SONG,
    asks_about_playlists,
    confirm_rename,
    retry_asks_about_playlists,
)
from scmp3.app.job_set_calls import SetCalls
from scmp3.app.job_settings import SettingsFollowing
from scmp3.app.job_steps import PipelineSteps, Steps
from scmp3.app.queue_file import QueueFile
from scmp3.app.queue_keeper import QueueKeeper
from scmp3.app.set_tracks import (
    could_still_save,
    may_retry,
    places_a_file,
    tracks_of,
    waits_for_info,
)
from scmp3.app.workers import TaskPool, ThreadTimers, Timers
from scmp3.core import pipeline
from scmp3.core.errors import ErrorKind
from scmp3.core.history import History
from scmp3.core.inputs import ParsedInput, parse_inputs
from scmp3.core.library import CollisionChoice
from scmp3.core.models import JobState
from scmp3.core.settings import SettingsStore
from scmp3.core.sources import soundcloud

# States a skip leaves alone, since nothing is left to skip.
FINISHED_STATES = frozenset({JobState.saved, JobState.skipped})
# The kind of input that's queued as a set, whose tracks are listed and then resolved one by one.
SET_INPUT = "soundcloud_set"
# How many jobs a call that names many, such as a paste or Save all, handles per turn of the lock.
JOBS_PER_TURN = 100

Answer = dict[str, Any]

log = logging.getLogger(__name__)


class JobManager(JobEditing, SettingsFollowing, SetCalls, QueueLifecycle):
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
            return sum(could_still_save(record) for record in self._flow.records.values())

    # Adding

    def add_inputs(self, text: str) -> Answer:
        """Queue every supported link and audio file in pasted or dropped text. Rejected items
        come back with a code."""
        # Parsing may look at local files, so it happens before the lock is taken.
        parsed_items = parse_inputs(text)
        added: list[str] = []
        rejected: list[dict[str, str]] = []
        # A few at a time, so other calls never wait long for the lock during a paste of 2,000.
        for start in range(0, len(parsed_items), JOBS_PER_TURN):
            with self._flow.lock:
                for parsed in parsed_items[start : start + JOBS_PER_TURN]:
                    self._clear_finished_re_tag(parsed)
                    rejection = self._rejection(parsed)
                    if rejection is None:
                        added.append(self._flow.queue(parsed).id)
                    else:
                        rejected.append(rejection)
        return {"added": added, "rejected": rejected}

    def _clear_finished_re_tag(self, parsed: ParsedInput) -> None:
        # A file whose re-tag is saved or skipped can come in again for another edit, in place
        # of its finished item.
        queued = self._flow.queued_with_link(queue_key(parsed))
        if queued is None or queued.job.kind != "retag":
            return
        if queued.job.state in FINISHED_STATES:
            self._remove(queued)

    def _rejection(self, parsed: ParsedInput) -> dict[str, str] | None:
        # Core refuses sets for the command line, which saves only tracks.
        is_set = parsed.kind == SET_INPUT and parsed.url is not None
        reason = None if is_set else pipeline.unsupported_reason(parsed)
        if reason is not None:
            return {"input": parsed.raw, "reason": reason_code(reason)}
        queued = self._flow.queued_with_link(queue_key(parsed))
        if queued is not None:
            return {"input": parsed.raw, "reason": ALREADY_IN_QUEUE, "job_id": queued.id}
        return None

    # Actions

    def save(self, job_ids: Iterable[str], rename_confirmed: bool = False) -> Answer:
        """Save each job that's waiting for review, or that's a confirmed repeat of a saved one.

        A set saves each of its included tracks that's waiting for review. A track whose
        artist list has featured artists and no main one stays, and the answer says so. So does
        a re-tag that renames the song, until `rename_confirmed` says the question was answered.
        """
        ids = list(job_ids)
        held = False
        asked = False
        # A few at a time, as when adding, since Save all can name the whole queue.
        for start in range(0, len(ids), JOBS_PER_TURN):
            with self._flow.lock:
                for job_id in ids[start : start + JOBS_PER_TURN]:
                    record = self._flow.find(job_id)
                    self.forget_set_answer(record)
                    for track in tracks_of(record):
                        if not self._can_save(track):
                            if track.parent is not None and waits_for_info(track):
                                # Save on a set saves its tracks still fetching once they're in.
                                track.wants_save = True
                                self._flow.publish(track)
                        elif needs_main_artist(track.artists, track.job.overrides):
                            held = True
                        elif not rename_confirmed and asks_about_playlists(
                            track, self._flow.settings
                        ):
                            asked = True
                        else:
                            confirm_rename(track, self._flow.settings)
                            self._flow.start_saving(track)
        if asked:
            return {"error": RENAMES_SONG}
        return {"error": NO_MAIN_ARTIST} if held else {}

    def _can_save(self, record: JobRecord) -> bool:
        job = record.job
        if not job.included or record.closed:
            return False
        if job.state == JobState.needs_review:
            return True
        if job.state != JobState.error or job.error is None:
            return False
        # Saving a track that stopped as a repeat of a saved one is the user confirming it.
        return job.error.kind == ErrorKind.duplicate

    def skip(self, job_id: str) -> Answer:
        """Leave a job unsaved and delete its staged audio. A set skips each track not saved yet."""
        with self._flow.lock:
            record = self._flow.find(job_id)
            if record is None:
                return {"error": UNKNOWN_JOB}
            if record.job.state in FINISHED_STATES or places_a_file(record):
                return {}
            self._skip(record)
        return {}

    def _skip(self, record: JobRecord) -> None:
        """Skip a job, and each track of a set that isn't saved yet. A set with a saved track
        counts as saved."""
        self._flow.close(record)
        for job_record in (record, *record.children):
            if job_record.job.state not in FINISHED_STATES:
                job_record.job.state = JobState.skipped
                job_record.changed()
        if any(track.job.state == JobState.saved for track in record.children):
            record.job.state = JobState.saved
        self._flow.publish(record)

    def retry(self, job_id: str, rename_confirmed: bool = False) -> Answer:
        """Run a failed job's failed step again and carry on from there. A listed set retries
        each of its tracks that failed.

        A re-tag whose Retry would give the song a title, artist or album not confirmed yet,
        as after a settings change, answers renames_song and retries once `rename_confirmed`.
        """
        with self._flow.lock:
            record = self._flow.find(job_id)
            if record is None:
                return {"error": UNKNOWN_JOB}
            if not may_retry(record):
                return {"error": NOT_EDITABLE}
            tracks = self.tracks_to_retry(record)
            settings = self._flow.settings
            if not rename_confirmed and any(
                retry_asks_about_playlists(track, settings) for track in tracks
            ):
                return {"error": RENAMES_SONG}
            for track in tracks:
                if retry_asks_about_playlists(track, settings):
                    confirm_rename(track, settings)
                self._flow.retry(track)
        return {}

    def cancel(self, job_id: str) -> Answer:
        """Put a save waiting for its audio back in review, or stop a download. A set puts each
        of its tracks waiting to save back in review."""
        with self._flow.lock:
            record = self._flow.find(job_id)
            if record is None:
                return {"error": UNKNOWN_JOB}
            if record.job.kind == "set":
                for track in record.children:
                    if track.job.state == JobState.saving and self._flow.stop_saving(track):
                        self._flow.publish(track)
                    elif track.wants_save and track.prepared is None:
                        track.wants_save = False
                        self._flow.publish(track)
                return {}
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

    def youtube_support_ready(self) -> None:
        """Retry every job that stopped for want of YouTube support, now that it's installed."""
        with self._flow.lock:
            for record in list(self._flow.tracks()):
                error = record.job.error
                if error is not None and error.kind == ErrorKind.youtube_support_missing:
                    self._flow.retry(record)

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
        if places_a_file(record):
            # The file is being placed, so the save finishes and then the job leaves.
            record.remove_when_saved = True
            return
        self._flow.remove(record)

    def answer_collision(
        self, job_id: str, choice: CollisionChoice, rest_of_set: bool = False
    ) -> Answer:
        """Save a job whose file name was taken, with the user's answer to the question. With
        `rest_of_set`, the answer goes for every other track of its set too."""
        with self._flow.lock:
            record = self._flow.find(job_id)
            if record is None:
                return {"error": UNKNOWN_JOB}
            if record.collision is None:
                return {}
            for track in self.answered_with(record, choice, rest_of_set):
                self._answer(track, choice)
        return {}

    def _answer(self, record: JobRecord, choice: CollisionChoice) -> None:
        if choice == "skip":
            self._skip(record)
            return
        record.collision_answer = (record.collision, choice)
        self._flow.start_saving(record)
