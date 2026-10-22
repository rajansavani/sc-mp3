"""SoundCloud sets: list a set's tracks before any of them is resolved, and pick its default mode.

yt-dlp lists the tracks, and one api-v2 call adds the set's type, release date and first cover
(TECH_SPEC §6). Each track is then resolved on its own with soundcloud.resolve_track, which callers
run in their resolve pool.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from scmp3.core import engine
from scmp3.core.errors import ScmpError
from scmp3.core.models import AlbumMode, PlaylistEntry, PlaylistInfo
from scmp3.core.sources import soundcloud, soundcloud_fields, ytdlp_errors
from scmp3.core.urls import UnsupportedInput, classify_link

if TYPE_CHECKING:
    from yt_dlp import YoutubeDL

# Build list_set's YoutubeDL with these, so yt-dlp lists the tracks without extracting each one.
LISTING_OPTIONS: Mapping[str, Any] = {"extract_flat": "in_playlist"}

# The set types that save as an album by default. Any other set saves as singles (PLAN §4.4).
ALBUM_SET_TYPES = frozenset({"album", "ep", "single"})
# yt-dlp's album_type for a set that has no type, such as a playlist someone put together.
PLAIN_PLAYLIST = "playlist"

log = logging.getLogger(__name__)


def list_set(url: str, ydl: YoutubeDL, token: str | None = None) -> PlaylistInfo:
    """List a set's tracks in order, with the set's own details and cover. No track is resolved.

    Build `ydl` with soundcloud.make_ydl, the same token and LISTING_OPTIONS. When the api-v2 call
    fails, yt-dlp's listing is used alone. Raises ScmpError when the set can't be read.
    """
    yt_dlp = engine.load_yt_dlp()
    link = _set_link(url)
    try:
        info = ydl.extract_info(link, download=False)
    except yt_dlp.utils.YoutubeDLError as error:
        raise ytdlp_errors.to_scmp_error(error) from error
    return _playlist_info(info, _try_api_set(ydl, link, token))


def api_set(ydl: YoutubeDL, url: str, token: str | None = None) -> dict[str, Any]:
    """api-v2's /resolve answer for a set link, which holds the set's type."""
    return soundcloud.api_resolve(ydl, url, "playlist", token)


def default_mode(playlist: PlaylistInfo) -> AlbumMode:
    """Album mode for a set SoundCloud marks as an album, EP or single, else singles mode."""
    if playlist.set_type:
        return "album" if playlist.set_type in ALBUM_SET_TYPES else "singles"
    # A set without a type can still be marked as an album.
    return "album" if playlist.is_album else "singles"


def _set_link(url: str) -> str:
    """The normalized form of a set link, before any request is made."""
    kind, link = classify_link(url)
    if kind != "soundcloud_set":
        # The caller sends tracks elsewhere; the link stays out, as it can be secret.
        raise ValueError(f"list_set takes a SoundCloud set link, not a {kind} link")
    return link


def _try_api_set(ydl: YoutubeDL, link: str, token: str | None) -> dict[str, Any]:
    """api_set, or an empty answer when the call fails, since yt-dlp's listing is enough."""
    try:
        return api_set(ydl, link, token)
    except ScmpError as error:
        log.warning("Listing the set without api-v2's fields: %s", error)
        return {}


def _playlist_info(info: Mapping[str, Any], api: Mapping[str, Any]) -> PlaylistInfo:
    """The set's details from api-v2's answer, each falling back to yt-dlp's listing."""
    owner = (api.get("user") or {}).get("username")
    tracks = api.get("tracks") or []
    release_date = soundcloud_fields.iso_date(api.get("release_date"))
    return PlaylistInfo(
        title=api.get("title") or info.get("title") or "",
        owner=owner or info.get("uploader") or "",
        set_type=_set_type(info, api),
        is_album=api.get("is_album") is True,
        release_date=release_date or soundcloud_fields.yyyymmdd(info.get("release_date")),
        genre=api.get("genre") or soundcloud_fields.first(info.get("genres")),
        artwork_url=_set_artwork(info, tracks),
        entries=_entries(info.get("entries") or [], tracks),
    )


def _set_type(info: Mapping[str, Any], api: Mapping[str, Any]) -> str | None:
    """album, ep, single or compilation, or None for a plain playlist."""
    # api-v2 gives "" for a plain playlist, and yt-dlp then says "playlist".
    found = api.get("set_type") or info.get("album_type")
    if not isinstance(found, str) or found == PLAIN_PLAYLIST:
        return None
    return found


def _set_artwork(info: Mapping[str, Any], tracks: Sequence[Mapping[str, Any]]) -> str | None:
    """The set's own cover, or else its first track's from api-v2, as SoundCloud shows it.

    For a set without its own cover, yt-dlp offers the owner's avatar, which isn't one. The first
    track's cover is known even when the track itself can't be resolved, as with DRM.
    """
    own = soundcloud_fields.pick_artwork(info.get("thumbnails") or [])
    if own and not soundcloud_fields.is_avatar(own):
        return own
    return soundcloud_fields.t500x500(tracks[0].get("artwork_url")) if tracks else None


def _entries(
    listed: Sequence[Mapping[str, Any]], tracks: Sequence[Mapping[str, Any]]
) -> list[PlaylistEntry]:
    """The set's tracks in order, as yt-dlp lists them, with the titles api-v2 already gave.

    api-v2 describes only the first few tracks in full, so yt-dlp lists the rest by their api link,
    and they have no title until they're resolved.
    """
    titles = {str(track.get("id")): track["title"] for track in tracks if track.get("title")}
    entries = [_entry(item, titles) for item in listed]
    return [entry for entry in entries if entry is not None]


def _entry(item: Mapping[str, Any], titles: Mapping[str, str]) -> PlaylistEntry | None:
    """One listed track, or None, with a warning, when yt-dlp gave no track link for it."""
    try:
        kind, link = classify_link(str(item.get("url") or ""))
    except UnsupportedInput:
        kind, link = None, ""
    if kind != "soundcloud_track":
        log.warning("Leaving out a set entry that yt-dlp listed without a track link")
        return None
    track_id = str(item.get("id") or "")
    return PlaylistEntry(
        url=link,
        id=f"soundcloud:tracks:{track_id}" if track_id else None,
        title=item.get("title") or titles.get(track_id),
    )
