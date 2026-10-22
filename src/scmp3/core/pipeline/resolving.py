"""The pipeline's first stage: resolve an input, refuse a track that can't be saved, look it up in
the history and suggest its tags. A set is listed first, then each of its tracks resolves alone."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.inputs import ParsedInput, classify
from scmp3.core.models import HistoryEntry, PlaylistEntry, Settings, SourceInfo
from scmp3.core.pipeline.common import Prepared, PreparedSet, SetTrack, Tools, only_scmp_errors
from scmp3.core.sources import soundcloud, soundcloud_sets
from scmp3.core.sources.soundcloud_formats import AVAILABILITY_KINDS
from scmp3.core.suggest import suggest
from scmp3.core.urls import NOT_SUPPORTED, UnsupportedInput

if TYPE_CHECKING:
    from yt_dlp import YoutubeDL

# Inputs a later version saves, with the reason given until then.
SETS_NOT_SUPPORTED = "SoundCloud sets and albums can't be saved yet. Add each track's own link."
YOUTUBE_NOT_SUPPORTED = "YouTube links can't be saved yet."
FILES_NOT_SUPPORTED = "Fixing the tags of files you already have isn't possible yet."
NOT_YET: dict[str, str] = {
    "soundcloud_set": SETS_NOT_SUPPORTED,  # T-051
    "youtube_video": YOUTUBE_NOT_SUPPORTED,  # T-061
    "local_file": FILES_NOT_SUPPORTED,  # T-062
}

log = logging.getLogger(__name__)

Resolver = Callable[[str, "YoutubeDL", str | None], soundcloud.ResolvedTrack]


def _resolve_soundcloud_short(
    url: str, ydl: YoutubeDL, token: str | None
) -> soundcloud.ResolvedTrack:
    """Follow a short link, then resolve the track it leads to."""
    kind, target = soundcloud.follow_short_link(url, ydl)
    if kind != "soundcloud_track":
        raise UnsupportedInput(NOT_YET.get(kind, NOT_SUPPORTED))
    return soundcloud.resolve_track(target, ydl, token)


# How each kind of input resolves. A new source adds its resolver here.
RESOLVERS: dict[str, Resolver] = {
    "soundcloud_track": soundcloud.resolve_track,
    "soundcloud_short": _resolve_soundcloud_short,
}


def unsupported_reason(parsed: ParsedInput) -> str | None:
    """Why `prepare` can't take this input, for the user, or None when it can."""
    if parsed.kind in RESOLVERS and parsed.url:
        return None
    return parsed.reason or NOT_YET.get(parsed.kind) or NOT_SUPPORTED


def prepare(
    parsed: ParsedInput,
    *,
    settings: Settings,
    token: str | None = None,
    tools: Tools | None = None,
    job_id: str | None = None,
) -> Prepared:
    """Resolve an input, refuse tracks that can't be saved, look it up in history and suggest tags.

    Raises ScmpError, or UnsupportedInput with the reason when the input is something sc-mp3 can't
    save yet, such as a short link that leads to a set. `Prepared.duplicate` is set, rather than
    an error raised, when the track was saved before.
    """
    reason = unsupported_reason(parsed)
    if reason is not None:
        raise UnsupportedInput(reason)
    tools = tools or Tools()
    with only_scmp_errors(where="data"):
        with tools.make_ydl(token) as ydl:
            resolved = RESOLVERS[parsed.kind](str(parsed.url), ydl, token)
        source = resolved.source
        _check_availability(source)
        if resolved.info is None:
            raise ScmpError(ErrorKind.engine_outdated, "yt-dlp gave no info for the track")
        return Prepared(
            job_id=job_id or uuid.uuid4().hex,
            source=source,
            suggestion=suggest(source, settings),
            duplicate=find_duplicate(tools.history, source),
            settings=settings,
            info=resolved.info,
            token=token,
            tools=tools,
        )


def prepare_set(
    parsed: ParsedInput,
    *,
    settings: Settings,
    token: str | None = None,
    tools: Tools | None = None,
) -> PreparedSet:
    """List a set's tracks and pick its default mode. Each track resolves with prepare_set_track.

    Takes a SoundCloud set link; the caller sends tracks to prepare. Raises ScmpError when the set
    can't be read or holds no tracks.
    """
    if parsed.kind != "soundcloud_set" or not parsed.url:
        raise ValueError(f"prepare_set takes a SoundCloud set link, not a {parsed.kind} input")
    tools = tools or Tools()
    options = soundcloud_sets.LISTING_OPTIONS
    # The YoutubeDL is made inside the error mapping, as prepare's is.
    with only_scmp_errors(where="data"), tools.make_ydl(token, extra=options) as ydl:
        playlist = soundcloud_sets.list_set(parsed.url, ydl, token)
    if not playlist.entries:
        # SoundCloud keeps a set whose tracks were all removed or made private.
        raise ScmpError(ErrorKind.unavailable, "the set has no tracks")
    return PreparedSet(
        url=parsed.url,
        playlist=playlist,
        default_mode=soundcloud_sets.default_mode(playlist),
        settings=settings,
        token=token,
        tools=tools,
    )


def prepare_set_track(
    prepared_set: PreparedSet, entry: PlaylistEntry, *, job_id: str | None = None
) -> Prepared:
    """Resolve one track of a set the way prepare resolves a track link. Raises as prepare does.

    Each call resolves one track, so the app runs them in its resolve pool.
    """
    return prepare(
        classify(entry.url),
        settings=prepared_set.settings,
        token=prepared_set.token,
        tools=prepared_set.tools,
        job_id=job_id,
    )


def prepare_set_tracks(prepared_set: PreparedSet) -> list[SetTrack]:
    """Resolve every track of a set in order, one after another. A failure stays with its track."""
    return [_resolve_set_track(prepared_set, entry) for entry in prepared_set.playlist.entries]


def _resolve_set_track(prepared_set: PreparedSet, entry: PlaylistEntry) -> SetTrack:
    try:
        return SetTrack(prepare_set_track(prepared_set, entry))
    except ScmpError as error:
        log.warning("Couldn't resolve a track of the set: %s", error)
        return SetTrack(None, error=error)


def _check_availability(source: SourceInfo) -> None:
    """Raise the matching kind for a DRM, preview-only or blocked track, before any review."""
    if source.availability == "ok":
        return
    kind = AVAILABILITY_KINDS[source.availability]
    raise ScmpError(kind, f"the track's availability is {source.availability}")


def find_duplicate(history: History, source: SourceInfo) -> HistoryEntry | None:
    """The latest save of this track, by its id or its link. A history failure only warns."""
    try:
        return history.find_by_source_id(source.id) or history.find_by_url(source.url)
    except ScmpError as error:
        # The check only adds a warning, so a broken history mustn't stop the save.
        log.warning("Couldn't look the track up in the history: %s", error)
        return None
