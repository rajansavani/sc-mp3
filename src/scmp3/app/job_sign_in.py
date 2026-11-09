"""The SoundCloud sign-in as jobs use it: a current token for each step, renewed once if refused.

Once yt-dlp's Authorization header is set, it never checks the token again, so a token that
expired or was revoked shows up as an api-v2 401, which yt-dlp's errors read as an outdated
engine. A step that SoundCloud refuses renews the sign-in and runs once more.
"""

import logging
import time
from collections.abc import Callable

from scmp3.core import auth
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.guest_warning import GuestWarningWatcher
from scmp3.core.pipeline import YdlMaker
from scmp3.core.sources import soundcloud, ytdlp_errors
from scmp3.core.token_store import TokenStore

log = logging.getLogger(__name__)


def _nothing() -> None:
    """The default for on_ended, when nothing shows the account."""


class JobSignIn:
    """Hands each SoundCloud step the current token, and renews it once when SoundCloud refuses it.

    `on_ended` runs when a step fails because the sign-in ended, so the account can say so.
    """

    def __init__(
        self,
        *,
        make_ydl: YdlMaker = soundcloud.make_ydl,
        make_store: Callable[[], TokenStore] = TokenStore,
        on_ended: Callable[[], None] = _nothing,
        clock: auth.Clock = time.time,
    ) -> None:
        self._make_ydl = make_ydl
        self._make_store = make_store
        self._on_ended = on_ended
        self._clock = clock

    def stored_token(self) -> str | None:
        """The stored access token as it is, with no renewal, for restoring jobs quickly."""
        tokens = self._make_store().load()
        return tokens.access if tokens is not None else None

    def run[T](self, attempt: Callable[[str | None], T]) -> T:
        """Run a step with the current token, or None when nobody is signed in.

        A step SoundCloud refuses with a 401, or one where yt-dlp carried on as a guest, runs once
        more after a renewal. When that fails too, it raises auth_expired.
        """
        token = self._signed_in(self._current_token)
        if token is None:
            return attempt(None)
        try:
            with GuestWarningWatcher() as watcher:
                result = attempt(token)
        except ScmpError as error:
            if not ytdlp_errors.is_unauthorized(error):
                raise
            log.info("SoundCloud refused the sign-in, so it's renewed and the step runs again")
            return self._signed_in(lambda: self._run_again(attempt, token))
        if not watcher.saw_guest_warning:
            return result
        log.info("yt-dlp carried on as a guest, so the sign-in is renewed and the step runs again")
        return self._signed_in(lambda: self._run_again(attempt, token))

    def _signed_in[T](self, step: Callable[[], T]) -> T:
        """Run `step`, and tell on_ended when it fails because the sign-in ended."""
        try:
            return step()
        except ScmpError as error:
            if error.kind == ErrorKind.auth_expired:
                self._on_ended()
            raise

    def _current_token(self) -> str | None:
        with self._make_ydl() as ydl:
            return auth.current_token(ydl, store=self._make_store(), clock=self._clock)

    def _run_again[T](self, attempt: Callable[[str | None], T], refused: str) -> T:
        """Renew the refused token and run the step with the new one, as a guest no longer."""
        token = self._renewed(refused)
        if token is None:
            # Signed out while the step ran, so it runs as a guest, as it would have from the start.
            return attempt(None)
        with GuestWarningWatcher() as watcher:
            try:
                result = attempt(token)
            except ScmpError as error:
                if ytdlp_errors.is_unauthorized(error):
                    detail = "SoundCloud refused the sign-in again after it was renewed"
                    raise ScmpError(ErrorKind.auth_expired, detail) from error
                raise
        if watcher.saw_guest_warning:
            detail = "yt-dlp carried on as a guest again after the sign-in was renewed"
            raise ScmpError(ErrorKind.auth_expired, detail)
        return result

    def _renewed(self, refused: str) -> str | None:
        """A new access token in place of the refused one, or None when nobody is signed in now."""
        store = self._make_store()
        with self._make_ydl() as ydl:
            tokens = store.load()
            if tokens is None:
                return None
            if tokens.access != refused:
                # Another job renewed it while this one ran.
                return auth.current_token(ydl, store=store, clock=self._clock)
            return auth.renew(tokens, ydl, store=store).access
