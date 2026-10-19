"""Where cover images come from: links, pasted data URLs, and the sizes yt-dlp and SoundCloud offer.

What these return is raw image bytes or URLs. `artwork.process` turns the bytes into a cover.
"""

import base64
import binascii
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from types import TracebackType
from typing import Any, Protocol, Self
from urllib.parse import unquote_to_bytes, urlsplit

from scmp3.core.errors import Cancelled, ErrorKind, ScmpError
from scmp3.core.log import safe_url
from scmp3.core.models import PlaylistInfo, SourceInfo

MAX_IMAGE_BYTES = 20_000_000
_READ_CHUNK_BYTES = 64 * 1024

_WEB_SCHEMES = frozenset({"http", "https"})

# SoundCloud offers these sizes too, but they're upscaled from the original.
SOUNDCLOUD_UPSCALED = frozenset({"t1080x1080", "t3000x3000"})
SOUNDCLOUD_IMAGE_HOST = "sndcdn.com"

# The size at the end of a SoundCloud image URL, as in artworks-…-t500x500.jpg.
_SOUNDCLOUD_SIZE = re.compile(r"-(?P<size>[0-9a-z]+)\.(?P<ext>jpg|png)(?=$|\?)")

# data:[<media type>][;<parameter>…],<data>
_DATA_URL = re.compile(r"(?i:data):(?P<type>[^;,]*)(?P<params>[^,]*),(?P<data>.*)", re.DOTALL)


class Headers(Protocol):
    """The response headers fetch_image reads."""

    def get(self, name: str, /) -> str | None: ...


class ImageResponse(Protocol):
    """What fetch_image needs from an opener's response. yt-dlp's and urllib's both fit."""

    headers: Headers

    def read(self, size: int = -1, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> object: ...


# Opens a URL and returns the response. In the app this is yt-dlp's `urlopen`.
Opener = Callable[[str], ImageResponse]


def fetch_image(url: str, opener: Opener, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Download an image through `opener`, refusing anything that isn't an image or is too big."""
    _check_web_link(url)
    where = safe_url(url)
    try:
        with opener(url) as response:
            _check_image_type(response.headers.get("Content-Type"), where)
            return _read_capped(response, max_bytes, where)
    except (ScmpError, Cancelled):
        raise
    except Exception as error:
        # Each opener raises its own error types, so failures are told apart by HTTP status alone.
        _close_response(error)
        detail = f"couldn't fetch the image at {where}: {error}"
        raise ScmpError(_request_error_kind(error), detail) from error


def from_data_url(s: str) -> bytes:
    """The image bytes in a data URL, such as the one the UI sends for a pasted image."""
    where = "the pasted image"
    match = _DATA_URL.fullmatch(s.strip())
    if match is None:
        raise ScmpError(ErrorKind.unavailable, f"{where} isn't a data URL")
    _check_image_type(match["type"], where)
    payload = match["data"]
    if "base64" not in match["params"].lower().split(";"):
        return unquote_to_bytes(payload)
    try:
        return base64.b64decode(payload, validate=True)
    except binascii.Error as error:
        raise ScmpError(ErrorKind.unavailable, f"{where} has invalid base64 data") from error


def pick_largest(
    thumbnails: Iterable[Mapping[str, Any]], exclude: Collection[str] = ()
) -> str | None:
    """The URL of the best thumbnail in a yt-dlp list, skipping those whose id is in `exclude`.

    They're ranked as yt-dlp ranks them, by preference, then width, then height. That puts
    SoundCloud's original first, though it has no size listed.
    """
    usable = [t for t in thumbnails if t.get("url") and t.get("id") not in exclude]
    if not usable:
        return None
    return max(usable, key=_thumbnail_rank)["url"]


def soundcloud_candidates(source: SourceInfo | PlaylistInfo) -> list[str]:
    """The cover URLs to try in order for a SoundCloud track or set: the original, then 500 px."""
    url = source.artwork_url
    if not url:
        return []
    match = _SOUNDCLOUD_SIZE.search(url)
    if match is None or not _is_soundcloud_image(url):
        # Not a sized SoundCloud image, so there are no other sizes to try.
        return [url]
    # The original may be a JPEG or a PNG, and the URL's extension is only a guess.
    ext = match["ext"]
    other_ext = "png" if ext == "jpg" else "jpg"
    return [
        _with_size(url, match, "original", ext),
        _with_size(url, match, "original", other_ext),
        # SoundCloud serves every size except the original as JPEG.
        _with_size(url, match, "t500x500", "jpg"),
    ]


def _check_web_link(url: str) -> None:
    """Refuse a link that isn't http or https, such as a file:// one, before any request."""
    scheme = urlsplit(url).scheme.lower()
    if scheme in _WEB_SCHEMES:
        return
    # Only the scheme goes in the detail, since a file link holds a local path.
    shown = f"a {scheme}: link" if scheme else "text that isn't a link"
    detail = f"can't fetch an image from {shown}; only http and https links work"
    raise ScmpError(ErrorKind.unavailable, detail)


def _check_image_type(content_type: str | None, where: str) -> None:
    """Refuse a media type that isn't an image, such as an HTML error page."""
    media_type = (content_type or "").split(";")[0].strip().lower()
    if not media_type.startswith("image/"):
        sent = media_type or "no media type"
        raise ScmpError(ErrorKind.unavailable, f"{where} is {sent}, not an image")


def _read_capped(response: ImageResponse, max_bytes: int, where: str) -> bytes:
    """Read the whole body, stopping as soon as it passes `max_bytes`."""
    chunks: list[bytes] = []
    total = 0
    while chunk := response.read(_READ_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            detail = f"the image at {where} is over {max_bytes} bytes"
            raise ScmpError(ErrorKind.unavailable, detail)
        chunks.append(chunk)
    return b"".join(chunks)


def _request_error_kind(error: Exception) -> ErrorKind:
    """What a failed image request means, read from the HTTP status when there is one."""
    # These come from our own code, such as an opener that returned nothing.
    if isinstance(error, TypeError | AttributeError):
        return ErrorKind.internal
    status = getattr(error, "status", None)
    if not isinstance(status, int):
        # No status means no answer came back.
        return ErrorKind.offline
    if status == 429:
        return ErrorKind.rate_limited
    if 500 <= status <= 599:
        return ErrorKind.service_down
    return ErrorKind.unavailable


def _close_response(error: Exception) -> None:
    """Close the response an HTTP error holds open, as yt-dlp's and urllib's errors both do."""
    close = getattr(error, "close", None)
    if callable(close):
        close()


def _thumbnail_rank(thumbnail: Mapping[str, Any]) -> tuple[float, float, float]:
    """yt-dlp's sort key for thumbnails, where a missing value counts as -1."""
    return (
        _rank_value(thumbnail.get("preference")),
        _rank_value(thumbnail.get("width")),
        _rank_value(thumbnail.get("height")),
    )


def _rank_value(value: object) -> float:
    """A number from a thumbnail field, or -1 when it's missing."""
    if isinstance(value, int | float):
        return value
    return -1


def _is_soundcloud_image(url: str) -> bool:
    """Whether the URL is on SoundCloud's image servers, such as i1.sndcdn.com."""
    host = urlsplit(url).hostname or ""
    return host == SOUNDCLOUD_IMAGE_HOST or host.endswith(f".{SOUNDCLOUD_IMAGE_HOST}")


def _with_size(url: str, match: re.Match[str], size: str, ext: str) -> str:
    """The same SoundCloud image URL at another size."""
    return f"{url[: match.start()]}-{size}.{ext}{url[match.end() :]}"
