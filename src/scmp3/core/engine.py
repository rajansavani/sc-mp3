"""The download engine: yt-dlp instances with the standard options, and API calls made through them.

yt-dlp is imported only through `load_yt_dlp`, so an updated copy can be put in its place first.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import register_secret, safe_url

if TYPE_CHECKING:
    from yt_dlp import YoutubeDL

YTDLP_LOGGER = "scmp3.ytdlp"
SOCKET_TIMEOUT = 30  # seconds

# What an HTTP error status from an API means for the job. 401 and 5xx are handled in
# _status_kind, and any other status is engine_outdated.
STATUS_KINDS: dict[int, ErrorKind] = {
    # SoundCloud answers 403 when its bot protection blocks us or the client_id is stale.
    403: ErrorKind.engine_outdated,
    404: ErrorKind.unavailable,
    410: ErrorKind.unavailable,
    429: ErrorKind.rate_limited,
}

ProgressHook = Callable[[dict[str, Any]], None]

log = logging.getLogger(__name__)


def load_yt_dlp() -> ModuleType:
    """Import yt-dlp. All of sc-mp3 gets the engine through here."""
    # TODO(T-071): Before this import, put a good updated copy from the components folder first
    # on sys.path, and fall back to the bundled copy if it fails to import.
    import yt_dlp

    return yt_dlp


def engine_version() -> str:
    """The version of the yt-dlp in use, such as 2026.08.19."""
    return load_yt_dlp().version.__version__


class YtDlpLogger:
    """Passes yt-dlp's messages to Python logging, so the redaction filter sees them."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(YTDLP_LOGGER)

    def debug(self, message: str) -> None:
        """Log a debug or progress line. These name files, so they stay at debug level."""
        self._logger.debug(message)

    def warning(self, message: str) -> None:
        """Log a warning, such as "Continuing as guest"."""
        self._logger.warning(message)

    def error(self, message: str) -> None:
        """Log an error. yt-dlp also raises it, so callers still see it as an exception."""
        self._logger.error(message)


def make_ydl(
    *,
    auth_token: str | None = None,
    progress_hook: ProgressHook | None = None,
    extra: Mapping[str, Any] | None = None,
) -> YoutubeDL:
    """Build a YoutubeDL with the standard options. Keys in `extra` override them."""
    params: dict[str, Any] = {
        "quiet": True,
        "noprogress": True,
        "logger": YtDlpLogger(),
        # Plain text, since the messages end up in a log file.
        "color": "never",
        "cachedir": str(paths.cache_dir() / "yt-dlp"),
        "socket_timeout": SOCKET_TIMEOUT,
    }
    ffmpeg = _find_ffmpeg()
    if ffmpeg:
        params["ffmpeg_location"] = str(ffmpeg)
    if auth_token:
        register_secret(auth_token)
        # yt-dlp's SoundCloud login takes the token as the password of a user called "oauth".
        params["username"] = "oauth"
        params["password"] = auth_token
    if progress_hook:
        params["progress_hooks"] = [progress_hook]
    params.update(extra or {})
    return load_yt_dlp().YoutubeDL(params)


def _find_ffmpeg() -> Path | None:
    """ffmpeg's path, or None with a warning. Reading track info works without it."""
    try:
        return paths.ffmpeg_exe()
    except ScmpError:
        log.warning("ffmpeg wasn't found, so yt-dlp runs without it")
        return None


def api_get_json(
    ydl: YoutubeDL,
    url: str,
    headers: Mapping[str, str] | None = None,
    *,
    status_kinds: Mapping[int, ErrorKind] | None = None,
) -> dict[str, Any]:
    """GET a JSON object through yt-dlp's opener, so its browser impersonation and cookies apply.

    `status_kinds` says what an HTTP error status means for this endpoint, ahead of the defaults.
    """
    load_yt_dlp()
    from yt_dlp.networking.exceptions import HTTPError, RequestError, TransportError

    where = safe_url(url)
    request_headers = {"Accept": "application/json", **(headers or {})}
    try:
        body = _fetch(ydl, url, request_headers)
    except HTTPError as error:
        error.close()
        sent_token = any(name.lower() == "authorization" for name in request_headers)
        kind = _status_kind(error.status, sent_token=sent_token, overrides=status_kinds or {})
        raise ScmpError(kind, f"HTTP {error.status} from {where}") from error
    except TransportError as error:
        raise ScmpError(ErrorKind.offline, f"couldn't reach {where}: {error}") from error
    except RequestError as error:
        # This is also how yt-dlp says it can't impersonate a browser, when curl_cffi is missing.
        raise ScmpError(ErrorKind.engine_outdated, f"request to {where} failed: {error}") from error

    try:
        data = json.loads(body)
    except ValueError as error:
        raise ScmpError(ErrorKind.engine_outdated, f"{where} didn't return JSON") from error
    if not isinstance(data, dict):
        raise ScmpError(ErrorKind.engine_outdated, f"{where} didn't return a JSON object")
    return data


def _status_kind(status: int, *, sent_token: bool, overrides: Mapping[int, ErrorKind]) -> ErrorKind:
    """What an HTTP error status means: the caller's overrides first, then the defaults."""
    if status in overrides:
        return overrides[status]
    if status == 401:
        # Only a request that sent a token can learn it expired. yt-dlp reads any other 401 as a
        # stale client_id.
        return ErrorKind.auth_expired if sent_token else ErrorKind.engine_outdated
    if 500 <= status <= 599:
        return ErrorKind.service_down
    return STATUS_KINDS.get(status, ErrorKind.engine_outdated)


def _fetch(ydl: YoutubeDL, url: str, headers: dict[str, str]) -> bytes:
    """GET `url` and return the body, impersonating Chrome as SoundCloud's bot protection needs."""
    from yt_dlp.networking.common import Request
    from yt_dlp.networking.impersonate import ImpersonateTarget

    # A bare "chrome" target gets the newest desktop Chrome that curl_cffi offers.
    request = Request(url, headers=headers, extensions={"impersonate": ImpersonateTarget("chrome")})
    with ydl.urlopen(request) as response:
        return response.read()
