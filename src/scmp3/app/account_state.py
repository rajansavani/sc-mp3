"""The SoundCloud account as the UI shows it, with signing in and out on threads of their own.

core.auth signs in, renews and stores the tokens. This keeps what the UI needs: who is signed in,
whether the sign-in ended, what's running and why the last try failed. Each change goes out as an
`account` event.
"""

import itertools
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Literal

from scmp3.app.account_cache import AccountCache
from scmp3.app.account_check import StartupCheck
from scmp3.app.account_problems import PROFILE_LEFT, Problem, Task, problem_of
from scmp3.app.signin import SignInWindow, WindowProblem, WindowRequests, WindowSignInFailed
from scmp3.app.signin_browsers import DEFAULT_CHOICE, BrowserOptions
from scmp3.app.signin_cookies import SignInCookies
from scmp3.core import auth
from scmp3.core.models import Account
from scmp3.core.pipeline import YdlMaker
from scmp3.core.sources import soundcloud
from scmp3.core.token_store import StoreLocation, TokenStore
from scmp3.core.tokens import TokenProblem

Status = Literal["signed_out", "signed_in", "ended"]
Emit = Callable[[dict[str, Any]], None]
Runner = Callable[[Callable[[], None]], None]
Clock = Callable[[], float]

# How long closing sc-mp3 waits for a sign-in window to stop before stopping what it started.
SHUTDOWN_WAIT = 5.0  # seconds

log = logging.getLogger(__name__)


def _nothing() -> None:
    """The default for on_signed_in, when nothing waits for a sign-in."""


def _default_browser() -> str:
    """The default for browser_setting, when there are no settings to read it from."""
    return DEFAULT_CHOICE


def run_in_thread(work: Callable[[], None]) -> None:
    """Run `work` on a daemon thread, so a sign-in never holds up a bridge call or closing."""
    threading.Thread(target=work, name="scmp3-account", daemon=True).start()


class AccountState:
    """Who is signed in to SoundCloud, kept current for the UI.

    Signing in and out run through `run`, a new thread by default, and report through `emit`.
    One task runs at a time, and a second one asked for meanwhile is ignored. `on_signed_in` runs
    after each sign-in that worked, so jobs that stopped for the sign-in can carry on.
    `browser_setting` gives the browser id picked in Settings for the window.
    """

    def __init__(
        self,
        emit: Emit,
        *,
        make_ydl: YdlMaker = soundcloud.make_ydl,
        make_store: Callable[[], TokenStore] = TokenStore,
        cache: AccountCache | None = None,
        window: SignInWindow | None = None,
        clock: Clock = time.time,
        run: Runner = run_in_thread,
        on_signed_in: Callable[[], None] = _nothing,
        browser_setting: Callable[[], str] = _default_browser,
    ) -> None:
        self._emit = emit
        self._on_signed_in = on_signed_in
        self._browser_setting = browser_setting
        self._make_ydl = make_ydl
        self._make_store = make_store
        self._cache = cache or AccountCache()
        self._window = window or SignInWindow()
        self._clock = clock
        self._run = run
        self._check = StartupCheck(make_store, make_ydl, self._cache, clock)
        # Held while the state changes and while its event is queued, so events keep their order.
        self._lock = threading.Lock()
        # Told each time a task ends, for closing sc-mp3 to wait on.
        self._idle = threading.Condition(self._lock)
        self._loaded = False
        self._browsers = BrowserOptions()
        self._status: Status = "signed_out"
        self._account: Account | None = None
        self._stored_in: StoreLocation | None = None
        self._running: Task | None = None
        self._problem: Problem | None = None
        self._problem_ids = itertools.count(1)
        # Counts changes, so a startup check that finishes after a sign-in or out is dropped.
        self._version = 0
        # What the person asks of the open sign-in window, and whether they said they're done.
        self._requests = WindowRequests()
        self._finishing = False

    def view(self) -> dict[str, Any]:
        """The account as the UI gets it, in snapshots and `account` events."""
        with self._lock:
            return self._view()

    def load_once(self) -> None:
        """Read the stored sign-in the first time it's asked for, and check it when that's due."""
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
        # The snapshot being built carries them, so no event is needed.
        self._find_browsers()
        self._run(self._window.clean_up)
        store = self._make_store()
        tokens = store.load()
        if tokens is None:
            self._cache.clear()
            return
        cached = self._cache.load()
        with self._lock:
            self._status = "signed_in"
            self._account = cached.account if cached else None
            self._stored_in = store.location
        if self._check.due(cached, tokens):
            self._run(self._check_stored)

    def refresh_browsers(self) -> None:
        """Look for the browsers again and pick the window's, after a change in Settings."""
        if self._find_browsers():
            with self._lock:
                self._send()

    def _find_browsers(self) -> bool:
        """Look for the browsers and pick the window's. Returns whether anything changed."""
        options = self._window.find(self._browser_setting())
        with self._lock:
            changed = options != self._browsers
            self._browsers = options
        return changed

    def sign_in_window(self) -> None:
        """Open the sign-in window in the browser picked for it, and sign in with what it gives."""
        requests = WindowRequests(on_reading=self._window_reading)
        # The event that says the window is starting carries the browsers found here.
        self._find_browsers()
        browser = self._browsers.chosen

        def window_cookies() -> SignInCookies | None:
            if browser is None:
                raise WindowSignInFailed(WindowProblem.no_browser)
            return self._window.open(requests, browser)

        self._start("window", lambda: self._sign_in("window", window_cookies), requests)

    def sign_in_from_browser(self, access_token: str, refresh_token: str) -> None:
        """Sign in with the two values copied from a browser."""
        cookies = SignInCookies(access_token, refresh_token)
        self._start("browser", lambda: self._sign_in("browser", lambda: cookies))

    def sign_out(self) -> None:
        """Delete the stored sign-in."""
        self._start("sign_out", self._sign_out)

    def sign_in_ended(self) -> None:
        """A job found that SoundCloud ended the sign-in, so the UI offers to sign in again."""
        with self._lock:
            if self._status != "signed_in":
                return
            self._status = "ended"
            self._version += 1
            self._send()

    def cancel_sign_in(self) -> None:
        """Close the sign-in window, if it's open, without signing in."""
        with self._lock:
            self._requests.cancel.set()

    def shut_down(self) -> None:
        """Stop a sign-in window as sc-mp3 closes, so no browser it started outlives the app."""
        with self._lock:
            if self._running != "window":
                return
            requests = self._requests
            requests.cancel.set()
            # The window stops its browser at its next look at cancel, and a read takes a moment.
            stopped = self._idle.wait_for(lambda: self._running is None, SHUTDOWN_WAIT)
        if not stopped:
            log.warning("The sign-in didn't stop in time, so what it started is stopped now")
        self._window.stop(requests)

    def finish_sign_in(self) -> None:
        """The person says they've signed in, so the window closes and the sign-in is read."""
        with self._lock:
            if self._running != "window" or self._finishing:
                return
            self._requests.finish.set()
            self._finishing = True
            self._send()

    def _window_reading(self, reading: bool) -> None:
        """The window closed by itself to read the sign-in, or opened again after finding none."""
        with self._lock:
            if self._running != "window":
                return
            self._finishing = reading
            self._send()

    def _start(
        self, task: Task, work: Callable[[], None], requests: WindowRequests | None = None
    ) -> None:
        with self._lock:
            if self._running is not None:
                log.info("Ignored a SoundCloud %s while a %s runs", task, self._running)
                return
            self._running = task
            self._requests = requests or WindowRequests()
            self._problem = None
            self._version += 1
            self._send()
        self._run(work)

    def _sign_in(self, task: Task, get_cookies: Callable[[], SignInCookies | None]) -> None:
        store = self._make_store()
        try:
            cookies = get_cookies()
            if cookies is None or self._cancelled(task):
                log.info("The sign-in was cancelled")
                self._settle()
                return
            with self._make_ydl() as ydl:
                access, refresh = cookies.access_token, cookies.refresh_token
                account = auth.sign_in(access, refresh, ydl, store=store, clock=self._clock)
            if self._cancelled(task):
                # Cancelled while SoundCloud checked it, so the pair just stored goes again.
                auth.sign_out(store=store)
                log.info("The sign-in was cancelled while SoundCloud checked it")
                self._settle()
                return
        except Exception as error:
            self._fail(task, *problem_of(error, "Signing in to"))
            return
        self._cache.save(account, self._clock())
        log.info("Signed in to SoundCloud, with the sign-in kept in the %s", store.location)
        self._settle(status="signed_in", account=account, stored_in=store.location)
        try:
            self._on_signed_in()
        except Exception:
            # The sign-in itself worked, so it stands; the jobs can still be retried by hand.
            log.exception("Retrying the jobs that stopped for the sign-in failed")

    def _cancelled(self, task: Task) -> bool:
        """Whether the person cancelled the window, even during its read."""
        with self._lock:
            return task == "window" and self._requests.cancel.is_set()

    def _sign_out(self) -> None:
        try:
            auth.sign_out(store=self._make_store())
        except Exception as error:
            self._fail("sign_out", *problem_of(error, "Signing out of"))
            return
        self._cache.clear()
        signed_out = {"status": "signed_out", "account": None, "stored_in": None}
        if not self._forget_window_profiles():
            # The tokens are gone, so this is signed out, but Sign out stays for the profile.
            self._fail("sign_out", PROFILE_LEFT, **signed_out)
            return
        log.info("Signed out of SoundCloud")
        self._settle(**signed_out)

    def _forget_window_profiles(self) -> bool:
        """Delete the window's kept profiles, with the Google session. False when one stays."""
        try:
            forgotten = self._window.forget()
        except Exception:
            log.exception("Deleting the sign-in window's profiles failed")
            return False
        if not forgotten:
            log.warning("Signed out of SoundCloud, but a browser profile for signing in stayed")
        return forgotten

    def _check_stored(self) -> None:
        """Ask SoundCloud who the stored sign-in belongs to, as the daily startup check."""
        with self._lock:
            version = self._version
        changes = self._check.run()
        if changes is not None:
            self._settle_check(version, **changes)

    def _settle(self, **changes: Any) -> None:
        """End the running task with these changes to the state."""
        with self._lock:
            self._apply(changes)
            self._end_task()

    def _settle_check(self, version: int, **changes: Any) -> None:
        """Apply a startup check's outcome, unless a sign-in or out came after it started."""
        with self._lock:
            if self._version != version:
                log.debug("The account changed during the startup check, so its outcome is dropped")
                return
            self._apply(changes)
            self._send()

    def _fail(
        self, task: Task, kind: str, reason: TokenProblem | None = None, **changes: Any
    ) -> None:
        """End the running task with a problem, and with these changes to the state."""
        with self._lock:
            self._problem = Problem.of(next(self._problem_ids), task, kind, reason)
            self._apply(changes)
            self._end_task()

    def _end_task(self) -> None:
        """Called under the lock: no task runs now, and anyone waiting for that is told."""
        self._running = None
        self._finishing = False
        self._version += 1
        self._send()
        self._idle.notify_all()

    def _apply(self, changes: dict[str, Any]) -> None:
        self._status = changes.get("status", self._status)
        self._account = changes.get("account", self._account)
        self._stored_in = changes.get("stored_in", self._stored_in)

    def _view(self) -> dict[str, Any]:
        return {
            "status": self._status,
            **self._browsers.to_dict(),
            "window_closes_itself": self._window.closes_by_itself,
            # A kept profile holds a Google session, so Sign out is offered while one is there.
            "window_profile": self._window.has_profile(),
            "user": self._account.to_dict() if self._account else None,
            "stored_in": self._stored_in,
            "running": self._running,
            "finishing": self._finishing,
            "problem": self._problem.to_dict() if self._problem else None,
        }

    def _send(self) -> None:
        """Queue the state as an event. Called under the lock, which the bus never waits on."""
        self._emit({"type": "account", "account": self._view()})
