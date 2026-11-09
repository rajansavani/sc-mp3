"""Made-up SoundCloud tokens and fake keyrings, shared by the sign-in tests.

The test files load this by path, since pytest's importlib mode can't import it by name.
"""

import base64
import io
import json
import secrets
from collections.abc import Callable
from typing import Any, Self

from keyring.errors import PasswordDeleteError
from yt_dlp.networking.common import Request, Response
from yt_dlp.networking.exceptions import HTTPError

from scmp3.core.token_store import TokenStore
from scmp3.core.tokens import Tokens

NOW = 1_800_000_000.0
HOUR = 3600
JWT_HEADER = {"alg": "RS256", "typ": "at+JWT", "kid": "test"}


def clock() -> float:
    return NOW


def b64url(data: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def jwt_with(claims: Any, length: int = 861) -> str:
    """A made-up JWT with these claims and a random signature, `length` characters long."""
    head = f"{b64url(JWT_HEADER)}.{b64url(claims)}."
    return head + random_text(length - len(head))


def random_text(length: int) -> str:
    """Random base64url characters, so every made-up token is different."""
    return base64.urlsafe_b64encode(secrets.token_bytes(length)).decode().rstrip("=")[:length]


def make_access(exp: float = NOW + HOUR, client_id: str = "", length: int = 861) -> str:
    """A made-up access token shaped like SoundCloud's."""
    claims = {
        "aud": "soundcloud",
        "client_id": client_id or claim_client_id(),
        "exp": int(exp),
        "iat": int(exp) - 24 * HOUR,
        "sub": "soundcloud:users:1",
    }
    return jwt_with(claims, length)


def make_refresh() -> str:
    """A made-up refresh token: 32 letters and digits, like SoundCloud's."""
    return secrets.token_hex(16)


def claim_client_id() -> str:
    return secrets.token_hex(16)


def make_tokens(exp: float = NOW + HOUR, length: int = 861) -> Tokens:
    client_id = claim_client_id()
    return Tokens(make_access(exp, client_id, length), make_refresh(), client_id)


def signed_in(store: TokenStore, exp: float = NOW + HOUR) -> Tokens:
    tokens = make_tokens(exp)
    store.save(tokens)
    return tokens


class FakeKeyring:
    """An in-memory keyring backend, so no test touches the real one."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.entries:
            raise PasswordDeleteError("not found")
        del self.entries[(service, username)]


class CredError(Exception):
    """Stands in for pywintypes.error, which keyring's Windows backend lets through."""


# Windows Credential Manager's limit for one entry, which keyring fills with UTF-16.
CREDENTIAL_BLOB_LIMIT = 2560  # bytes


class CredentialManagerKeyring(FakeKeyring):
    """Refuses entries over Credential Manager's limit, as keyring's Windows backend does."""

    def set_password(self, service: str, username: str, password: str) -> None:
        if len(password.encode("utf-16-le")) > CREDENTIAL_BLOB_LIMIT:
            raise CredError(1783, "CredWrite", "The stub received bad data.")
        super().set_password(service, username, password)


class CountingKeyring:
    """Passes every call on to another backend, and counts them."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0

    def get_password(self, service: str, username: str) -> str | None:
        self.calls += 1
        return self.inner.get_password(service, username)

    def set_password(self, service: str, username: str, password: str) -> None:
        self.calls += 1
        self.inner.set_password(service, username, password)

    def delete_password(self, service: str, username: str) -> None:
        self.calls += 1
        self.inner.delete_password(service, username)


class BrokenKeyring:
    """A keyring backend whose every call raises `error`."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_password(self, service: str, username: str) -> str | None:
        raise self.error

    def set_password(self, service: str, username: str, password: str) -> None:
        raise self.error

    def delete_password(self, service: str, username: str) -> None:
        raise self.error


Outcome = bytes | Exception | Callable[[Request], bytes]

TOKEN_URL = "https://secure.soundcloud.com/oauth/token"
ME_URL = "https://api-v2.soundcloud.com/me"


class FakeYdl:
    """Answers urlopen with canned outcomes in order, and records each request.

    It's also its own context manager and maker, so `with make_ydl() as ydl` gets it back.
    """

    def __init__(self, *outcomes: Outcome) -> None:
        self.requests: list[Request] = []
        self.made = 0
        self._outcomes = list(outcomes)

    def __call__(self, token: str | None = None, **options: Any) -> Self:
        self.made += 1
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def answer(self, *outcomes: Outcome) -> None:
        """Queue more outcomes after the ones still waiting."""
        self._outcomes.extend(outcomes)

    def urlopen(self, request: Request) -> io.BytesIO:
        self.requests.append(request)
        assert self._outcomes, f"an unexpected request to {request.url}"
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return io.BytesIO(outcome(request))
        return io.BytesIO(outcome)


def token_answer(access: str, refresh: str | None = None) -> bytes:
    """The token endpoint's answer to a renewal, as seen in T-003."""
    answer: dict[str, Any] = {"access_token": access, "expires_in": 86399, "token_type": "Bearer"}
    if refresh is not None:
        answer["refresh_token"] = refresh
    return json.dumps(answer).encode()


def me_answer(product_id: str | None = "free", **fields: Any) -> bytes:
    """A /me answer for a made-up account, with private fields like the real one's."""
    answer: dict[str, Any] = {
        "username": "Kaia",
        "permalink": "kaia-music",
        "avatar_url": "https://i1.sndcdn.com/avatars-000-abc-large.jpg",
        "consumer_subscription": {"product": {"id": product_id}},
        "primary_email": "kaia@example.com",
        "full_name": "Kaia Example",
        **fields,
    }
    return json.dumps(answer).encode()


def oauth_error(code: str, status: int = 400) -> HTTPError:
    body = json.dumps({"error": code, "error_description": "made up"}).encode()
    return HTTPError(Response(io.BytesIO(body), TOKEN_URL, {}, status=status))


def status_error(status: int, url: str = ME_URL) -> HTTPError:
    return HTTPError(Response(io.BytesIO(b""), url, {}, status=status))
