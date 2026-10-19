"""The HTTP layer for GitHub and its downloads: GETs through urllib, and failures as error kinds.

Redirects are followed only to https. Everything here takes or returns a `Response`, so tests can
pass in a fake `Opener` instead.
"""

import http.client
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from types import TracebackType
from typing import Any, Protocol, Self
from urllib.parse import urlsplit

from scmp3 import __version__
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import safe_url

# GitHub refuses API requests that don't name their client.
USER_AGENT = f"sc-mp3/{__version__}"
# Seconds a connection or a read may stall before the request fails.
TIMEOUT = 30
READ_CHUNK_BYTES = 256 * 1024


class Headers(Protocol):
    """The response headers read here. urllib's are case-insensitive."""

    def get(self, name: str, /) -> str | None: ...


class Response(Protocol):
    """An HTTP response as urllib returns it. urllib's HTTPError has the same shape."""

    status: int
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


# GETs a URL with extra headers and returns the response, whatever its status.
type Opener = Callable[[str, Mapping[str, str]], Response]


class HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follows redirects, but never from https to plain http."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if urlsplit(newurl).scheme.lower() != "https":
            fp.close()
            detail = f"{safe_url(req.full_url)} redirected to a link without https"
            raise ScmpError(ErrorKind.bad_download, detail)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# urllib's usual handlers, with redirects held to https.
_URL_OPENER = urllib.request.build_opener(HttpsOnlyRedirects)


def open_url(url: str, headers: Mapping[str, str]) -> Response:
    """GET a URL with urllib, following https redirects. An error status is returned, not raised."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        return _URL_OPENER.open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        # The error is the response, with the status and headers a 304 or a rate limit needs.
        return error


@contextmanager
def network_errors(url: str) -> Iterator[None]:
    """Turn a failed connection or read into ScmpError(offline), naming the host and path."""
    try:
        yield
    except (OSError, http.client.HTTPException) as error:
        raise ScmpError(ErrorKind.offline, f"couldn't reach {safe_url(url)}: {error}") from error


def status_error(status: int, url: str) -> ScmpError:
    """The error for an answer that isn't a success, from the API or a download server."""
    if status == 429:
        kind = ErrorKind.rate_limited
    elif 500 <= status <= 599:
        kind = ErrorKind.service_down
    else:
        kind = ErrorKind.unavailable
    return ScmpError(kind, f"HTTP {status} from {safe_url(url)}")


def read_capped(response: Response, url: str, max_bytes: int) -> bytes:
    """The whole body of a small response, refusing one over `max_bytes`."""
    chunks: list[bytes] = []
    total = 0
    while True:
        with network_errors(url):
            chunk = response.read(READ_CHUNK_BYTES)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise ScmpError(ErrorKind.service_down, f"{safe_url(url)} sent over {max_bytes} bytes")
        chunks.append(chunk)
