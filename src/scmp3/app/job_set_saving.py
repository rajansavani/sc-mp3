"""One save of a set's track, as the save worker runs it: in its album, with its number and the
cover the album shares, or as a single.

As in job_saving.py, a taken name under the "ask" policy comes back as a question. The saving
itself is finalize_set_track's (TECH_SPEC §6). The album's cover is loaded by the first track
saved with it, and the set keeps it for the others.
"""

from dataclasses import dataclass, replace

from scmp3.app.job_record import JobRecord
from scmp3.app.job_saving import (
    CollisionQuestion,
    SaveOrder,
    answered_policy,
    cover_to_save,
    keep_both,
    save_order,
)
from scmp3.app.job_steps import Steps
from scmp3.app.set_tracks import TrackPlace, render_track
from scmp3.core import pipeline
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.library import CollisionChoice, reuse_folders_by_case
from scmp3.core.models import ArtworkChoice, JobState, Settings
from scmp3.core.pipeline import AlbumPlace, LoadedCover, PreparedSet, Saved, SetTrack

AlbumCover = tuple[ArtworkChoice, LoadedCover]


@dataclass(frozen=True)
class SetTrackOrder:
    """Everything one save of a set's track needs, taken under the lock."""

    track: SaveOrder
    place: TrackPlace | None  # None for a single
    prepared_set: PreparedSet
    # The set's tracks and its own edits, which album_places loads the album's cover with.
    tracks: tuple[SetTrack, ...]
    album_edits: dict[str, str]
    cover_choice: ArtworkChoice
    loaded_cover: LoadedCover | None  # the album's cover, when an earlier save loaded it
    # What the person answered for every taken name in the set, used where the setting asks.
    set_choice: CollisionChoice | None


@dataclass(frozen=True)
class SetTrackSaved:
    """What the save did, and the album's cover when this save loaded it."""

    outcome: Saved | CollisionQuestion | None
    album_cover: AlbumCover | None


def set_track_order(
    record: JobRecord, settings: Settings, place: TrackPlace | None
) -> SetTrackOrder:
    """What the set's track saves with, as it and its set are now. It must have its audio."""
    parent = record.parent
    if parent is None or parent.prepared_set is None:
        raise ValueError("only a track of a listed set is saved this way")
    choice = cover_to_save(parent)
    kept = parent.album_cover
    return SetTrackOrder(
        track=save_order(record, settings),
        place=place,
        prepared_set=parent.prepared_set,
        tracks=tuple(_set_track(track) for track in parent.children),
        album_edits=dict(parent.job.overrides),
        cover_choice=choice,
        loaded_cover=kept[1] if kept is not None and kept[0] == choice else None,
        set_choice=parent.collision_choice,
    )


def save_set_track(order: SetTrackOrder, steps: Steps) -> SetTrackSaved:
    """Save the track, or ask first when its name is taken. An outcome of None means the policy
    said to skip. Raises ScmpError, or InvalidOverride for hand edits that can't be rendered."""
    place, loaded = _album_place(order, steps)
    track = order.track
    source = track.prepared.source
    overrides = track.overrides
    if order.place is not None:
        overrides = pipeline.album_overrides(order.place.fields, track.overrides)
    tags = render_track(
        source,
        track.suggestion,
        track.settings,
        style=track.style,
        overrides=overrides,
        place=order.place,
    )
    planned = pipeline.destination(
        source,
        track.suggestion,
        track.staged,
        tags,
        track.settings,
        style=track.style,
        overrides=overrides,
        in_album_set=order.place is not None,
    )
    planned = reuse_folders_by_case(planned, track.settings.library_dir, warn=False)
    policy = answered_policy(track, planned)
    if policy == "ask" and order.set_choice is not None:
        policy = order.set_choice
    if policy == "ask" and planned.exists():
        return SetTrackSaved(CollisionQuestion(planned), loaded)
    set_track = SetTrack(
        track.prepared,
        staged=track.staged,
        suggestion=track.suggestion,
        style=track.style,
        overrides=dict(track.overrides),
        cover=track.artwork,
    )
    settings = replace(track.settings, when_file_exists=policy)
    result = steps.finalize_set_track(set_track, place, settings, ask=keep_both)
    if result.outcome == "failed":
        raise result.error or ScmpError(ErrorKind.internal, "a set's track failed without a reason")
    return SetTrackSaved(result.saved if result.outcome == "saved" else None, loaded)


def _album_place(order: SetTrackOrder, steps: Steps) -> tuple[AlbumPlace | None, AlbumCover | None]:
    """The track's place in its album with the cover loaded, and that cover when it's new."""
    place = order.place
    if place is None:
        return None, None
    cover = order.loaded_cover
    loaded: AlbumCover | None = None
    if cover is None:
        places = steps.album_places(
            order.prepared_set,
            order.tracks,
            order.track.settings,
            album=order.album_edits,
            cover=order.cover_choice,
        )
        cover = next((found.cover for found in places if found is not None), LoadedCover(None))
        # A cover that didn't load is tried again by the next track, in case it works by then.
        if cover.cover is not None:
            loaded = (order.cover_choice, cover)
    return AlbumPlace(place.fields, place.track_no, place.track_total, cover), loaded


def _set_track(record: JobRecord) -> SetTrack:
    """A track of the set as album_places reads it: whether it's numbered, and if it's saved.

    A track left out is passed without its info, so the album's cover never comes from it, the
    same as the set's preview.
    """
    job = record.job
    saved = job.output_path if job.state == JobState.saved else None
    return SetTrack(
        record.prepared if job.included else None,
        included=job.included,
        suggestion=job.suggestion,
        overrides=dict(job.overrides),
        saved=Saved(saved) if saved is not None else None,
    )
