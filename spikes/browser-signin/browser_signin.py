"""Spike: SoundCloud sign-in in a real Edge window on a temporary profile. See README.md here."""

import argparse
import logging
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from account import AccountResult, check_account
from cookies import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    WANTED,
    CookieFacts,
    describe,
    facts,
    page_place,
    sign_in_cookies,
    token_values,
)
from devtools import (
    BrowserGone,
    DevTools,
    DevToolsError,
    close_browser,
    refuses_web_origin,
    version,
    webdriver_flag,
)
from edge import (
    DevToolsPort,
    EdgeError,
    close_main_window,
    delete_profile,
    edge_processes,
    find_edge,
    kill_tree,
    listening_addresses,
    reachable_from_lan,
    start_edge,
    stop_leftovers,
    wait_for_exit,
    wait_for_port,
)
from two_step import TwoStepRun, run_two_step

from scmp3.core.log import RedactFilter, redact

SIGNIN_URL = "https://soundcloud.com/signin"
DEFAULT_SCRATCH = Path(tempfile.gettempdir()) / "scmp3-browser-signin"
SIGNIN_TIMEOUT = 600.0  # seconds
POLL_INTERVAL = 1.0  # seconds
# The self-test closes the window itself after this many polls, to see that it's noticed.
SELFTEST_POLLS = 5
# The ways a watch can end that mean the person closed the window.
CLOSE_OUTCOMES = frozenset({"window closed", "browser exited"})


def say(text: str = "") -> None:
    """Print a line after masking every registered token, token shape and the home folder."""
    print(redact(text), flush=True)


@dataclass
class WindowRun:
    """What one Edge window did. The token values stay out of repr."""

    ended_by: str = "not started"
    seconds_open: float | None = None  # from the port file appearing to the browser exiting
    cookies: list[CookieFacts] = field(default_factory=list)
    tokens: dict[str, str] = field(default_factory=dict, repr=False)
    places: list[str] = field(default_factory=list)  # each distinct page seen, in order
    closed_at: float | None = None  # when the self-test closed the window
    noticed_after: float | None = None  # how long until the close was noticed
    polls: int = 0
    slowest_poll: float = 0.0


def main() -> int:
    args = parse_args()
    _set_up_logging()
    args.scratch.mkdir(parents=True, exist_ok=True)
    edge = find_edge()
    if edge is None:
        say("Edge wasn't found in App Paths or the usual install folders")
        return 1
    say(f"Edge: {edge}")
    if args.command == "twostep":
        return two_step(edge, args)
    selftest = args.command == "selftest"
    timeout = args.timeout if not selftest else 60.0
    run = run_window(edge, args.scratch, timeout=timeout, selftest=selftest)
    print_run(run)
    if selftest or run.ended_by != "both cookies found":
        return 0 if selftest else 1
    return account_checks(run.tokens, args.scratch)


def two_step(edge: Path, args: argparse.Namespace) -> int:
    run = run_two_step(edge, args.scratch, args.timeout, say)
    print_two_step(run)
    if not run.tokens:
        return 1
    return account_checks(run.tokens, args.scratch)


def account_checks(tokens: dict[str, str], scratch: Path) -> int:
    """/me and one renewal, then the values are dropped."""
    result = check_account(tokens[ACCESS_COOKIE], tokens[REFRESH_COOKIE], scratch, say)
    tokens.clear()
    print_account(result)
    return 0 if result.error is None else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["selftest", "signin", "twostep"])
    parser.add_argument("--scratch", type=Path, default=DEFAULT_SCRATCH)
    parser.add_argument("--timeout", type=float, default=SIGNIN_TIMEOUT)
    return parser.parse_args()


def _set_up_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="log: %(name)s: %(message)s")


def run_window(edge: Path, scratch: Path, *, timeout: float, selftest: bool) -> WindowRun:
    """Open the sign-in page in a new Edge, watch it, then close it and delete its profile."""
    run = WindowRun()
    profile = Path(tempfile.mkdtemp(prefix="profile-", dir=scratch))
    say(f"msedge.exe processes running before the start: {edge_processes(profile).others}")
    started = time.monotonic()
    process = start_edge(edge, profile, SIGNIN_URL)
    say(f"started Edge, process id {process.pid}")
    devtools: DevTools | None = None
    port: DevToolsPort | None = None
    try:
        port = wait_for_port(process, profile, started)
        say(f"DevTools port written after {port.seconds_to_open:.2f} s")
        devtools = DevTools(port.port, port.browser_path)
        print_exposure(port, devtools, selftest)
        run.ended_by = watch(devtools, process, run, deadline=started + timeout, selftest=selftest)
        if run.ended_by in CLOSE_OUTCOMES and run.closed_at is not None:
            run.noticed_after = time.monotonic() - run.closed_at
    except (EdgeError, DevToolsError, OSError) as error:
        run.ended_by = f"error: {error}"
    finally:
        shut_down(process, devtools, profile, port, started, run)
    return run


def print_exposure(port: DevToolsPort, devtools: DevTools, selftest: bool) -> None:
    """Who can reach the DevTools port, and what the browser says about itself."""
    info = version(port.port)
    say(f"browser: {info.get('Browser')}, protocol {info.get('Protocol-Version')}")
    say(f"user agent says headless: {'Headless' in info.get('User-Agent', '')}")
    say(f"listening on: {', '.join(listening_addresses(port.port)) or 'nothing found'}")
    lan = reachable_from_lan(port.port)
    say(f"this machine's LAN addresses accept the port: {lan or 'no LAN address'}")
    refused = refuses_web_origin(port.port, port.browser_path)
    say(f"a WebSocket with a web page's Origin is refused: {refused}")
    if selftest:
        say(f"navigator.webdriver as a page sees it: {webdriver_flag(devtools)}")


def watch(
    devtools: DevTools,
    process: subprocess.Popen[bytes],
    run: WindowRun,
    *,
    deadline: float,
    selftest: bool,
) -> str:
    """Poll once a second until both cookies appear, the window closes or time runs out."""
    saw_page = False
    while True:
        if process.poll() is not None:
            return "browser exited"
        poll_started = time.monotonic()
        try:
            pages = _pages(devtools)
            cookies = devtools.call("Storage.getCookies").get("cookies", [])
        except BrowserGone:
            return "browser exited"
        run.polls += 1
        run.slowest_poll = max(run.slowest_poll, time.monotonic() - poll_started)
        _note_places(pages, run)
        if pages:
            saw_page = True
        elif saw_page:
            return "window closed"
        if _take_tokens(cookies, run):
            return "both cookies found"
        if selftest and run.polls == SELFTEST_POLLS:
            _close_window_like_a_person(process, cookies, run)
        if time.monotonic() > deadline:
            return "timed out"
        time.sleep(POLL_INTERVAL)


def _pages(devtools: DevTools) -> list[dict[str, Any]]:
    """The open tabs and popups. Listing targets doesn't attach to them."""
    targets = devtools.call("Target.getTargets").get("targetInfos", [])
    return [target for target in targets if target.get("type") == "page"]


def _note_places(pages: list[dict[str, Any]], run: WindowRun) -> None:
    for page in pages:
        place = page_place(page.get("url", ""))
        if page.get("openerId"):
            place += " (popup)"
        if place not in run.places:
            run.places.append(place)
            say(f"{time.strftime('%H:%M:%S')} page: {place}")


def _take_tokens(cookies: list[dict[str, Any]], run: WindowRun) -> bool:
    """Keep both values in memory once both cookies are there. True when they are."""
    found = sign_in_cookies(cookies)
    tokens = token_values(found)
    if tokens is None:
        return False
    run.cookies = [facts(found[name]) for name in WANTED]
    run.tokens = tokens
    return True


def _close_window_like_a_person(
    process: subprocess.Popen[bytes], cookies: list[dict[str, Any]], run: WindowRun
) -> None:
    soundcloud = [c for c in cookies if "soundcloud.com" in c.get("domain", "")]
    say(f"signed-out page: {len(soundcloud)} soundcloud.com cookies, no sign-in cookies")
    run.closed_at = time.monotonic()
    say(f"closing the window with WM_CLOSE: sent {close_main_window(process.pid)}")


def shut_down(
    process: subprocess.Popen[bytes],
    devtools: DevTools | None,
    profile: Path,
    port: DevToolsPort | None,
    started: float,
    run: WindowRun,
) -> None:
    """Browser.close, wait for the exit, stop leftovers by process id, delete the profile."""
    if devtools is not None:
        say(close_browser(devtools))
    exited = wait_for_exit(process)
    if port is not None:
        run.seconds_open = time.monotonic() - (started + port.seconds_to_open)
    say(f"browser exited after Browser.close: {exited}, code {process.returncode}")
    if not exited:
        kill_tree(process.pid)
        say(f"stopped process {process.pid} and its children by id")
    say(f"processes left on the temporary profile, stopped by id: {stop_leftovers(profile)}")
    say(f"temporary profile deleted: {delete_profile(profile)}")
    say(f"msedge.exe processes running after the run: {edge_processes(profile).others}")


def print_run(run: WindowRun) -> None:
    say()
    say(f"ended by: {run.ended_by}")
    say(f"polls: {run.polls}, slowest {run.slowest_poll * 1000:.0f} ms")
    if run.seconds_open is not None:
        say(f"DevTools port open for {run.seconds_open:.1f} s")
    if run.noticed_after is not None:
        say(f"window close noticed after {run.noticed_after:.2f} s")
    say(f"pages seen: {len(run.places)}")
    for cookie in run.cookies:
        say(describe(cookie))


def print_two_step(run: TwoStepRun) -> None:
    say()
    say(f"step 1 ended by: {run.sign_in_ended_by}")
    if run.sign_in_seconds is not None:
        say(f"step 1 took {run.sign_in_seconds:.1f} s")
    if run.read_seconds is not None:
        say(f"step 2 took {run.read_seconds:.1f} s from start to exit")
    if run.port_open_seconds is not None:
        say(f"step 2 DevTools port open for {run.port_open_seconds:.1f} s")
    if run.error:
        say(f"stopped by an error: {run.error}")
    for cookie in run.cookies:
        say(describe(cookie))
    say(f"cookies missing: {', '.join(run.missing) or 'none'}")
    say(f"soundcloud.com cookies the relaunch found, of any name: {run.soundcloud_cookies}")


def print_account(result: AccountResult) -> None:
    say()
    if result.error:
        say(f"account checks stopped: {result.error}")
    say(f"/me username: {result.username}")
    say(f"/me plan: {result.tier}")
    say(f"temporary store used: {result.stored_in}")
    say(f"renewed once: {result.renewed}")
    if result.refresh_changed is not None:
        say(f"refresh token changed on renewal: {result.refresh_changed}")
    if result.new_token_hours is not None:
        say(f"renewed access token expires in {result.new_token_hours:.2f} hours")
    say(f"/me with the renewed sign-in: {result.me_with_new_token}")


if __name__ == "__main__":
    sys.exit(main())
