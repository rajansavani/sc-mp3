"""The job manager's calls that move a job on after it's added: skip, retry, cancel, the answer
about a taken file name, and the retries once YouTube support or the sign-in is back.

A call that names a set acts on its tracks, as the job manager's other calls do.
"""

from typing import Any

from scmp3.app.job_editing import NOT_EDITABLE, UNKNOWN_JOB
from scmp3.app.job_record import JobRecord
from scmp3.app.job_renames import RENAMES_SONG, confirm_rename, retry_asks_about_playlists
from scmp3.app.job_set_calls import SetCalls
from scmp3.app.set_tracks import may_retry, places_a_file
from scmp3.core.errors import ErrorKind
from scmp3.core.library import CollisionChoice
from scmp3.core.models import JobState

# States a skip leaves alone, since nothing is left to skip.
FINISHED_STATES = frozenset({JobState.saved, JobState.skipped})

Answer = dict[str, Any]


class JobActions(SetCalls):
    """Skip, retry, cancel and answer, on the job manager's flow. SetCalls says which of a set's
    tracks a retry or an answer goes for."""

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

    def youtube_support_ready(self) -> None:
        """Retry every job that stopped for want of YouTube support, now that it's installed."""
        self._retry_stopped_by(ErrorKind.youtube_support_missing)

    def sign_in_ready(self) -> None:
        """Retry every job that stopped because the SoundCloud sign-in ended, now that it's back."""
        self._retry_stopped_by(ErrorKind.auth_expired)

    def _retry_stopped_by(self, kind: ErrorKind) -> None:
        """Retry each failed job, set or set's track that stopped with this kind of error."""
        with self._flow.lock:
            # Sets are included, since listing one can stop for the sign-in too.
            for record in self._flow.every_record():
                error = record.job.error
                failed = record.job.state == JobState.error and not record.closed
                if failed and error is not None and error.kind == kind:
                    self._flow.retry(record)
