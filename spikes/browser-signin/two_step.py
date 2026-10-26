"""The two-step variant: sign in with nothing attached, then read the cookies headless.

Step 1 starts Edge with no DevTools port, and the person closes the window once signed in.
Step 2 starts Edge headless on the same profile with a port, reads the two cookies and closes.
"""

import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cookies import WANTED, CookieFacts, facts, is_soundcloud, sign_in_cookies, token_values
from devtools import DevTools, DevToolsError, close_browser
from edge import (
    BASE_FLAGS,
    HEADLESS_FLAG,
    PORT_FILE,
    PORT_FLAG,
    EdgeError,
    delete_profile,
    edge_processes,
    kill_tree,
    start_edge,
    stop_leftovers,
    wait_for_exit,
    wait_for_port,
)

SIGNIN_URL = "https://soundcloud.com/signin"
# The headless relaunch loads no page, so nothing on SoundCloud's side can renew the tokens.
READ_URL = "about:blank"
READ_FLAGS = (HEADLESS_FLAG, PORT_FLAG, *BASE_FLAGS)

Say = Callable[[str], None]


@dataclass
class TwoStepRun:
    """What the two steps did. The token values stay out of repr."""

    sign_in_ended_by: str = "not started"
    sign_in_seconds: float | None = None
    read_seconds: float | None = None  # from the headless start to its exit
    port_open_seconds: float | None = None
    cookies: list[CookieFacts] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    soundcloud_cookies: int = 0  # how many soundcloud.com cookies the relaunch found, of any name
    tokens: dict[str, str] = field(default_factory=dict, repr=False)
    error: str | None = None


def run_two_step(edge: Path, scratch: Path, timeout: float, say: Say) -> TwoStepRun:
    """Run both steps, then delete the profile, which holds the Google session too."""
    run = TwoStepRun()
    profile = Path(tempfile.mkdtemp(prefix="profile-", dir=scratch))
    say(f"msedge.exe processes running before the start: {edge_processes(profile).others}")
    try:
        _sign_in_step(edge, profile, timeout, run, say)
        _read_step(edge, profile, run, say)
    except (EdgeError, DevToolsError, OSError) as error:
        run.error = str(error)
    finally:
        say(f"processes left on the temporary profile, stopped by id: {stop_leftovers(profile)}")
        say(f"temporary profile deleted: {delete_profile(profile)}")
        say(f"msedge.exe processes running after the run: {edge_processes(profile).others}")
    return run


def _sign_in_step(edge: Path, profile: Path, timeout: float, run: TwoStepRun, say: Say) -> None:
    """Edge with no port and no connection, until the person closes it or time runs out."""
    started = time.monotonic()
    process = start_edge(edge, profile, SIGNIN_URL, BASE_FLAGS)
    say(f"step 1: started Edge with no DevTools port, process id {process.pid}")
    if wait_for_exit(process, timeout):
        run.sign_in_ended_by = "window closed"
    else:
        _stop(process, say)
        run.sign_in_ended_by = "timed out, stopped by id"
    run.sign_in_seconds = time.monotonic() - started
    say(f"step 1 ended: {run.sign_in_ended_by}, after {run.sign_in_seconds:.1f} s")
    # The crash handler can outlive the browser, and the relaunch needs the profile to itself.
    say(f"step 1 processes left, stopped by id: {stop_leftovers(profile)}")


def _read_step(edge: Path, profile: Path, run: TwoStepRun, say: Say) -> None:
    """A headless Edge on the same profile, asked once for its cookies, then closed."""
    (profile / PORT_FILE).unlink(missing_ok=True)
    started = time.monotonic()
    process = start_edge(edge, profile, READ_URL, READ_FLAGS)
    say(f"step 2: started headless Edge, process id {process.pid}")
    devtools: DevTools | None = None
    port_seen: float | None = None
    try:
        port = wait_for_port(process, profile, started)
        port_seen = started + port.seconds_to_open
        say(f"step 2: DevTools port written after {port.seconds_to_open:.2f} s")
        devtools = DevTools(port.port, port.browser_path)
        _take_cookies(devtools, run)
    finally:
        if devtools is not None:
            say(close_browser(devtools))
        if not wait_for_exit(process):
            _stop(process, say)
        run.read_seconds = time.monotonic() - started
        if port_seen is not None:
            run.port_open_seconds = time.monotonic() - port_seen


def _take_cookies(devtools: DevTools, run: TwoStepRun) -> None:
    """Keep only the two sign-in cookies. The rest, Google's included, is dropped unseen."""
    cookies = devtools.call("Storage.getCookies").get("cookies", [])
    run.soundcloud_cookies = sum(is_soundcloud(cookie.get("domain", "")) for cookie in cookies)
    found = sign_in_cookies(cookies)
    run.cookies = [facts(found[name]) for name in WANTED if name in found]
    run.missing = [name for name in WANTED if name not in found]
    run.tokens = token_values(found) or {}


def _stop(process: subprocess.Popen[bytes], say: Say) -> None:
    kill_tree(process.pid)
    wait_for_exit(process)
    say(f"stopped process {process.pid} and its children by id")
