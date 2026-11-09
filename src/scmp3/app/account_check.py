"""The account's startup check: who the stored SoundCloud sign-in belongs to, at most once a day.

It asks sooner when the access token is close to expiring, so a sign-in that ended shows before
any job runs into it.
"""

import logging
from collections.abc import Callable
from typing import Any

from scmp3.app.account_cache import AccountCache, CachedAccount
from scmp3.core import auth
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import Account
from scmp3.core.pipeline import YdlMaker
from scmp3.core.token_store import StoreLocation, TokenStore
from scmp3.core.tokens import Tokens, read_claims

# SoundCloud is asked who is signed in at startup, at most this often.
CHECK_EVERY = 24 * 3600  # seconds

Clock = Callable[[], float]

log = logging.getLogger(__name__)


class StartupCheck:
    """Asks SoundCloud who the stored sign-in belongs to, and keeps account.json up to date."""

    def __init__(
        self,
        make_store: Callable[[], TokenStore],
        make_ydl: YdlMaker,
        cache: AccountCache,
        clock: Clock,
    ) -> None:
        self._make_store = make_store
        self._make_ydl = make_ydl
        self._cache = cache
        self._clock = clock

    def due(self, cached: CachedAccount | None, tokens: Tokens) -> bool:
        """Whether to ask SoundCloud now: once a day, or sooner when the token needs renewing."""
        if cached is None or not 0 <= self._clock() - cached.checked_at < CHECK_EVERY:
            return True
        try:
            expires_at = read_claims(tokens.access).exp
        except ScmpError:
            return True
        return expires_at - self._clock() < auth.RENEW_MARGIN

    def run(self) -> dict[str, Any] | None:
        """The account's state as SoundCloud has it now, or None to leave it as it was."""
        try:
            account, stored_in = self._stored_account()
        except ScmpError as error:
            if error.kind != ErrorKind.auth_expired:
                log.warning(
                    "Couldn't check the SoundCloud sign-in, so it's shown as before: %s", error
                )
                return None
            log.warning("The SoundCloud sign-in has ended: %s", error)
            return {"status": "ended"}
        except Exception:
            log.exception("Checking the SoundCloud sign-in failed unexpectedly")
            return None
        if account is None:
            self._cache.clear()
            return {"status": "signed_out", "account": None, "stored_in": None}
        self._cache.save(account, self._clock())
        return {"status": "signed_in", "account": account, "stored_in": stored_in}

    def _stored_account(self) -> tuple[Account | None, StoreLocation | None]:
        """The stored sign-in's account from /me, after one renewal if /me refuses the token."""
        store = self._make_store()
        with self._make_ydl() as ydl:
            token = auth.current_token(ydl, store=store, clock=self._clock)
            if token is None:
                return None, None
            try:
                return auth.validate(token, ydl), store.location
            except ScmpError as error:
                if error.kind != ErrorKind.auth_expired:
                    raise
            log.info(
                "SoundCloud refused the stored token before it was due to expire, so it's renewed"
            )
            tokens = store.load()
            if tokens is None:
                return None, None
            renewed = auth.renew(tokens, ydl, store=store)
            return auth.validate(renewed.access, ydl), store.location
