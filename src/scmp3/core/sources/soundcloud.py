"""SoundCloud tracks: resolve a link into a SourceInfo. soundcloud_formats picks what to download,
soundcloud_sets lists the tracks of a set, and soundcloud_fields maps single values.

yt-dlp extracts the track, and one extra api-v2 call adds the fields it leaves out (TECH_SPEC §6).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit

from scmp3.core import engine
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import register_secret
from scmp3.core.models import Availability, FormatOption, OriginalDownload, SourceInfo
from scmp3.core.sources import ytdlp_errors
from scmp3.core.sources.soundcloud_fields import (
    codec,
    first,
    is_avatar,
    iso_date,
    pick_artwork,
    seconds,
    t500x500,
    yyyymmdd,
)
from scmp3.core.sources.soundcloud_formats import is_preview, original_format
from scmp3.core.urls import LinkKind, UnsupportedInput, classify_link, resolve_short_link

if TYPE_CHECKING:
    from yt_dlp import YoutubeDL

API_RESOLVE = "https://api-v2.soundcloud.com/resolve"
# The host every api link is normalized to.
API_HOST = "api.soundcloud.com"

# Each format yt-dlp lists costs one API request, against a limit of about 600 per 10 minutes.
# So only AAC and MP3 are asked for, named in full so encrypted HLS (hls-aes) never is.
STREAM_FORMATS = ("hls_aac", "http_aac", "hls_mp3", "http_mp3")

# What an error status from /resolve means. 401 is left to api_get_json, which reads it as an
# expired sign-in when our token was sent, and as a stale client_id when it wasn't.
RESOLVE_STATUS_KINDS: dict[int, ErrorKind] = {
    403: ErrorKind.engine_outdated,
    404: ErrorKind.unavailable,
}

# yt-dlp refuses these, but api-v2 still describes them, so they resolve with this availability.
REFUSED_AVAILABILITY: dict[ErrorKind, Availability] = {
    ErrorKind.drm: "drm",
    ErrorKind.geo_blocked: "geo_blocked",
}

# A preview lasts 30 seconds; this much either side still counts.
PREVIEW_SECONDS = 30.0
PREVIEW_TOLERANCE = 1.0

log = logging.getLogger(__name__)


@dataclass
class ResolvedTrack:
    """A resolved track, plus yt-dlp's info dict, which downloading starts from."""

    source: SourceInfo
    info: dict[str, Any] | None  # None when yt-dlp refused the track, as it does for DRM


def make_ydl(
    token: str | None = None,
    *,
    progress_hook: engine.ProgressHook | None = None,
    extra: Mapping[str, Any] | None = None,
) -> YoutubeDL:
    """engine.make_ydl plus the lean SoundCloud format list. Keys in `extra` override both.

    The keywords match download()'s ydl_factory, so this, or a partial with the token, fits it.
    """
    options = {**ydl_options(), **(extra or {})}
    return engine.make_ydl(auth_token=token, progress_hook=progress_hook, extra=options)


def ydl_options() -> dict[str, Any]:
    """The yt-dlp options SoundCloud adds to the standard ones, new each time so none are shared."""
    return {"extractor_args": {"soundcloud": {"formats": list(STREAM_FORMATS)}}}


def resolve(url: str, ydl: YoutubeDL, token: str | None = None) -> SourceInfo:
    """Resolve a track link into a SourceInfo. See resolve_track."""
    return resolve_track(url, ydl, token).source


def resolve_track(url: str, ydl: YoutubeDL, token: str | None = None) -> ResolvedTrack:
    """Resolve a track link, raising ScmpError when the track can't be read.

    DRM, geo-blocked and preview-only tracks still resolve, with `availability` saying why they
    can't be saved. Build `ydl` with make_ydl and the same token. A short link has to be followed
    first, with follow_short_link, since it can lead to a set.
    """
    yt_dlp = engine.load_yt_dlp()
    link = _track_link(url)
    try:
        info = ydl.extract_info(link, download=False)
    except yt_dlp.utils.YoutubeDLError as error:
        return ResolvedTrack(_resolve_refused(link, ydl, token, error), None)
    track = _try_api_track(ydl, link, token)
    source = _from_info(_page_link(link, info), info)
    if track is not None:
        _add_api_fields(source, track)
    source.original = _original(source.formats, track, signed_in=bool(token))
    source.availability = _availability(source, track)
    return ResolvedTrack(source, info)


def follow_short_link(url: str, ydl: YoutubeDL) -> tuple[LinkKind, str]:
    """Where an on.soundcloud.com link leads: a track or a set, and its normalized link.

    It takes one request. yt-dlp's errors become ScmpError, and a link that leads to neither
    raises UnsupportedInput with the reason.
    """
    yt_dlp = engine.load_yt_dlp()
    try:
        target = resolve_short_link(url, ydl.urlopen)
    except yt_dlp.utils.YoutubeDLError as error:
        raise ytdlp_errors.to_scmp_error(error) from error
    return classify_link(target)


def _track_link(url: str) -> str:
    """The normalized form of a track link, before any request is made."""
    kind, link = classify_link(url)
    if kind != "soundcloud_track":
        # The caller sends sets and short links elsewhere; the link stays out, as it can be secret.
        raise ValueError(f"resolve takes a SoundCloud track link, not a {kind} link")
    return link


def _page_link(link: str, info: Mapping[str, Any]) -> str:
    """The track's own page in place of a public api link, which sets list some tracks by.

    So the tags and the history hold the page. A secret api link stays, since its page would need
    the token too.
    """
    parts = urlsplit(link)
    if parts.hostname != API_HOST or parts.query:
        return link
    try:
        kind, page = classify_link(str(info.get("webpage_url") or ""))
    except UnsupportedInput:
        return link
    return page if kind == "soundcloud_track" else link


def api_track(ydl: YoutubeDL, url: str, token: str | None = None) -> dict[str, Any]:
    """api-v2's /resolve answer for a track link, which holds the fields yt-dlp leaves out."""
    return api_resolve(ydl, url, "track", token)


def api_resolve(ydl: YoutubeDL, url: str, kind: str, token: str | None = None) -> dict[str, Any]:
    """api-v2's /resolve answer for a link, refused unless it describes a `kind`, like "track"."""
    # yt-dlp stores the client_id it finds, and the advanced setting overrides it there too.
    client_id = ydl.cache.load("soundcloud", "client_id")
    if not client_id:
        raise ScmpError(ErrorKind.engine_outdated, "yt-dlp has no SoundCloud client_id")
    headers: dict[str, str] = {}
    if token:
        register_secret(token)
        # On this request only: set on yt-dlp itself, it breaks extraction (yt-dlp #12249).
        headers["Authorization"] = f"OAuth {token}"
    query = urlencode({"url": url, "client_id": client_id})
    answer = engine.api_get_json(
        ydl, f"{API_RESOLVE}?{query}", headers, status_kinds=RESOLVE_STATUS_KINDS
    )
    if answer.get("kind") != kind:
        raise ScmpError(ErrorKind.engine_outdated, f"api-v2 /resolve didn't return a {kind}")
    return answer


def _resolve_refused(
    link: str, ydl: YoutubeDL, token: str | None, error: BaseException
) -> SourceInfo:
    """Describe a track yt-dlp refused from api-v2 alone, or raise the refusal as a ScmpError."""
    refusal = ytdlp_errors.to_scmp_error(error)
    availability = REFUSED_AVAILABILITY.get(refusal.kind)
    if availability is None:
        raise refusal from error
    track = _try_api_track(ydl, link, token)
    if track is None:
        raise refusal from error
    source = _from_api(link, track)
    _add_api_fields(source, track)
    source.availability = availability
    return source


def _try_api_track(ydl: YoutubeDL, link: str, token: str | None) -> dict[str, Any] | None:
    """api_track, or None when the call fails, since yt-dlp's fields are enough to go on."""
    try:
        return api_track(ydl, link, token)
    except ScmpError as error:
        log.warning("Carrying on without api-v2's fields: %s", error)
        return None


def _from_info(link: str, info: Mapping[str, Any]) -> SourceInfo:
    """The SourceInfo fields yt-dlp's info dict gives."""
    artwork_url = pick_artwork(info.get("thumbnails") or [])
    upload_date = yyyymmdd(info.get("upload_date"))
    return SourceInfo(
        kind="soundcloud",
        id=f"soundcloud:tracks:{info['id']}",
        url=link,
        title=info.get("title") or "",
        uploader=info.get("uploader") or "",
        # Never `artist`: yt-dlp swaps the commas in it for FULLWIDTH COMMA (yt-dlp #16295).
        artists=list(info.get("artists") or []),
        release_date=yyyymmdd(info.get("release_date")) or upload_date,
        upload_date=upload_date,
        genre=first(info.get("genres")),
        description=info.get("description") or None,
        duration=info.get("duration"),
        artwork_url=artwork_url,
        artwork_is_avatar=is_avatar(artwork_url),
        formats=[_format_option(fmt) for fmt in info.get("formats") or []],
    )


def _from_api(link: str, track: Mapping[str, Any]) -> SourceInfo:
    """The SourceInfo fields api-v2 gives for a track yt-dlp refused. It has no formats."""
    user = track.get("user") or {}
    artwork_url = t500x500(track.get("artwork_url") or user.get("avatar_url"))
    publisher = track.get("publisher_metadata") or {}
    upload_date = iso_date(track.get("created_at"))
    return SourceInfo(
        kind="soundcloud",
        id=f"soundcloud:tracks:{track['id']}",
        url=link,
        title=track.get("title") or "",
        uploader=user.get("username") or "",
        artists=[publisher["artist"]] if publisher.get("artist") else [],
        release_date=upload_date,
        upload_date=upload_date,
        genre=track.get("genre") or None,
        description=track.get("description") or None,
        duration=seconds(track.get("duration")),
        artwork_url=artwork_url,
        artwork_is_avatar=is_avatar(artwork_url),
    )


def _add_api_fields(source: SourceInfo, track: Mapping[str, Any]) -> None:
    """Fill in what only api-v2 has: publisher details, the release date and the full length."""
    publisher = track.get("publisher_metadata") or {}
    source.album = publisher.get("album_title") or None
    source.isrc = publisher.get("isrc") or None
    explicit = publisher.get("explicit")
    source.explicit = explicit if isinstance(explicit, bool) else None
    # The label sits beside the publisher details on the track, not inside them.
    source.label = publisher.get("label_name") or track.get("label_name") or None
    source.release_date = iso_date(track.get("release_date")) or source.release_date
    source.description = track.get("description") or source.description
    source.full_duration = seconds(track.get("full_duration"))


def _original(
    formats: Sequence[FormatOption], track: Mapping[str, Any] | None, *, signed_in: bool
) -> OriginalDownload | None:
    """The artist's own file, when yt-dlp found it, or when signing in would offer it."""
    found = original_format(formats)
    if found is not None:
        return OriginalDownload(ext=found.ext or None)
    offered = track is not None and track.get("downloadable") and track.get("has_downloads_left")
    if offered and not signed_in:
        return OriginalDownload(needs_sign_in=True)
    return None


def _availability(source: SourceInfo, track: Mapping[str, Any] | None) -> Availability:
    """Whether the track can be saved, from api-v2's policy and the formats yt-dlp found."""
    policy = track.get("policy") if track is not None else None
    if policy == "BLOCK":
        return "geo_blocked"
    if policy == "SNIP" or _only_previews(source.formats) or _is_snipped(source):
        return "preview_only"
    return "ok"


def _only_previews(formats: Sequence[FormatOption]) -> bool:
    streams = [fmt for fmt in formats if not fmt.is_original]
    return bool(streams) and all(is_preview(fmt) for fmt in streams)


def _is_snipped(source: SourceInfo) -> bool:
    """Whether the length is a preview's 30 seconds while the real track is longer."""
    if source.duration is None or source.full_duration is None:
        return False
    is_preview_length = abs(source.duration - PREVIEW_SECONDS) <= PREVIEW_TOLERANCE
    return is_preview_length and source.full_duration > source.duration + PREVIEW_TOLERANCE


def _format_option(fmt: Mapping[str, Any]) -> FormatOption:
    """One entry of yt-dlp's formats list as a FormatOption."""
    format_id = str(fmt.get("format_id") or "")
    return FormatOption(
        format_id=format_id,
        ext=fmt.get("ext") or "",
        codec=codec(fmt.get("acodec"), format_id),
        bitrate=fmt.get("abr") or fmt.get("tbr"),
        # yt-dlp names previews ..._preview and ranks them at -10.
        is_preview="_preview" in format_id or fmt.get("preference") == -10,
        is_original=format_id == "download",
    )
