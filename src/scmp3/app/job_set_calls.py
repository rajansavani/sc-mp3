"""The job manager's calls for sets: the album mode and which tracks are saved.

The other calls take a set too, and act on its tracks, as the job manager's own methods say.
An album's mode, fields, cover and tracks are fixed once one of its tracks saves.
"""

from typing import Any

from scmp3.app.job_editing import NOT_EDITABLE, UNKNOWN_JOB
from scmp3.app.job_flow import JobFlow
from scmp3.app.job_record import JobRecord
from scmp3.app.set_tracks import can_never_save, numbers_are_fixed, set_is_editable
from scmp3.core.library import CollisionChoice
from scmp3.core.models import AlbumMode, JobState

Answer = dict[str, Any]


class SetCalls:
    """The set calls, on the job manager's flow."""

    _flow: JobFlow

    def set_album_mode(self, job_id: str, mode: AlbumMode) -> Answer:
        """Save the set as one album, with shared fields and numbers, or as singles."""
        with self._flow.lock:
            record = self._flow.records.get(job_id)
            if record is None or record.job.kind != "set":
                return {"error": UNKNOWN_JOB}
            if not set_is_editable(record):
                return {"error": NOT_EDITABLE}
            if record.job.album_mode != mode:
                record.job.album_mode = mode
                self.album_changed(record)
        return {}

    def set_included(self, job_id: str, child_id: str, included: bool) -> Answer:
        """Choose whether one track of the set is saved. In an album, the others are renumbered."""
        with self._flow.lock:
            record = self._flow.records.get(job_id)
            track = _track_in(record, child_id)
            if record is None or track is None:
                return {"error": UNKNOWN_JOB}
            fixed = numbers_are_fixed(record)
            if record.prepared_set is None or record.closed or fixed or _is_saving(track):
                return {"error": NOT_EDITABLE}
            if track.job.included != included:
                track.job.included = included
                track.wants_save = track.wants_save and included
                track.changed()
                self._follow_inclusion(track)
                self.album_changed(record)
                # An album's saves may have waited for this track's info.
                self._flow.save_if_ready(record)
        return {}

    def tracks_to_retry(self, record: JobRecord) -> list[JobRecord]:
        """The failed tracks Retry runs again: the job itself, or a set's tracks that failed in a
        way a retry can fix."""
        if record.prepared_set is None:
            tracks = [record]
        else:
            tracks = [track for track in record.children if not can_never_save(track)]
        return [track for track in tracks if track.job.state == JobState.error and not track.closed]

    def answered_with(
        self, record: JobRecord, choice: CollisionChoice, rest_of_set: bool
    ) -> list[JobRecord]:
        """The tracks an answer about a taken name goes for: this one, and with `rest_of_set`,
        every track of its set asked now. The set keeps the answer for the ones asked later."""
        parent = record.parent
        if not rest_of_set or parent is None:
            return [record]
        parent.collision_choice = choice
        others = [track for track in parent.children if track.collision and track is not record]
        return [record, *others]

    def forget_set_answer(self, record: JobRecord | None) -> None:
        """Drop a set's answer for every taken name, so it covers only the Save it was given in,
        and a track saved later is asked about its own file."""
        owner = record.parent if record is not None and record.parent else record
        if owner is not None:
            owner.collision_choice = None

    def album_changed(self, record: JobRecord) -> None:
        """Every track's tags and name may have changed, so name questions no longer apply."""
        for track in record.children:
            track.forget_name_question()
        self._flow.publish(record)

    def _follow_inclusion(self, track: JobRecord) -> None:
        """Download a track that's put back in, as the setting says, and stop one left out."""
        flow = self._flow
        if not track.job.included:
            flow.cancel_download(track)
            return
        wanted = flow.settings.download_while_reviewing and not track.download_cancelled
        if wanted and track.job.state == JobState.needs_review and track.prepared is not None:
            flow.start_download(track)


def _track_in(record: JobRecord | None, child_id: str) -> JobRecord | None:
    if record is None:
        return None
    return next((track for track in record.children if track.id == child_id), None)


def _is_saving(track: JobRecord) -> bool:
    return track.saving_now or track.job.state in (JobState.saving, JobState.saved)
