"""What a set's tracks add up to: the set's state, its album mode, and each track's place in the
album, worked out from the tracks as they are now (TECH_SPEC §6).

The functions read job records, the live ones under the lock or a snapshot's copies outside it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from scmp3.core import pipeline
from scmp3.core.errors import ErrorKind
from scmp3.core.models import (
    AlbumMode,
    Job,
    JobState,
    Settings,
    SourceInfo,
    Tags,
    TagSuggestion,
    TitleStyle,
)
from scmp3.core.pipeline import PreparedSet
from scmp3.core.sources import soundcloud_sets

if TYPE_CHECKING:
    from scmp3.app.job_record import JobRecord

# The fields a set's own edits change, which every track of its album shares.
ALBUM_FIELDS = ("album", "album_artist", "year", "genre")
# Errors no retry can fix, so a track that stops with one is left out of its set's album. With no
# sign-in yet, a Go+ preview is one of them.
UNSAVABLE_KINDS = frozenset(
    {ErrorKind.drm, ErrorKind.preview_only, ErrorKind.unavailable, ErrorKind.geo_blocked}
)
# The states in which a track still waits for something, so its set still needs review.
WAITING_STATES = frozenset(
    {JobState.queued, JobState.resolving, JobState.needs_review, JobState.error}
)


@dataclass(frozen=True)
class TrackPlace:
    """A track's number in its album, and the fields every track of the album shares."""

    fields: Mapping[str, str]  # album_fields with the set's own edits on top, as overrides
    track_no: int
    track_total: int


def set_source(prepared_set: PreparedSet) -> SourceInfo:
    """The set as a source, which the UI reads its title, owner, link and cover from."""
    playlist = prepared_set.playlist
    return SourceInfo(
        kind="soundcloud",
        id=prepared_set.url,
        url=prepared_set.url,
        title=playlist.title,
        uploader=playlist.owner,
        release_date=playlist.release_date,
        genre=playlist.genre,
        artwork_url=playlist.artwork_url,
        playlist=playlist,
    )


def album_mode(set_job: Job) -> AlbumMode:
    """The mode the set saves in: the one picked, or else what its type suggests."""
    if set_job.album_mode is not None:
        return set_job.album_mode
    return default_mode(set_job) or "singles"


def default_mode(set_job: Job) -> AlbumMode | None:
    """What the set's type suggests, or None before the set is listed."""
    playlist = set_job.source.playlist if set_job.source else None
    return soundcloud_sets.default_mode(playlist) if playlist else None


def can_never_save(track: JobRecord) -> bool:
    """Whether the track stopped in a way no retry can fix, such as DRM."""
    error = track.job.error
    return track.job.state == JobState.error and error is not None and error.kind in UNSAVABLE_KINDS


def is_numbered(track: JobRecord) -> bool:
    """Whether the track has a number to show and save in the album: it's included and resolved."""
    return track.job.included and track.prepared is not None and track.job.suggestion is not None


def takes_a_number(track: JobRecord) -> bool:
    """Whether the track counts toward the album's numbers: it's included, and it resolved or may
    still get its info.

    A track still fetching is counted, so the numbers shown stay put as tracks come in. An album
    saves only once every track's info is in, so then these are the tracks that resolved.
    """
    return track.job.included and (is_numbered(track) or waits_for_info(track))


def waits_for_info(track: JobRecord) -> bool:
    """Whether an included track's info may still arrive, which would change the numbering."""
    if not track.job.included or track.closed or track.prepared is not None:
        return False
    return not can_never_save(track)


def saves_once_in(track: JobRecord) -> bool:
    """Whether the track saves once its info is in, since Save was pressed on its set. That
    holds while it waits to retry getting its info, too."""
    return track.wants_save and waits_for_info(track)


def album_fields(set_job: Job, tracks: Sequence[JobRecord]) -> dict[str, str]:
    """The album, album artist, year and genre every track shares, with the set's own edits."""
    playlist = set_job.source.playlist if set_job.source else None
    if playlist is None:
        return dict(set_job.overrides)
    suggestions = [_suggestion(track) for track in tracks if is_numbered(track)]
    return {**pipeline.album_fields(playlist, suggestions), **set_job.overrides}


def track_places(set_job: Job, tracks: Sequence[JobRecord]) -> list[TrackPlace | None]:
    """Each track's place in the album, or None for one without a number or a set of singles."""
    if album_mode(set_job) != "album":
        return [None] * len(tracks)
    numbers = pipeline.track_numbers([takes_a_number(track) for track in tracks])
    total = sum(number is not None for number in numbers)
    if total == 0:
        return [None] * len(tracks)
    fields = album_fields(set_job, tracks)
    return [
        TrackPlace(fields, number, total) if number is not None and is_numbered(track) else None
        for track, number in zip(tracks, numbers, strict=True)
    ]


def track_overrides(job: Job, place: TrackPlace | None) -> dict[str, str]:
    """What a track is tagged and placed with: its own edits, over the album's fields if any."""
    if place is None:
        return dict(job.overrides)
    return pipeline.album_overrides(place.fields, job.overrides)


def render_track(
    source: SourceInfo,
    suggestion: TagSuggestion,
    settings: Settings,
    *,
    style: TitleStyle | None,
    overrides: Mapping[str, str],
    place: TrackPlace | None,
) -> Tags:
    """The tags of a track, with its number in the album when it has a place in one.

    `overrides` are track_overrides'. Raises InvalidOverride as render_tags does.
    """
    if place is None:
        return pipeline.render_tags(source, suggestion, settings, style=style, overrides=overrides)
    return pipeline.render_album_track(
        source,
        suggestion,
        settings,
        track_no=place.track_no,
        track_total=place.track_total,
        style=style,
        overrides=overrides,
    )


def set_state(tracks: Sequence[JobRecord]) -> JobState:
    """A listed set's state, from its tracks that can still be saved.

    It's saving while any track saves, and needs review while any waits for info, for review or
    for a retry. Once none does, it's saved if any track was.
    """
    active = [track for track in tracks if track.job.included and not can_never_save(track)]
    states = {track.job.state for track in active}
    if JobState.saving in states or any(saves_once_in(track) for track in active):
        return JobState.saving
    if states & WAITING_STATES:
        return JobState.needs_review
    if any(track.job.state == JobState.saved for track in tracks):
        return JobState.saved
    # Every track left was skipped, or else none can be saved until one is put back in.
    return JobState.skipped if active else JobState.needs_review


def album_is_settled(tracks: Sequence[JobRecord]) -> bool:
    """Whether a track of the set is saved or placing its file, which fixes the album's numbers."""
    return any(track.saving_now or track.job.state == JobState.saved for track in tracks)


def numbers_are_fixed(record: JobRecord) -> bool:
    """Whether a set's album numbers can no longer change: it's an album, and a track is saved.

    Singles have no numbers, so saving some of them fixes nothing.
    """
    return album_mode(record.job) == "album" and album_is_settled(record.children)


def may_retry(track: JobRecord) -> bool:
    """Whether a track may be retried. One that can never save stays out of an album whose
    numbers are fixed, since getting its info now would move the numbers of tracks not saved."""
    parent = track.parent
    if parent is None or not can_never_save(track):
        return True
    return not numbers_are_fixed(parent)


def set_is_editable(record: JobRecord) -> bool:
    """Whether a set's mode, album fields and cover can still change: it's listed and in review,
    so no track of it saves, and none has saved."""
    if record.prepared_set is None or record.closed:
        return False
    return record.job.state == JobState.needs_review and not album_is_settled(record.children)


def could_still_save(record: JobRecord) -> bool:
    """Whether the job is still on its way to a save, or failed in a way Retry can fix. A set
    could while one of its included tracks could."""
    job = record.job
    if job.state in (JobState.saved, JobState.skipped):
        return False
    if record.prepared_set is not None:
        return any(could_still_save(track) for track in record.children if track.job.included)
    return job.error is None or job.error.kind not in UNSAVABLE_KINDS


def tracks_of(record: JobRecord | None) -> tuple[JobRecord, ...]:
    """The tracks a call on this job acts on: a set's tracks, or the job itself."""
    if record is None:
        return ()
    # A set that isn't listed yet is retried itself.
    return record.children if record.prepared_set is not None else (record,)


def places_a_file(record: JobRecord) -> bool:
    """Whether a save is placing a file for the job, or for a track of the set, right now."""
    return record.saving_now or any(track.saving_now for track in record.children)


def _suggestion(track: JobRecord) -> TagSuggestion:
    suggestion = track.job.suggestion
    assert suggestion is not None
    return suggestion
