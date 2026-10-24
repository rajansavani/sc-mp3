"""Turn yt-dlp's exceptions into ScmpError kinds, from their messages, types and HTTP statuses.

This covers extracting and downloading, so it also tells disk and connection failures apart.
"""

from __future__ import annotations

import http.client
import re
import socket
import ssl
from collections.abc import Iterator
from dataclasses import dataclass

from scmp3.core import engine
from scmp3.core.errors import ErrorKind, ErrorReason, ScmpError
from scmp3.core.log import redact


@dataclass(frozen=True)
class MessageCase:
    """A phrase in yt-dlp's messages, lowercased, and what a message with it means."""

    phrase: str
    kind: ErrorKind
    reason: ErrorReason | None = None


# Checked before anything else, in this order, so a more exact case comes before a general one.
# YouTube's phrases are its playability messages, which yt-dlp passes on.
MESSAGE_CASES: tuple[MessageCase, ...] = (
    MessageCase("drm protected", ErrorKind.drm),
    MessageCase("not available from your location", ErrorKind.geo_blocked),
    MessageCase("not made this video available in your country", ErrorKind.geo_blocked),
    MessageCase("blocked it in your country", ErrorKind.geo_blocked),
    MessageCase("unable to extract client id", ErrorKind.engine_outdated),
    MessageCase("not a bot", ErrorKind.bot_check),
    MessageCase("captcha", ErrorKind.bot_check),
    MessageCase("your ip is likely being blocked", ErrorKind.bot_check),
    MessageCase("rate-limited by youtube", ErrorKind.rate_limited),
    MessageCase("content isn't available, try again later", ErrorKind.rate_limited),
    MessageCase("confirm your age", ErrorKind.unavailable, "age_restricted"),
    MessageCase("inappropriate for some users", ErrorKind.unavailable, "age_restricted"),
    MessageCase("age-restricted", ErrorKind.unavailable, "age_restricted"),
    MessageCase("members-only", ErrorKind.unavailable, "members_only"),
    MessageCase("members on level", ErrorKind.unavailable, "members_only"),
    MessageCase("requires payment", ErrorKind.unavailable, "paid"),
    MessageCase("private video", ErrorKind.unavailable, "private"),
    MessageCase("video is private", ErrorKind.unavailable, "private"),
    MessageCase("live event will begin", ErrorKind.unavailable, "live_stream"),
    MessageCase("premieres in", ErrorKind.unavailable, "live_stream"),
    MessageCase("premiere will begin", ErrorKind.unavailable, "live_stream"),
    # Last of YouTube's, since its message for a private video can start the same way.
    MessageCase("video unavailable", ErrorKind.unavailable, "removed"),
    MessageCase("video is unavailable", ErrorKind.unavailable, "removed"),
    MessageCase("video is not available", ErrorKind.unavailable, "removed"),
    MessageCase("isn't available anymore", ErrorKind.unavailable, "removed"),
    MessageCase("no longer available", ErrorKind.unavailable, "removed"),
    MessageCase("recording is not available", ErrorKind.unavailable, "removed"),
    MessageCase("this video has been removed", ErrorKind.unavailable, "removed"),
)
# Typographic apostrophes, as in YouTube's "you're" written with U+2019, count as straight ones.
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'"})

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
    """A ScmpError of the right kind and reason, with yt-dlp's message, redacted, as the detail."""
    if isinstance(error, ScmpError):
        return error
    message = redact(str(error).removeprefix("ERROR: "))
    case = message_case(error)
    if case is not None:
        return ScmpError(case.kind, message, reason=case.reason)
    return ScmpError(error_kind(error), message)


def message_case(error: BaseException) -> MessageCase | None:
    """The first case whose phrase is in the message of the error or of one of its causes."""
    for item in _chain(error):
        case = _message_case(str(item))
        if case is not None:
            return case
    return None


def error_kind(error: BaseException) -> ErrorKind:
    """What a yt-dlp exception means for the job. Anything that isn't yt-dlp's is internal."""
    case = message_case(error)
    if case is not None:
        return case.kind
    chain = list(_chain(error))
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


def _message_case(message: str) -> MessageCase | None:
    lowered = message.lower().translate(_APOSTROPHES)
    return next((case for case in MESSAGE_CASES if case.phrase in lowered), None)


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
