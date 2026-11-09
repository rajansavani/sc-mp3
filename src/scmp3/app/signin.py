"""The sign-in window: SoundCloud in a separate browser window, and the cookies it leaves.

It runs in two steps, as T-043 found Google allows. First a plain Chromium-based browser, such as
Edge or Brave, on the profile sc-mp3 keeps for it, with no DevTools port and nothing connected.
The person signs in with Google, Apple or email, and the window closes by itself once SoundCloud
shows a signed-in page, or when the person says they're done. Then signin_read reads the two
cookies headless on the same profile. core.auth checks and stores them.
"""

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from scmp3.app import browser_windows, chromium
from scmp3.app.signin_browsers import Browser, BrowserOptions, find_browsers
from scmp3.app.signin_cookies import SignInCookies
from scmp3.app.signin_read import read_sign_in
from scmp3.app.started_browsers import StartedBrowsers
from scmp3.core.errors import ScmpError

# Only a signed-in person reaches this page. SoundCloud sends anyone else to sign in, then back.
START_URL = "https://soundcloud.com/settings"
# The titles of pages only a signed-in person sees, the start page's first. A window's title is
# its page's title, then the browser's name, as in "Settings on SoundCloud - Brave".
SIGNED_IN_TITLES = ("Settings on SoundCloud", "Your feed on SoundCloud")
# How long the window may stay open, across all its openings, before sc-mp3 gives up and closes it.
SIGN_IN_TIMEOUT = 10 * 60  # seconds
CANCEL_POLL = 0.25  # seconds
# Each listing of the profile's processes starts PowerShell, so a handed-off window is listed
# at most this often.
LIST_EVERY = 2.0  # seconds
# How often the window opens again when it closed by itself but the read found no sign-in.
REOPENS = 1

log = logging.getLogger(__name__)


class WindowProblem(StrEnum):
    """Why the window gave no sign-in, besides a cancel. The UI picks the words."""

    no_browser = "no_browser"  # none of the browsers the window can use is installed
    no_sign_in = "no_sign_in"  # closed before SoundCloud signed the person in
    timed_out = "timed_out"
    # a Cancel couldn't stop a browser that still runs on the profile, as it couldn't be listed
    browser_unknown = "browser_unknown"


class WindowSignInFailed(Exception):
    """The window closed without a sign-in. Never holds a cookie."""

    def __init__(self, problem: WindowProblem) -> None:
        super().__init__(f"the sign-in window gave no sign-in: {problem}")
        self.problem = problem


class Closed(StrEnum):
    """How the window's step ended."""

    signed_in = "signed_in"  # by itself, once a window showed a signed-in page
    by_person = "by_person"  # the person closed it, or asked for it to close
    cancelled = "cancelled"


def _ignore(reading: bool) -> None:
    """The default for on_reading, when nothing shows the window's progress."""


@dataclass(frozen=True)
class WindowRequests:
    """What the person asks of an open window, and where the window says it closed by itself."""

    cancel: threading.Event = field(default_factory=threading.Event)
    # "I've signed in": the window is closed normally, so the browser saves the cookies, then read.
    finish: threading.Event = field(default_factory=threading.Event)
    # Told True when the window closes by itself for the read, and False when it opens again.
    on_reading: Callable[[bool], None] = _ignore
    started: StartedBrowsers = field(default_factory=StartedBrowsers)


def open_sign_in_window(requests: WindowRequests, browser: Browser) -> SignInCookies | None:
    """Open SoundCloud in `browser`, and return the sign-in cookies once its window has closed.

    The window closes by itself on a signed-in page, or the person closes it or asks for it to
    close through `requests.finish`. Returns None after `requests.cancel`, even one during the
    read. Raises WindowSignInFailed when it closed before a sign-in, after 10 minutes, or when its
    processes can't be checked, and ScmpError when the browser fails.
    """
    profile = chromium.kept_profile(browser.kind.id)
    deadline = time.monotonic() + SIGN_IN_TIMEOUT
    reopens = REOPENS
    while True:
        closed = _sign_in_step(browser, profile, requests, deadline)
        if closed is Closed.cancelled:
            return None
        found = _read(browser, profile, requests.started)
        # The read has deleted SoundCloud's cookies, so dropping what it found leaves nothing.
        if requests.cancel.is_set():
            log.info("The sign-in was cancelled while it was read, so nothing is signed in")
            return None
        if found is not None:
            return found
        # The kept profile keeps the person's place, so a title that fooled sc-mp3 costs a click.
        if closed is not Closed.signed_in or requests.finish.is_set() or reopens == 0:
            raise WindowSignInFailed(WindowProblem.no_sign_in)
        reopens -= 1
        log.info("The window closed on a signed-in page but held no sign-in, so it opens again")
        requests.on_reading(False)


def _sign_in_step(
    browser: Browser, profile: Path, requests: WindowRequests, deadline: float
) -> Closed:
    """A plain browser, until none runs on its profile."""
    # Brave restores the last session's tabs, even the read step's, and asks before closing them.
    chromium.forget_tabs(profile)
    process = requests.started.start(browser.path, profile, START_URL, chromium.BASE_FLAGS)
    log.info("Opened SoundCloud's sign-in in %s, process %d", browser.kind.name, process.pid)
    closed = Closed.by_person
    asked_to_close: set[int] = set()
    watch = WindowWatch(process, profile)
    while browsers := watch.browsers():
        if closed is not Closed.signed_in and _shows_signed_in(browsers):
            log.info("SoundCloud shows a signed-in page, so the window closes by itself")
            closed = Closed.signed_in
            requests.on_reading(True)
        if closed is Closed.signed_in or requests.finish.is_set():
            _close(browsers, asked_to_close)
        # Stopping the browser saves no cookies, which is right for both of these.
        if requests.cancel.is_set():
            log.info("The sign-in was cancelled, so its browser window is stopped")
            if not _stop(process, profile):
                raise WindowSignInFailed(WindowProblem.browser_unknown)
            return Closed.cancelled
        if time.monotonic() >= deadline:
            log.info("Nobody signed in within the time limit, so the browser is stopped")
            _stop(process, profile)
            raise WindowSignInFailed(WindowProblem.timed_out)
        requests.cancel.wait(CANCEL_POLL)
    log.info("The sign-in window was closed")
    return closed


class WindowWatch:
    """The browsers keeping the window open: the one started, or one it handed the window to.

    The read must wait for none, or its headless browser would hand off to one and close it. A
    free lockfile says no browser runs on the profile, with no process listing. Only a handed-off
    window is listed, for the ids of its windows, and at most every LIST_EVERY seconds.
    """

    def __init__(self, process: subprocess.Popen[bytes], profile: Path) -> None:
        self._process = process
        self._profile = profile
        self._seen = [process.pid]
        self._next_listing = 0.0

    def browsers(self) -> list[int]:
        """The browser processes keeping the window open, which is none once it has closed."""
        if self._process.poll() is None:
            return [self._process.pid]
        if not chromium.profile_in_use(self._profile):
            return []
        if time.monotonic() < self._next_listing:
            return self._seen
        self._next_listing = time.monotonic() + LIST_EVERY
        found = chromium.browsers_on(self._profile)
        if not found:
            # The lockfile says a browser still runs, though the listing failed or missed it.
            return self._seen
        if found != self._seen:
            log.info("The browser on the sign-in profile now runs as process(es) %s", found)
        self._seen = found
        return found


def _shows_signed_in(browsers: list[int]) -> bool:
    """Whether one of the browsers' windows shows a page only a signed-in person reaches."""
    titles = browser_windows.window_titles(browsers)
    return any(title.startswith(SIGNED_IN_TITLES) for title in titles)


def _close(browsers: list[int], asked_to_close: set[int]) -> None:
    """Ask each browser's windows to close once, as their X does, so it saves the cookies."""
    for pid in browsers:
        if pid in asked_to_close:
            continue
        asked_to_close.add(pid)
        closed = browser_windows.close_windows(pid)
        log.info("%d window(s) of browser process %d were asked to close", closed, pid)


def _stop(process: subprocess.Popen[bytes], profile: Path) -> bool:
    """Stop the window's browser, or whatever runs on its profile once that one has exited.

    False when what runs on the profile can't be known.
    """
    if process.poll() is None:
        chromium.stop(process)
        return True
    if not chromium.profile_in_use(profile) or chromium.stop_processes_on(profile):
        return True
    log.warning("Couldn't check whether a browser still runs on the sign-in profile")
    return False


def _read(browser: Browser, profile: Path, started: StartedBrowsers) -> SignInCookies | None:
    """Step 2, after a window that closed normally and may have saved SoundCloud's session."""
    started.reading(profile)
    try:
        return read_sign_in(browser.path, profile, started)
    except ScmpError:
        # Once sc-mp3 is closing, stop_started sets the profile aside instead.
        if not started.closed:
            log.warning("The sign-in couldn't be read, so the profile that may hold it is deleted")
            chromium.forget_profile(profile)
        raise
    finally:
        started.reading(None)


def stop_started(requests: WindowRequests) -> None:
    """Stop what a sign-in started that still runs, as sc-mp3 exits, by the processes' own ids.

    A profile whose SoundCloud session was saved but never read is set aside, so the next start
    deletes it.
    """
    # Closed first, so the sign-in's thread starts no browser and changes nothing from here on.
    running, profile = requests.started.close()
    for process in running:
        log.info("sc-mp3 is closing, so the sign-in's browser, process %d, is stopped", process.pid)
        chromium.stop(process)
    if profile is not None:
        log.info("The sign-in profile may hold an unread SoundCloud session, so it's set aside")
        chromium.set_aside(profile)


@dataclass(frozen=True)
class SignInWindow:
    """The window's parts, so tests can pass fakes and the app passes nothing."""

    open: Callable[[WindowRequests, Browser], SignInCookies | None] = open_sign_in_window
    # The browsers found, and the one to use for the browser id picked in Settings.
    find: Callable[[str], BrowserOptions] = find_browsers
    # Deletes what an earlier sign-out couldn't, at startup.
    clean_up: Callable[[], None] = chromium.remove_leftover_profiles
    # Deletes every kept profile, when signing out. False when one stays.
    forget: Callable[[], bool] = chromium.delete_profiles
    # Whether a kept profile is there, so Sign out is offered even with no sign-in stored.
    has_profile: Callable[[], bool] = chromium.has_kept_profiles
    # Stops what a sign-in started, when sc-mp3 exits before it's done.
    stop: Callable[[WindowRequests], None] = stop_started
    # Whether the window can see the sign-in and close by itself, so the UI picks its words.
    closes_by_itself: bool = browser_windows.CAN_READ_TITLES
