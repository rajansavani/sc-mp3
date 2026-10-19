"""SoundCloud's two sign-in tokens: their shapes, the access token's claims, and pasted values.

Pure rules with no I/O. token_store keeps the tokens, and auth runs the sign-in flows.
"""

import base64
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeGuard

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import register_secret

# A refresh token is 32 characters, the same length as a client_id.
REFRESH_LENGTH = 32
# A JWT is three base64url parts joined by dots, of which the last may be empty.
_JWT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
# The refresh token lives in a cookie, so it uses RFC 6265's cookie characters, within loose bounds
# that a format change on SoundCloud's side won't break.
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]{16,4096}")
_COOKIE_NAME = re.compile(r"(?:oauth_token|oauth_refresh_token)\s*=", re.IGNORECASE)
# At the end too, so a scheme with no token after it leaves nothing.
_AUTH_SCHEME = re.compile(r"(?:OAuth|Bearer)(?:\s+|$)", re.IGNORECASE)
_QUOTES = "\"'"


@dataclass(frozen=True)
class Tokens:
    """One SoundCloud sign-in. The values stay out of repr, so they can't reach a log that way."""

    access: str = field(repr=False)  # the access token, a JWT that lasts 24 hours
    refresh: str = field(repr=False)  # the refresh token that renews it
    client_id: str = field(repr=False)  # the access token's client_id claim, which renewing needs


@dataclass(frozen=True)
class Claims:
    """The access token's claims that renewing needs."""

    exp: float  # when the token expires, in seconds since the epoch
    client_id: str = field(repr=False)


class TokenProblem(StrEnum):
    """Why a pasted value was refused. The UI picks the words."""

    access_empty = "access_empty"
    refresh_empty = "refresh_empty"
    access_is_refresh = "access_is_refresh"  # a refresh token in the access token's field
    refresh_is_access = "refresh_is_access"  # an access token in the refresh token's field
    access_not_token = "access_not_token"  # not a JWT, so not an access token at all
    refresh_not_token = "refresh_not_token"  # can't be a cookie value, so not a refresh token


class TokenRefused(ValueError):
    """A pasted value can't be the token its field asks for. Never holds the value."""

    def __init__(self, problem: TokenProblem) -> None:
        super().__init__(f"refused a pasted token: {problem}")
        self.problem = problem


def read_claims(access_token: str) -> Claims:
    """Decode the JWT's payload for `exp` and `client_id`, without checking the signature.

    The claims only time and form a renewal, so nothing is trusted because of them.
    """
    register_secret(access_token)
    parts = access_token.split(".")
    if len(parts) != 3:
        raise ScmpError(ErrorKind.auth_expired, "the access token isn't a JWT")
    claims = _decode_json_part(parts[1])
    if not isinstance(claims, dict):
        raise ScmpError(ErrorKind.auth_expired, "the access token's claims can't be read")
    exp = claims.get("exp")
    client_id = claims.get("client_id")
    # bool is an int in Python, but never a time.
    if not isinstance(exp, int | float) or isinstance(exp, bool):
        raise ScmpError(ErrorKind.auth_expired, "the access token has no exp claim")
    if not is_text(client_id):
        raise ScmpError(ErrorKind.auth_expired, "the access token has no client_id claim")
    register_secret(client_id)
    return Claims(exp=float(exp), client_id=client_id)


def _decode_json_part(part: str) -> Any:
    """One base64url part of a JWT decoded as JSON, or None when it doesn't decode."""
    padded = part + "=" * (-len(part) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        # Bad base64 and bad JSON both land here, and the caller says in plain words what's wrong.
        return None


def normalize_access(value: str) -> str:
    """The access token in pasted text, without quotes, an auth scheme or a cookie name.

    Raises TokenRefused when nothing is left, when it has the refresh token's length, or when
    it isn't a JWT.
    """
    token = _unwrap(value)
    if not token:
        raise TokenRefused(TokenProblem.access_empty)
    register_secret(token)
    if len(token) == REFRESH_LENGTH:
        raise TokenRefused(TokenProblem.access_is_refresh)
    if not _JWT.fullmatch(token):
        raise TokenRefused(TokenProblem.access_not_token)
    return token


def normalize_refresh(value: str) -> str:
    """The refresh token in pasted text, cleaned up as in normalize_access.

    Raises TokenRefused when nothing is left, when it's a JWT like the access token, or when it
    can't be a cookie value.
    """
    token = _unwrap(value)
    if not token:
        raise TokenRefused(TokenProblem.refresh_empty)
    register_secret(token)
    if _JWT.fullmatch(token):
        raise TokenRefused(TokenProblem.refresh_is_access)
    if not _COOKIE_VALUE.fullmatch(token):
        raise TokenRefused(TokenProblem.refresh_not_token)
    return token


def _unwrap(value: str) -> str:
    """Drop whitespace and quotes, then a cookie name, then an auth scheme, as they wrap a token."""
    token = _trim(value)
    token = _trim(_drop_prefix(_COOKIE_NAME, token))
    return _trim(_drop_prefix(_AUTH_SCHEME, token))


def _trim(text: str) -> str:
    return text.strip().strip(_QUOTES).strip()


def _drop_prefix(prefix: re.Pattern[str], text: str) -> str:
    match = prefix.match(text)
    return text[match.end() :] if match else text


def is_text(value: object) -> TypeGuard[str]:
    """True for a string that isn't empty, as every token and account field must be."""
    return isinstance(value, str) and bool(value)
