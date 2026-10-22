"""Saving a set: its included tracks in order, as singles or as one album, going on past a track
that fails. Each track is saved the way saving saves one, and finalize_set_track saves one alone,
as the app's save worker does."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.library import AskFn
from scmp3.core.models import (
    AlbumMode,
    ArtworkChoice,
    Settings,
    SourceInfo,
    Tags,
    TagSuggestion,
    TitleStyle,
)
from scmp3.core.pipeline.albums import (
    album_cover_source,
    album_fields,
    album_overrides,
    render_album_track,
    track_numbers,
)
from scmp3.core.pipeline.common import (
    Prepared,
    PreparedSet,
    Saved,
    SetTrack,
    TrackOutcome,
    TrackResult,
    only_scmp_errors,
)
from scmp3.core.pipeline.downloading import fetch
from scmp3.core.pipeline.saving import LoadedCover, finalize, finalize_with_cover, try_load_cover
from scmp3.core.pipeline.tags import render_tags
from scmp3.core.suggest import InvalidOverride

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlbumPlace:
    """A track's place in an album being saved, and what it shares with the other tracks."""

    fields: Mapping[str, str]  # album_fields with the person's edits on top, as overrides
    track_no: int
    track_total: int
    cover: LoadedCover


def finalize_set(
    prepared_set: PreparedSet,
    tracks: Sequence[SetTrack],
    settings: Settings,
    *,
    mode: AlbumMode | None = None,
    album: Mapping[str, str] | None = None,
    cover: ArtworkChoice | None = None,
    ask: AskFn | None = None,
) -> list[TrackResult]:
    """Save a set's included tracks in order with finalize_set_track, going on past any that fail.

    Returns one result for each of `tracks`, which are the set's tracks in order. `mode` defaults
    to the set's own. In album mode, album_places numbers the tracks and shares `album` and
    `cover` among them. Saving the same tracks again saves only those that weren't saved yet, with
    the numbers they had.
    """
    places: list[AlbumPlace | None] = [None] * len(tracks)
    if (mode or prepared_set.default_mode) == "album":
        places = album_places(prepared_set, tracks, settings, album=album, cover=cover)
    return [
        finalize_set_track(track, place, settings, ask=ask)
        for track, place in zip(tracks, places, strict=True)
    ]


def album_places(
    prepared_set: PreparedSet,
    tracks: Sequence[SetTrack],
    settings: Settings,
    *,
    album: Mapping[str, str] | None = None,
    cover: ArtworkChoice | None = None,
) -> list[AlbumPlace | None]:
    """Each track's place in the album, or None for one that isn't saved. Loads the shared cover.

    `album` holds the person's edits to the album fields, which go over album_fields, and `cover`
    replaces the set's own cover. Tracks are numbered among the included ones that resolved, the
    ones already saved too. The cover is loaded once, unless every track is already saved, and an
    internal error loading it is raised.
    """
    suggestions = [
        track.suggestion or track.prepared.suggestion
        for track in tracks
        if track.prepared is not None and track.included
    ]
    if not suggestions:
        return [None] * len(tracks)
    fields = {**album_fields(prepared_set.playlist, suggestions), **(album or {})}
    shared = _shared_cover(prepared_set, tracks, settings, cover)
    numbers = track_numbers([track.to_save for track in tracks])
    return [
        None if number is None else AlbumPlace(fields, number, len(suggestions), shared)
        for number in numbers
    ]


def finalize_set_track(
    track: SetTrack, place: AlbumPlace | None, settings: Settings, *, ask: AskFn | None = None
) -> TrackResult:
    """Save one track of a set, in its album at `place`, or as a single when `place` is None.

    Gives what went wrong as the result instead of raising it, and records the save on the track.
    A track saved before is left as it is. One that isn't staged yet is downloaded first, and its
    audio stays on the track if the save then fails; clear `staged` to download it again.
    Whatever `ask` raises reaches the caller unchanged.
    """
    if not track.included:
        return _result("left_out", place)
    if track.saved is not None:
        return _result("saved", place, saved=track.saved)
    if track.prepared is None:
        unresolved = ScmpError(ErrorKind.internal, "the track was never resolved")
        return _result("failed", place, error=track.error or unresolved)
    try:
        saved = _save_track(track.prepared, track, place, settings, ask)
    except ScmpError as error:
        log.warning("Couldn't save a track of the set: %s", error)
        return _result("failed", place, error=error)
    if saved is None:
        return _result("name_taken", place)
    return _result("saved", place, saved=saved)


def _shared_cover(
    prepared_set: PreparedSet,
    tracks: Sequence[SetTrack],
    settings: Settings,
    cover: ArtworkChoice | None,
) -> LoadedCover:
    """The album's cover, loaded once, or none when every track is already saved."""
    if all(track.saved is not None for track in tracks if track.to_save):
        return LoadedCover(None)
    first = next(track.prepared.source for track in tracks if track.prepared is not None)
    owner = album_cover_source(prepared_set.playlist, first)
    with only_scmp_errors(where="data"):
        return try_load_cover(prepared_set.tools, cover or ArtworkChoice(), owner, settings)


def _save_track(
    prepared: Prepared,
    track: SetTrack,
    place: AlbumPlace | None,
    settings: Settings,
    ask: AskFn | None,
) -> Saved | None:
    """Tag and save one track, as a single or in its album, downloading it first if need be."""
    suggestion = track.suggestion or prepared.suggestion
    overrides = track.overrides if place is None else album_overrides(place.fields, track.overrides)
    tags = _set_track_tags(prepared.source, suggestion, track.style, overrides, place, settings)
    if track.staged is None:
        # Kept on the track, so a retry after a failed save doesn't download it again.
        track.staged = fetch(prepared)
    if place is None:
        saved = finalize(
            prepared,
            track.staged,
            tags,
            track.cover,
            settings,
            style=track.style,
            suggestion=suggestion,
            overrides=overrides,
            ask=ask,
        )
    else:
        saved = finalize_with_cover(
            prepared,
            track.staged,
            tags,
            place.cover,
            settings,
            style=track.style,
            suggestion=suggestion,
            overrides=overrides,
            ask=ask,
            in_album_set=True,
        )
    # The staging folder is gone either way: the audio is in the library, or the name was skipped.
    track.staged = None
    track.saved = saved
    return saved


def _set_track_tags(
    source: SourceInfo,
    suggestion: TagSuggestion,
    style: TitleStyle | None,
    overrides: Mapping[str, str],
    place: AlbumPlace | None,
    settings: Settings,
) -> Tags:
    """The tags of one track of a set, as a single or with its album's number."""
    try:
        if place is None:
            return render_tags(source, suggestion, settings, style=style, overrides=overrides)
        return render_album_track(
            source,
            suggestion,
            settings,
            track_no=place.track_no,
            track_total=place.track_total,
            style=style,
            overrides=overrides,
        )
    except InvalidOverride as error:
        # Hand edits are checked as they're typed, so one that can't be rendered here is a bug.
        detail = f"the track's hand edits can't be rendered: {error}"
        raise ScmpError(ErrorKind.internal, detail) from error


def _result(
    outcome: TrackOutcome,
    place: AlbumPlace | None,
    *,
    saved: Saved | None = None,
    error: ScmpError | None = None,
) -> TrackResult:
    """A track's result, with its number when it has a place in the album."""
    if place is None:
        return TrackResult(outcome, saved=saved, error=error)
    return TrackResult(
        outcome,
        saved=saved,
        error=error,
        track_no=place.track_no,
        track_total=place.track_total,
    )
