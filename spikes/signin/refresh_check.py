"""Renews an access token with a refresh token, the way the web app's proxy does server-side.

The web app POSTs to a same-origin proxy that reads the HttpOnly refresh cookie and calls
`secure.soundcloud.com/oauth/token` with `grant_type=refresh_token`. The app can't use that proxy,
so this calls the token endpoint directly with the refresh token read from the window's cookies.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from redact import error_details
from token_info import access_token_client_id, lifetime_lines, read_jwt
from yt_dlp import YoutubeDL
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError, RequestError

TOKEN_URL = "https://secure.soundcloud.com/oauth/token"

# A sender takes the form body and returns (status, body bytes), or raises RefreshTransportError.
Sender = Callable[[bytes], tuple[int, bytes]]


class RefreshTransportError(Exception):
    """A renewal request that never got an HTTP response."""


@dataclass
class RefreshCall:
    """One renewal request, with no token values."""

    label: str
    status: int | None = None
    error: str = ""
    field_names: list[str] = field(default_factory=list)
    token_type: str = ""
    scope_present: bool = False
    expires_in: int | None = None
    access_token_header: str = ""
    access_lifetime: list[str] = field(default_factory=list)
    # Whether the response had these fields at all, not whether the values are new.
    access_token_returned: bool = False
    refresh_token_returned: bool = False
    # True or False only when a refresh token came back: does it differ from the one sent?
    refresh_token_changed: bool | None = None
    # OAuth error codes such as invalid_grant or invalid_client, redacted. Never token values.
    error_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Tokens:
    """The tokens a renewal returned, kept apart so they never reach output."""

    access: str | None = None
    refresh: str | None = None


def refresh_once(
    label: str, client_id: str, refresh_token: str, send: Sender
) -> tuple[RefreshCall, _Tokens]:
    """Send one `grant_type=refresh_token` request and record everything but the token values."""
    body = urlencode(
        {"client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token}
    ).encode()
    call = RefreshCall(label)
    tokens = _Tokens()
    try:
        status, raw = send(body)
    except RefreshTransportError as error:
        # The message is built from fixed text and a type name, so it carries no token.
        call.error = str(error)
        return call, tokens
    call.status = status
    _fill_from_payload(call, tokens, _parse_json(raw))
    if tokens.refresh is not None:
        call.refresh_token_changed = tokens.refresh != refresh_token
    return call, tokens


def run_refresh_test(
    client_id: str,
    refresh_token: str,
    send: Sender | None = None,
    max_calls: int = 3,
    source: str = "from the box",
) -> tuple[list[RefreshCall], list[str]]:
    """Renew, renew again with the token that came back, then reuse the original one.

    The original is reused last: servers that detect reuse revoke the whole token family, which
    would make the returned token look broken if it were tried after. Makes at most `max_calls`
    calls, three at most. Returns the calls and every token value seen, for masking.
    """
    send = send or make_ytdlp_sender()
    calls: list[RefreshCall] = []
    secrets: list[str] = [refresh_token]
    if max_calls < 1:
        return calls, secrets
    first, returned = refresh_once(
        f"renew with the refresh token {source}", client_id, refresh_token, send
    )
    calls.append(first)
    _remember(secrets, returned)
    if first.status != 200 or returned.refresh is None or len(calls) >= max_calls:
        return calls, secrets
    second, again = refresh_once(
        "renew with the refresh token the first renewal returned", client_id, returned.refresh, send
    )
    calls.append(second)
    _remember(secrets, again)
    # Reusing the original says something only if the server handed back a different token.
    if not first.refresh_token_changed or len(calls) >= max_calls:
        return calls, secrets
    reuse, reused = refresh_once(
        f"reuse the original refresh token {source} (a failure means single-use)",
        client_id,
        refresh_token,
        send,
    )
    calls.append(reuse)
    _remember(secrets, reused)
    return calls, secrets


def make_ytdlp_sender() -> Sender:
    """A sender that POSTs to the token endpoint through yt-dlp's opener."""
    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

    def send(body: bytes) -> tuple[int, bytes]:
        with YoutubeDL({"logger": _QuietLogger(), "cachedir": False}) as ydl:
            try:
                response = ydl.urlopen(Request(TOKEN_URL, data=body, headers=headers))
                # Read inside the try, so a failed read is reported like a failed request.
                return response.status, response.read()
            except HTTPError as error:
                # A 4xx still names the error fields; its body never carries a token.
                return error.status, _read_error_body(error)
            except RequestError as error:
                raise RefreshTransportError(f"request failed: {type(error).__name__}") from None

    return send


def _read_error_body(error: HTTPError) -> bytes:
    try:
        return error.response.read()
    except RequestError:
        return b""


def _fill_from_payload(call: RefreshCall, tokens: _Tokens, payload: Any) -> None:
    if not isinstance(payload, dict):
        call.error = "the response wasn't a JSON object"
        return
    call.field_names = sorted(payload)
    if call.status != 200:
        call.error_fields = error_details(payload)
    call.token_type = str(payload.get("token_type", ""))
    call.scope_present = "scope" in payload
    call.expires_in = (
        payload.get("expires_in") if isinstance(payload.get("expires_in"), int) else None
    )
    tokens.access = _as_str(payload.get("access_token"))
    tokens.refresh = _as_str(payload.get("refresh_token"))
    call.access_token_returned = tokens.access is not None
    call.refresh_token_returned = tokens.refresh is not None
    if tokens.access:
        _describe_access_token(call, tokens.access)


def _describe_access_token(call: RefreshCall, access: str) -> None:
    facts = read_jwt(access)
    if facts is None:
        return
    call.access_token_header = f"alg={facts.alg}, typ={facts.typ}"
    call.access_lifetime = lifetime_lines(facts, int(time.time()))


def _remember(secrets: list[str], tokens: _Tokens) -> None:
    secrets.extend(token for token in (tokens.access, tokens.refresh) if token)


def _parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class _QuietLogger:
    """Swallows yt-dlp's log lines, so a refresh request can't print a token."""

    def debug(self, message: str) -> None: ...

    def info(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def error(self, message: str) -> None: ...


def compare_client(access_token: str, web_client_id: str) -> tuple[str, str | None]:
    """Compare the access token's client_id claim with the web client_id.

    Returns a verdict safe to print ("same", "different" or "unknown") and the claim's client_id,
    which the caller uses to form a request but must never print.
    """
    claim = access_token_client_id(access_token)
    if claim is None:
        return "unknown", None
    return ("same" if claim == web_client_id else "different"), claim
