"""Step 2 of the sign-in window: a headless browser on the kept profile reads the two cookies.

It loads no page, deletes every soundcloud.com cookie once they're read, checks none is left, and
closes. When any could have stayed, the whole profile goes, so it never holds a session sc-mp3
renews.
"""

import logging
import subprocess
from pathlib import Path
from typing import Any

from scmp3.app import chromium
from scmp3.app.devtools import BrowserDevTools, open_socket
from scmp3.app.signin_cookies import (
    SignInCookies,
    cookie_list,
    expired_copy,
    pick_cookies,
    soundcloud_cookies,
)
from scmp3.app.started_browsers import StartedBrowsers
from scmp3.core.errors import ScmpError

# No page loads, so nothing on SoundCloud's side can renew the tokens while they're read.
READ_URL = "about:blank"

log = logging.getLogger(__name__)


def read_sign_in(browser: Path, profile: Path, started: StartedBrowsers) -> SignInCookies | None:
    """The two sign-in cookies in the profile, or None, with SoundCloud's deleted from it.

    Its browsers start through `started`, so sc-mp3 can stop them if it closes first.
    """
    process, port = _start_reader(browser, profile, started)
    cleared = False
    try:
        devtools = BrowserDevTools(open_socket(port.port, port.browser_path))
        try:
            cookies = cookie_list(devtools.call("Storage.getCookies").get("cookies"))
            found = pick_cookies(cookies)
            cleared = _clear_soundcloud(devtools, cookies)
        finally:
            devtools.close_browser()
    finally:
        if not chromium.wait_for_exit(process):
            chromium.stop(process)
            # Chromium writes deleted cookies to disk at a normal close, so a stop can undo them.
            cleared = False
    if not cleared:
        # The profile mustn't keep the session sc-mp3 is about to renew, so it goes, Google's too.
        log.warning("SoundCloud's cookies may have stayed in the sign-in profile, so it's deleted")
        chromium.forget_profile(profile)
    if found is not None:
        log.info("Read the sign-in from the browser window's profile")
    return found


def _clear_soundcloud(devtools: BrowserDevTools, cookies: list[dict[str, Any]]) -> bool:
    """Delete soundcloud.com's cookies and check that none is left. False when some are."""
    doomed = soundcloud_cookies(cookies)
    if not doomed:
        return True
    try:
        devtools.call("Storage.setCookies", {"cookies": [expired_copy(item) for item in doomed]})
        left = soundcloud_cookies(cookie_list(devtools.call("Storage.getCookies").get("cookies")))
    except ScmpError as error:
        log.warning("Deleting SoundCloud's cookies from the sign-in profile failed: %s", error)
        return False
    if left:
        log.warning("%d of SoundCloud's %d cookies stayed in the profile", len(left), len(doomed))
        return False
    log.info("Deleted SoundCloud's %d cookies from the sign-in profile", len(doomed))
    return True


def _start_reader(
    browser: Path, profile: Path, started: StartedBrowsers
) -> tuple[subprocess.Popen[bytes], chromium.DevToolsPort]:
    """Start the headless browser and wait for its port, once more if the profile was still busy."""
    try:
        return _reader(browser, profile, started)
    except ScmpError as error:
        # A browser hands its work to another one still running on the profile, such as one still
        # shutting down after the window closed, and exits.
        log.info("The headless browser didn't start, so it's tried once more: %s", error)
        if not chromium.stop_processes_on(profile):
            log.warning("Couldn't check what still runs on the sign-in profile")
        return _reader(browser, profile, started)


def _reader(
    browser: Path, profile: Path, started: StartedBrowsers
) -> tuple[subprocess.Popen[bytes], chromium.DevToolsPort]:
    (profile / chromium.PORT_FILE).unlink(missing_ok=True)
    # So no tab a headless browser might restore loads SoundCloud while the tokens are read.
    chromium.forget_tabs(profile)
    process = started.start(browser, profile, READ_URL, chromium.READ_FLAGS)
    try:
        return process, chromium.wait_for_port(process, profile)
    except ScmpError:
        if process.poll() is None:
            chromium.stop(process)
        raise
