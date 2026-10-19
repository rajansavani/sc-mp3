"""Turn yt-dlp's exceptions into ScmpError kinds, from their messages, types and HTTP statuses.

This covers extracting and downloading, so it also tells disk and connection failures apart.
"""

from __future__ import annotations

import http.client
import re
import socket
import ssl
from collections.abc import Iterator

from scmp3.core import engine
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import redact

# Phrases in yt-dlp's messages, lowercased, checked before anything else. T-061 adds YouTube's.
MESSAGE_KINDS: tuple[tuple[str, ErrorKind], ...] = (
    ("drm protected", ErrorKind.drm),
    ("not available from your location", ErrorKind.geo_blocked),
    ("unable to extract client id", ErrorKind.engine_outdated),
)

# What an HTTP status in a yt-dlp error means: the API meanings, except that yt-dlp sends no token
# of ours and has already retried a 401 with a fresh client_id. 5xx is service_down.
STATUS_KINDS: dict[int, ErrorKind] = {**engine.STATUS_KINDS, 401: ErrorKind.engine_outdated}

# Standard library network failures that can sit under a yt-dlp error. Some are OSErrors, so
# they're checked before an OSError is read as a disk failure.
NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    socket.gaierror,
    socket.herror,
    ssl.SSLError,
    http.client.IncompleteRead,
)

# How yt-dlp writes a status when no HTTPError object is attached.
_STATUS_IN_MESSAGE = re.compile(r"HTTP Error (\d{3})")
# Causes are followed this deep at most, in case an exception chain loops.
_MAX_CHAIN = 10


def to_scmp_error(error: BaseException) -> ScmpError:
    """A ScmpError of the right kind, with yt-dlp's message, redacted, as the detail."""
    if isinstance(error, ScmpError):
        return error
    message = str(error).removeprefix("ERROR: ")
    return ScmpError(error_kind(error), redact(message))


def error_kind(error: BaseException) -> ErrorKind:
    """What a yt-dlp exception means for the job. Anything that isn't yt-dlp's is internal."""
    chain = list(_chain(error))
    for item in chain:
        kind = _message_kind(str(item))
        if kind is not None:
            return kind
    for item in chain:
        kind = _type_kind(item)
        if kind is not None:
            return kind
    kind = _os_error_kind(error)
    if kind is not None:
        return kind
    for item in chain:
        found = _STATUS_IN_MESSAGE.search(str(item))
        if found:
            return status_kind(int(found[1]))
    if any(_is_yt_dlp_error(item) for item in chain):
        return ErrorKind.engine_outdated
    return ErrorKind.internal


def status_kind(status: int) -> ErrorKind:
    """What an HTTP error status from one of yt-dlp's requests means."""
    if 500 <= status <= 599:
        return ErrorKind.service_down
    return STATUS_KINDS.get(status, ErrorKind.engine_outdated)


def _message_kind(message: str) -> ErrorKind | None:
    lowered = message.lower()
    for phrase, kind in MESSAGE_KINDS:
        if phrase in lowered:
            return kind
    return None


def _type_kind(error: BaseException) -> ErrorKind | None:
    """The kind an exception's type says, for the types that say one."""
    engine.load_yt_dlp()
    from yt_dlp.networking.exceptions import HTTPError, TransportError
    from yt_dlp.utils import ContentTooShortError, GeoRestrictedError

    if isinstance(error, GeoRestrictedError):
        return ErrorKind.geo_blocked
    if isinstance(error, HTTPError):
        return status_kind(error.status)
    # A download that ended short of its announced size lost its connection on the way.
    if isinstance(error, (TransportError, ContentTooShortError, *NETWORK_ERRORS)):
        return ErrorKind.offline
    return None


def _os_error_kind(error: BaseException) -> ErrorKind | None:
    """offline or disk, when an OSError is behind the error, else None.

    yt-dlp raises UnavailableVideoError while handling the OSError of a failed write, so the
    OSError is only the context. Context is followed for this check alone, since elsewhere it can
    hold an error yt-dlp already recovered from, like the 401 before a client_id retry.
    """
    for item in _chain(error, follow_context=True):
        if isinstance(item, NETWORK_ERRORS):
            return ErrorKind.offline
        if isinstance(item, OSError):
            return ErrorKind.disk
    return None


def _is_yt_dlp_error(error: BaseException) -> bool:
    engine.load_yt_dlp()
    from yt_dlp.utils import YoutubeDLError

    return isinstance(error, YoutubeDLError)


def _chain(error: BaseException, *, follow_context: bool = False) -> Iterator[BaseException]:
    """The error, then what caused it: yt-dlp keeps causes in exc_info and cause attributes."""
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending and len(seen) < _MAX_CHAIN:
        item = pending.pop(0)
        if id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        pending.extend(_causes(item, follow_context=follow_context))


def _causes(error: BaseException, *, follow_context: bool) -> list[BaseException]:
    """The exceptions one error points to as its cause, and its context when asked for."""
    exc_info = getattr(error, "exc_info", None)
    candidates = [
        exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) == 3 else None,
        getattr(error, "cause", None),
        error.__cause__,
    ]
    # `raise ... from None` hides the context on purpose, so it isn't followed then.
    if follow_context and not error.__suppress_context__:
        candidates.append(error.__context__)
    return [item for item in candidates if isinstance(item, BaseException)]
