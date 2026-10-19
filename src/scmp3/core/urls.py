"""Recognize SoundCloud and YouTube links and turn them into canonical URLs.

Only the standard library is used. Nothing here touches the network, except that
resolve_short_link makes one request through the opener it's given.
"""

import re
import unicodedata
from collections.abc import Callable
from contextlib import closing
from typing import Literal, Protocol
from urllib.parse import SplitResult, parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

type LinkKind = Literal["soundcloud_track", "soundcloud_set", "soundcloud_short", "youtube_video"]

# Reasons are shown to the user next to the item that was rejected.
NOT_SUPPORTED = (
    "Not a supported link. Paste a SoundCloud or YouTube link, or add an MP3 or M4A file."
)
SOUNDCLOUD_NOT_TRACK = (
    "This SoundCloud link isn't a track or a set. Open the track or set and copy its link."
)
SHORT_LINK_DEAD_END = (
    "This SoundCloud short link doesn't lead to a track or a set. "
    "Open it in a browser and copy the full link."
)
YOUTUBE_PLAYLIST = "YouTube playlists aren't supported yet."
YOUTUBE_NOT_VIDEO = "This YouTube link isn't a video. Open the video and copy its link."


class UnsupportedInput(ValueError):
    """A link or file sc-mp3 can't handle. The message is the reason to show the user."""


class Response(Protocol):
    """The parts of an HTTP response that resolving a short link reads."""

    @property
    def url(self) -> str: ...  # the final URL, after redirects

    def close(self) -> None: ...


# yt-dlp's `YoutubeDL.urlopen` fits this.
type Opener = Callable[[str], Response]

# Each host has a handler that classifies and normalizes its links.
type _LinkHandler = Callable[[SplitResult], tuple[LinkKind, str]]

# First path segments that are SoundCloud's own pages, not profiles.
_SOUNDCLOUD_PAGES = frozenset({"charts", "search", "stations", "tags", "you"})
# Second path segments that are tabs of a profile, not track names.
_SOUNDCLOUD_PROFILE_TABS = frozenset(
    {
        "albums",
        "comments",
        "followers",
        "following",
        "likes",
        "popular-tracks",
        "reposts",
        "sets",
        "spotlight",
        "tracks",
    }
)
# yt-dlp matches profile, track and short-link names with [\w-]+; set names may also hold ":".
_SOUNDCLOUD_NAME = re.compile(r"[\w-]+")
_SOUNDCLOUD_SET_NAME = re.compile(r"[\w:-]+")
_SECRET_TOKEN = re.compile(r"s-[A-Za-z0-9]+")
# api.soundcloud.com collections and the kind of input each one holds.
_SOUNDCLOUD_API_KINDS: dict[str, LinkKind] = {
    "tracks": "soundcloud_track",
    "playlists": "soundcloud_set",
}
# An api id is a number, sometimes written as a URN like "soundcloud:tracks:123".
_SOUNDCLOUD_API_ID = re.compile(r"(?:soundcloud:(?:tracks|playlists):)?(\d+)")

# youtube.com paths whose second segment is the video id.
_YOUTUBE_ID_PATHS = frozenset({"shorts", "live"})
_YOUTUBE_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")

# What chat text and web pages leave around a link. No link form here starts or ends with these.
# After the ASCII ones: curly quotes, guillemets, then fullwidth, corner and lenticular brackets.
_LINK_LEADING = "(<[\"'" + "\u201c\u2018\u00ab\u2039" + "\uff08\u300c\u300e\u3010"
# Then the closing forms, and the CJK full stop, comma, marks, colon, semicolon and list comma.
_LINK_TRAILING = (
    ")>]\"'.,;:!?"
    + "\u201d\u2019\u00bb\u203a"
    + "\uff09\u300d\u300f\u3011"
    + "\u3002\uff0c\uff01\uff1f\uff1a\uff1b\u3001"
)


def is_link(text: str) -> bool:
    """Whether text is an http(s) link, or a bare or protocol-relative link to a known host."""
    return _parse_link(text) is not None


def classify_link(text: str) -> tuple[LinkKind, str]:
    """Return the kind and normalized form of a link to any supported site."""
    return _classify_link(text, _LINK_HOSTS)


def normalize_soundcloud(url: str) -> str:
    """Return the canonical form of a SoundCloud link, unwrapping player embeds."""
    return _classify_link(url, _SOUNDCLOUD_HOSTS)[1]


def normalize_youtube(url: str) -> str:
    """Return https://www.youtube.com/watch?v=<id> for any YouTube link to one video."""
    return _classify_link(url, _YOUTUBE_HOSTS)[1]


def resolve_short_link(url: str, opener: Opener) -> str:
    """Follow an on.soundcloud.com link with one request and normalize where it leads."""
    kind, short_url = _classify_link(url, _SOUNDCLOUD_HOSTS)
    if kind != "soundcloud_short":
        # The URL stays out of the message, since it can hold a secret token.
        raise ValueError("resolve_short_link needs an on.soundcloud.com link")
    try:
        # Only the final URL matters, so the body is never read.
        with closing(opener(short_url)) as response:
            final_url = response.url
    except Exception as error:
        # A target that refuses the request, with a 403 say, still shows where the redirect went.
        target = _track_or_set(_response_url(error))
        if target is None:
            raise
        _close_response(error)
        return target
    target = _track_or_set(final_url)
    if target is None:
        raise UnsupportedInput(SHORT_LINK_DEAD_END)
    return target


def _parse_link(text: str) -> SplitResult | None:
    """Parse text as an http(s) link, ignoring invisible characters, brackets and punctuation."""
    text = _strip_invisible(text).lstrip(_LINK_LEADING).rstrip(_LINK_TRAILING)
    # A bare known host, or a protocol-relative link to one, gets https in front.
    bare = text.removeprefix("//")
    if re.split(r"[/?#]", bare, maxsplit=1)[0].lower() in _LINK_HOSTS:
        text = "https://" + bare
    elif not text[:8].lower().startswith(("http://", "https://")):
        return None
    if not _is_utf8(text):
        return None
    try:
        return urlsplit(text)
    except ValueError:
        return None  # a broken IPv6 host


def _strip_invisible(text: str) -> str:
    """Drop control and format characters, such as zero-width spaces, that copied text carries."""
    if text.isprintable():
        return text
    return "".join(char for char in text if unicodedata.category(char) not in ("Cc", "Cf"))


def _is_utf8(text: str) -> bool:
    """False for text with lone surrogates, which JavaScript can pass along and UTF-8 can't hold."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _classify_link(url: str, hosts: dict[str, _LinkHandler]) -> tuple[LinkKind, str]:
    """Classify and normalize a link, accepting only the hosts given."""
    link = _parse_link(url)
    if link is None:
        raise UnsupportedInput(NOT_SUPPORTED)
    return _handle_link(link, hosts)


def _handle_link(link: SplitResult, hosts: dict[str, _LinkHandler]) -> tuple[LinkKind, str]:
    handler = hosts.get(link.hostname or "")
    if handler is None:
        raise UnsupportedInput(NOT_SUPPORTED)
    return handler(link)


def _response_url(error: Exception) -> str | None:
    """The URL of the response an HTTP error carries, as yt-dlp's HTTPError does."""
    url = getattr(getattr(error, "response", None), "url", None)
    return url if isinstance(url, str) else None


def _close_response(error: Exception) -> None:
    """Close the response an HTTP error kept open, as yt-dlp's HTTPError.close does."""
    close = getattr(getattr(error, "response", None), "close", None)
    if callable(close):
        close()


def _track_or_set(url: str | None) -> str | None:
    """The normalized link when url is a SoundCloud track or set, else None."""
    if url is None:
        return None
    try:
        kind, target = _classify_link(url, _SOUNDCLOUD_HOSTS)
    except UnsupportedInput:
        return None
    # Landing on a short link again means the redirect went nowhere useful.
    return None if kind == "soundcloud_short" else target


def _path_segments(path: str) -> list[str]:
    """The non-empty parts of a URL path, so trailing and doubled slashes don't count."""
    return [segment for segment in path.split("/") if segment]


def _build_url(host: str, segments: list[str], query: str = "") -> str:
    """An https URL with no fragment, which is how every normalized link looks."""
    return urlunsplit(("https", host, "/" + "/".join(segments), query, ""))


def _with_param(url: str, key: str, value: str) -> str:
    """Append one query parameter to a URL."""
    parts = urlsplit(url)
    query = urlencode([*parse_qsl(parts.query, keep_blank_values=True), (key, value)])
    return urlunsplit(parts._replace(query=query))


def _soundcloud_page(link: SplitResult) -> tuple[LinkKind, str]:
    """A soundcloud.com permalink to a track or a set, public or secret."""
    segments = _path_segments(link.path)
    kind = _soundcloud_page_kind(segments)
    # Secret tokens live in the path and yt-dlp ignores the query, so only the path is kept.
    return kind, _build_url("soundcloud.com", segments)


def _soundcloud_page_kind(segments: list[str]) -> LinkKind:
    """Tell a track from a set by the shape of its path, checking names the way yt-dlp does."""
    if segments and segments[0].lower() in _SOUNDCLOUD_PAGES:
        raise UnsupportedInput(SOUNDCLOUD_NOT_TRACK)
    match segments:
        case [user, "sets", name, *rest] if _optional_secret(rest):
            if _SOUNDCLOUD_NAME.fullmatch(user) and _SOUNDCLOUD_SET_NAME.fullmatch(name):
                return "soundcloud_set"
        case [user, track, *rest] if _optional_secret(rest):
            are_names = _SOUNDCLOUD_NAME.fullmatch(user) and _SOUNDCLOUD_NAME.fullmatch(track)
            if are_names and track.lower() not in _SOUNDCLOUD_PROFILE_TABS:
                return "soundcloud_track"
    raise UnsupportedInput(SOUNDCLOUD_NOT_TRACK)


def _optional_secret(rest: list[str]) -> bool:
    """Whether what follows a permalink's names is nothing, or one secret token."""
    return not rest or (len(rest) == 1 and _SECRET_TOKEN.fullmatch(rest[0]) is not None)


def _soundcloud_short(link: SplitResult) -> tuple[LinkKind, str]:
    """An on.soundcloud.com share link, which takes one request to resolve."""
    segments = _path_segments(link.path)
    if len(segments) != 1 or not _SOUNDCLOUD_NAME.fullmatch(segments[0]):
        raise UnsupportedInput(SOUNDCLOUD_NOT_TRACK)
    # The code is the whole link and is case-sensitive; the target gets normalized on its own.
    return "soundcloud_short", _build_url("on.soundcloud.com", segments)


def _soundcloud_embed(link: SplitResult) -> tuple[LinkKind, str]:
    """A w.soundcloud.com player, classified by the link it wraps."""
    query = parse_qs(link.query)
    wrapped = query.get("url", [""])[0]
    if _path_segments(link.path) != ["player"] or not wrapped:
        raise UnsupportedInput(SOUNDCLOUD_NOT_TRACK)
    # Secret embeds can carry the token beside the wrapped link instead of inside it.
    if "secret_token" in query:
        wrapped = _with_param(wrapped, "secret_token", query["secret_token"][0])
    return _classify_link(wrapped, _SOUNDCLOUD_EMBEDDABLE)


def _soundcloud_api(link: SplitResult) -> tuple[LinkKind, str]:
    """An api or api-v2 link to a track or a playlist, by id or by URN."""
    segments = _path_segments(link.path)
    if len(segments) != 2 or segments[0] not in _SOUNDCLOUD_API_KINDS:
        raise UnsupportedInput(SOUNDCLOUD_NOT_TRACK)
    collection, reference = segments
    # The colons in a URN are often percent-encoded.
    found = _SOUNDCLOUD_API_ID.fullmatch(unquote(reference))
    if found is None:
        raise UnsupportedInput(SOUNDCLOUD_NOT_TRACK)
    # yt-dlp reads secret_token only when it's the sole parameter, so the rest go.
    tokens = parse_qs(link.query).get("secret_token", [])
    query = urlencode({"secret_token": tokens[0]}) if tokens else ""
    url = _build_url("api.soundcloud.com", [collection, found[1]], query)
    return _SOUNDCLOUD_API_KINDS[collection], url


def _youtube_site(link: SplitResult) -> tuple[LinkKind, str]:
    """A youtube.com or music.youtube.com link: a watch page, a short or a live stream."""
    segments = _path_segments(link.path)
    query = parse_qs(link.query)
    if segments == ["watch"]:
        return _youtube_video(query.get("v", [""])[0], query)
    if len(segments) == 2 and segments[0] in _YOUTUBE_ID_PATHS:
        return _youtube_video(segments[1], query)
    return _youtube_video("", query)


def _youtu_be(link: SplitResult) -> tuple[LinkKind, str]:
    """A youtu.be share link, whose path is the video id."""
    segments = _path_segments(link.path)
    video_id = segments[0] if len(segments) == 1 else ""
    return _youtube_video(video_id, parse_qs(link.query))


def _youtube_video(video_id: str, query: dict[str, list[str]]) -> tuple[LinkKind, str]:
    """The watch link for a video id. With no id, a list= parameter means a playlist."""
    if not video_id and "list" in query:
        raise UnsupportedInput(YOUTUBE_PLAYLIST)
    if not _YOUTUBE_VIDEO_ID.fullmatch(video_id):
        raise UnsupportedInput(YOUTUBE_NOT_VIDEO)
    # Everything else in the query (list, t, si, feature) is dropped.
    return "youtube_video", f"https://www.youtube.com/watch?v={video_id}"


# Hosts a player embed may wrap. The player host isn't one, so embeds can't nest.
_SOUNDCLOUD_EMBEDDABLE: dict[str, _LinkHandler] = {
    "soundcloud.com": _soundcloud_page,
    "www.soundcloud.com": _soundcloud_page,
    "m.soundcloud.com": _soundcloud_page,
    "on.soundcloud.com": _soundcloud_short,
    "api.soundcloud.com": _soundcloud_api,
    "api-v2.soundcloud.com": _soundcloud_api,
}
_SOUNDCLOUD_HOSTS = _SOUNDCLOUD_EMBEDDABLE | {"w.soundcloud.com": _soundcloud_embed}
_YOUTUBE_HOSTS: dict[str, _LinkHandler] = {
    "youtube.com": _youtube_site,
    "www.youtube.com": _youtube_site,
    "m.youtube.com": _youtube_site,
    "music.youtube.com": _youtube_site,
    "youtu.be": _youtu_be,
}
# Every host a pasted link may point at. A new source adds its table here.
_LINK_HOSTS = _SOUNDCLOUD_HOSTS | _YOUTUBE_HOSTS
