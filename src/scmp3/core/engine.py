"""The download engine: yt-dlp instances with the standard options, and API calls made through them.

yt-dlp is imported only through `load_yt_dlp`. Before anything does, engine_start.py puts a good
updated copy first on sys.path.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from scmp3.core import overlay, paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import redact, register_secret, safe_url
from scmp3.core.releases import version_key

if TYPE_CHECKING:
    from yt_dlp import YoutubeDL
    from yt_dlp.networking.exceptions import HTTPError

YTDLP_LOGGER = "scmp3.ytdlp"
SOCKET_TIMEOUT = 30  # seconds
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"

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

# Warnings from yt-dlp that are harmless, so they go to DEBUG.
QUIET_WARNINGS = (
    # Parallel resolves race to write the client_id cache, and the winner's copy is kept.
    "Writing cache to ",
    # The app ships no ffprobe (TECH_SPEC §19), so yt-dlp remuxes HLS audio without checking it.
    "Unable to extract metadata: ffprobe not found",
)

# Lets yt-dlp fetch the YouTube scripts from GitHub when its own copy doesn't fit (TECH_SPEC §7).
REMOTE_EJS = ["ejs:github"]

log = logging.getLogger(__name__)


class HttpStatusError(ScmpError):
    """An API answered with an HTTP error status. `answer` is the JSON object in its body, or {}."""

    def __init__(
        self, kind: ErrorKind, detail: str, *, status: int, answer: dict[str, Any]
    ) -> None:
        super().__init__(kind, detail)
        self.status = status
        self.answer = answer


def load_yt_dlp() -> ModuleType:
    """Import yt-dlp, or get the copy start_engine imported. All of sc-mp3 gets the engine here."""
    overlay.turn_off_plugins()
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
        """Log a warning, such as "Continuing as guest". The harmless ones go to DEBUG."""
        if message.startswith(QUIET_WARNINGS):
            self._logger.debug(message)
            return
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
    if needs_remote_ejs():
        params["remote_components"] = list(REMOTE_EJS)
    params.update(extra or {})
    return load_yt_dlp().YoutubeDL(params)


@functools.cache
def needs_remote_ejs() -> bool:
    """Whether the loaded yt-dlp-ejs isn't the major.minor that the loaded yt-dlp asks for."""
    load_yt_dlp()
    try:
        from yt_dlp.extractor.youtube.jsc._builtin import vendor
    except ImportError as error:
        log.info("Can't tell which YouTube scripts yt-dlp wants, so they may come from GitHub")
        log.debug("Looking up yt-dlp's YouTube script version failed", exc_info=error)
        return True
    try:
        import yt_dlp_ejs
    except ImportError:
        log.info("yt-dlp-ejs is missing, so the YouTube scripts may come from GitHub")
        return True
    loaded, wanted = yt_dlp_ejs.version, vendor.VERSION
    if version_key(loaded)[:2] == version_key(wanted)[:2]:
        return False
    log.info("yt-dlp-ejs %s isn't the %s yt-dlp wants, so GitHub's may be used", loaded, wanted)
    return True


def store_client_id(client_id: str | None) -> None:
    """Have yt-dlp use this SoundCloud client_id, or find its own again when it's None."""
    # yt-dlp keeps it until SoundCloud rejects it, and then finds a new one by itself.
    with make_ydl() as ydl:
        ydl.cache.store("soundcloud", "client_id", client_id)


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
        raise ScmpError(ErrorKind.offline, _failure(f"couldn't reach {where}", error)) from None
    except RequestError as error:
        # This is also how yt-dlp says it can't impersonate a browser, when curl_cffi is missing.
        detail = _failure(f"request to {where} failed", error)
        raise ScmpError(ErrorKind.engine_outdated, detail) from None
    return _json_object(body, where)


def api_post_form(
    ydl: YoutubeDL,
    url: str,
    form: Mapping[str, str],
    *,
    status_kinds: Mapping[int, ErrorKind] | None = None,
) -> dict[str, Any]:
    """POST a form through yt-dlp's opener, impersonating Chrome, and return the JSON object.

    An error status raises HttpStatusError, whose `answer` holds the body's JSON, such as an OAuth
    error code. `status_kinds` works as in api_get_json.
    """
    load_yt_dlp()
    from yt_dlp.networking.exceptions import HTTPError, RequestError, TransportError

    where = safe_url(url)
    headers = {"Accept": "application/json", "Content-Type": FORM_CONTENT_TYPE}
    try:
        body = _fetch(ydl, url, headers, data=urlencode(form).encode())
    except HTTPError as error:
        answer = _error_answer(error)
        kind = _status_kind(error.status, sent_token=False, overrides=status_kinds or {})
        detail = f"HTTP {error.status} from {where}"
        raise HttpStatusError(kind, detail, status=error.status, answer=answer) from error
    except TransportError as error:
        raise ScmpError(ErrorKind.offline, _failure(f"couldn't reach {where}", error)) from None
    except RequestError as error:
        detail = _failure(f"request to {where} failed", error)
        raise ScmpError(ErrorKind.engine_outdated, detail) from None
    return _json_object(body, where)


def _failure(what: str, error: Exception) -> str:
    """A detail with yt-dlp's error type and its text, redacted.

    The request can hold a token, so callers raise this detail without chaining the error itself.
    """
    # Only yt-dlp's text is redacted, since a URL ending in /token reads like a token's name.
    return f"{what}: {type(error).__name__}: {redact(str(error))}"


def _error_answer(error: HTTPError) -> dict[str, Any]:
    """The JSON object in an error response's body, or {} when there isn't one."""
    from yt_dlp.networking.exceptions import RequestError

    try:
        body = error.response.read()
    except RequestError as read_error:
        log.debug("Couldn't read an error response's body: %s", type(read_error).__name__)
        return {}
    finally:
        error.close()
    try:
        data = json.loads(body)
    except ValueError:
        # An error page in HTML is common, and the status alone still says what happened.
        return {}
    return data if isinstance(data, dict) else {}


def _json_object(body: bytes, where: str) -> dict[str, Any]:
    """The JSON object in a response body. Anything else means SoundCloud's API changed."""
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


def _fetch(ydl: YoutubeDL, url: str, headers: dict[str, str], data: bytes | None = None) -> bytes:
    """GET `url`, or POST `data` to it, impersonating Chrome as SoundCloud's bot protection needs.

    Returns the response body.
    """
    from yt_dlp.networking.common import Request
    from yt_dlp.networking.impersonate import ImpersonateTarget

    # A bare "chrome" target gets the newest desktop Chrome that curl_cffi offers.
    impersonate = {"impersonate": ImpersonateTarget("chrome")}
    request = Request(url, data=data, headers=headers, extensions=impersonate)
    with ydl.urlopen(request) as response:
        return response.read()
