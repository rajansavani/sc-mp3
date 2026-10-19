"""SoundCloud sign-in flows: signing in and out, renewing the 24-hour token, reading the account.

tokens has the token rules and token_store keeps them. The sign-in window lives in app/.
TECH_SPEC §13 has the design.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from scmp3.core import engine
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import register_secret
from scmp3.core.models import Account, account_tier
from scmp3.core.token_store import TokenStore
from scmp3.core.tokens import Tokens, is_text, normalize_access, normalize_refresh, read_claims

if TYPE_CHECKING:
    from yt_dlp import YoutubeDL

TOKEN_URL = "https://secure.soundcloud.com/oauth/token"
ME_URL = "https://api-v2.soundcloud.com/me"

# A job never starts with an access token that has less than this left.
RENEW_MARGIN = 600  # seconds

# OAuth answers errors with 400 or 401, and the error code in the body says which one it is.
TOKEN_STATUS_KINDS = {400: ErrorKind.auth_expired, 401: ErrorKind.auth_expired}
ME_STATUS_KINDS = {401: ErrorKind.auth_expired}
# The OAuth error code that means the refresh token is dead, so the sign-in has ended.
ENDED_CODE = "invalid_grant"
_ERROR_CODE = re.compile(r"[a-z_]{1,64}")

Clock = Callable[[], float]

log = logging.getLogger(__name__)


class SignInEnded(ScmpError):
    """The sign-in is over: SoundCloud refused the refresh token, or someone signed out."""

    def __init__(self, detail: str) -> None:
        super().__init__(ErrorKind.auth_expired, detail)


def sign_in(
    access_token: str,
    refresh_token: str,
    ydl: YoutubeDL,
    *,
    store: TokenStore | None = None,
    clock: Clock = time.time,
) -> Account:
    """Check a new sign-in with /me, store it and return the account.

    Takes the two values as pasted or read from the window. Raises TokenRefused when one has the
    wrong shape. An access token that is about to expire is renewed first.
    """
    store = store or TokenStore()
    access = normalize_access(access_token)
    refresh = normalize_refresh(refresh_token)
    tokens = Tokens(access=access, refresh=refresh, client_id=read_claims(access).client_id)
    # Both locks come before any request, so a busy lock raises offline before anything is spent
    # or saved, and no renewal here or in another process can land in between.
    with store.exclusive(must_hold=True):
        TokenStore.try_keyring_again()
        if _expires_at(tokens) - clock() < RENEW_MARGIN:
            tokens = _request_new_pair(tokens, ydl)
            # Stored before anything else, since the refresh token that was given is now spent.
            store.save(tokens)
        account = validate(tokens.access, ydl)
        store.save(tokens)
        _use_in_ytdlp(tokens.access)
    return account


def current_token(
    ydl: YoutubeDL, *, store: TokenStore | None = None, clock: Clock = time.time
) -> str | None:
    """A valid access token, renewed first when it expires within 10 minutes.

    Returns None when nobody is signed in. yt-dlp's header is set to match, so a renewal or a
    sign-out in another sc-mp3 process reaches this one by its next job. `ydl` only carries the
    renewal request.
    """
    store = store or TokenStore()
    with store.lock:
        token = _usable_token(store, ydl, clock)
        _point_ytdlp_at(token)
    return token


def _usable_token(store: TokenStore, ydl: YoutubeDL, clock: Clock) -> str | None:
    """The stored access token, renewed when it's close to expiring."""
    tokens = store.load()
    if tokens is None:
        return None
    expires_at = _expires_at(tokens)
    if expires_at - clock() >= RENEW_MARGIN:
        return tokens.access
    try:
        return renew(tokens, ydl, store=store).access
    except SignInEnded:
        raise
    except ScmpError as error:
        # Measured again, since a renewal can take as long as the 30-second socket timeout.
        if expires_at - clock() <= 0:
            raise
        # Only an ended sign-in stops a job while the token still works, so a 503 or a refused
        # request waits until the token expires.
        log.warning("Couldn't renew the SoundCloud sign-in early, so it's used as is: %s", error)
        return tokens.access


def renew(tokens: Tokens, ydl: YoutubeDL, *, store: TokenStore | None = None) -> Tokens:
    """Trade the stored refresh token for a new pair, store the pair and return it.

    It runs under the store's lock and a lock file that other sc-mp3 processes share, so no two
    jobs spend the same refresh token: one that waited gets the pair the first one stored. When
    the sign-in has ended, the stored tokens are deleted and SignInEnded is raised.
    """
    store = store or TokenStore()
    with store.exclusive(must_hold=True):
        # Loaded only now, since another process may have renewed while this one waited.
        stored = store.load()
        if stored is None:
            _clear_ytdlp_header()
            raise SignInEnded("nobody is signed in to SoundCloud any more")
        if stored.access != tokens.access:
            log.debug("Another job already renewed the SoundCloud sign-in")
            _use_in_ytdlp(stored.access)
            return stored
        try:
            new = _request_new_pair(stored, ydl)
        except SignInEnded:
            _forget_ended_sign_in(store)
            raise
        store.save(new)
        # Under the lock, so a sign-out that waited for it clears this header, not an older one.
        _use_in_ytdlp(new.access)
    changed = "changed" if new.refresh != stored.refresh else "stayed the same"
    log.info("Renewed the SoundCloud sign-in, and the refresh token %s", changed)
    return new


def _request_new_pair(tokens: Tokens, ydl: YoutubeDL) -> Tokens:
    """Ask SoundCloud's token endpoint for a new pair. Stores nothing.

    Raises SignInEnded when the refresh token is refused with invalid_grant.
    """
    form = {
        "grant_type": "refresh_token",
        # The access token's own client_id, since the web client_id yt-dlp finds gets invalid_grant.
        "client_id": tokens.client_id,
        "refresh_token": tokens.refresh,
    }
    try:
        answer = engine.api_post_form(ydl, TOKEN_URL, form, status_kinds=TOKEN_STATUS_KINDS)
    except engine.HttpStatusError as error:
        code = _error_code(error.answer)
        if code == ENDED_CODE:
            raise SignInEnded(f"SoundCloud refused the refresh token ({ENDED_CODE})") from error
        log.warning("Renewing the SoundCloud sign-in failed: %s, error code %s", error, code)
        raise
    return _pair_from_answer(answer, tokens)


def _pair_from_answer(answer: Mapping[str, Any], old: Tokens) -> Tokens:
    """The pair in a renewal's answer. A missing refresh token means the old one still works."""
    access = answer.get("access_token")
    refresh = answer.get("refresh_token")
    for value in (access, refresh):
        if isinstance(value, str):
            register_secret(value)
    if not is_text(access):
        raise ScmpError(ErrorKind.engine_outdated, "the token endpoint sent no access token")
    if not is_text(refresh):
        # OAuth lets a server keep the refresh token as it is by sending none (RFC 6749 §6).
        refresh = old.refresh
    # A refresh token stays bound to the client it was issued to, so the client_id carries over.
    return Tokens(access=access, refresh=refresh, client_id=old.client_id)


def _error_code(answer: Mapping[str, Any]) -> str | None:
    """The OAuth error code in an error answer, when it looks like one."""
    code = answer.get("error")
    if isinstance(code, str) and _ERROR_CODE.fullmatch(code):
        return code
    return None


def _expires_at(tokens: Tokens) -> float:
    """When the access token expires. One that can't be read counts as long expired."""
    try:
        return read_claims(tokens.access).exp
    except ScmpError as error:
        log.warning("The stored access token can't be read, so it's renewed: %s", error)
        return 0.0


def _forget_ended_sign_in(store: TokenStore) -> None:
    """Delete an ended sign-in. A failure is only logged, since the ending is the news."""
    _clear_ytdlp_header()
    log.warning("SoundCloud ended the sign-in, so the stored tokens are deleted")
    try:
        store.delete()
    except ScmpError as error:
        log.warning("Couldn't delete the ended sign-in: %s", error)


def sign_out(*, store: TokenStore | None = None) -> None:
    """Delete both stored entries and clear the Authorization header yt-dlp keeps.

    It waits for a renewal here or in another process to finish, so the renewal can't put the
    sign-in back. Signing out must always work, so it goes ahead if that renewal takes too long.
    """
    store = store or TokenStore()
    with store.exclusive(must_hold=False):
        TokenStore.try_keyring_again()
        try:
            store.delete()
        finally:
            _clear_ytdlp_header()


def validate(token: str, ydl: YoutubeDL) -> Account:
    """The account an access token belongs to, from /me. A 401 raises ScmpError(auth_expired).

    Only the name, picture and plan are read. /me also holds the email address, real name and
    birth date, so its answer is never logged.
    """
    register_secret(token)
    headers = {"Authorization": f"OAuth {token}"}
    me = engine.api_get_json(ydl, ME_URL, headers, status_kinds=ME_STATUS_KINDS)
    username = me.get("username")
    permalink = me.get("permalink")
    if not (is_text(username) and is_text(permalink)):
        raise ScmpError(ErrorKind.engine_outdated, "/me didn't name the account")
    avatar_url = me.get("avatar_url")
    return Account(
        username=username,
        permalink=permalink,
        avatar_url=avatar_url if is_text(avatar_url) else None,
        tier=account_tier(_product_id(me)),
    )


def _product_id(me: Mapping[str, Any]) -> str | None:
    """consumer_subscription.product.id from /me, or None when a part is missing."""
    subscription = me.get("consumer_subscription")
    product = subscription.get("product") if isinstance(subscription, dict) else None
    product_id = product.get("id") if isinstance(product, dict) else None
    return product_id if isinstance(product_id, str) else None


def _ytdlp_headers() -> dict[str, str]:
    """The headers yt-dlp's SoundCloud extractors send, shared by every YoutubeDL in the process.

    They sit on the extractors' base class, so a new YoutubeDL with another token keeps the old
    header (TECH_SPEC §6).
    """
    engine.load_yt_dlp()
    from yt_dlp.extractor.soundcloud import SoundcloudBaseIE

    return SoundcloudBaseIE._HEADERS


def _use_in_ytdlp(access_token: str) -> None:
    """Make yt-dlp send this access token from now on, in jobs already running too."""
    _ytdlp_headers()["Authorization"] = f"OAuth {access_token}"


def _clear_ytdlp_header() -> None:
    _ytdlp_headers().pop("Authorization", None)


def _point_ytdlp_at(token: str | None) -> None:
    """Make yt-dlp send this access token, or none when nobody is signed in."""
    if token is None:
        _clear_ytdlp_header()
        return
    _use_in_ytdlp(token)
