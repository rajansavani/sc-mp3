"""Runs a Chromium-based browser on a profile of sc-mp3's own, for the sign-in window.

Chromium runs one browser per --user-data-dir, so these folders never touch the person's own
browser. Each browser keeps one in sc-mp3's data folder until signing out, so Google remembers the
person. A process is only ever stopped by its id: one sc-mp3 started, or one browser_processes
finds on these profiles. T-043's spike (spikes/browser-signin) has the evidence behind each flag.
"""

import logging
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from scmp3.app import browser_processes
from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError

# Every Chromium browser takes these. Without --disable-sync, Edge signs a new profile in to the
# Windows account and syncs into it.
BASE_FLAGS = ("--no-first-run", "--no-default-browser-check", "--disable-sync")
# Port 0 lets the browser pick a free port, which it writes to DevToolsActivePort in the profile.
READ_FLAGS = ("--headless=new", "--remote-debugging-port=0", *BASE_FLAGS)
PORT_FILE = "DevToolsActivePort"
# A browser holds this file in its profile, shared with nothing, for as long as it runs. Windows
# deletes it when the browser ends, even when it's stopped. Only one browser runs on a profile.
LOCK_FILE = "lockfile"
PROFILES_FOLDER = "signin"
# Where a browser saves its open tabs, in the profile's Default folder: Sessions in current
# versions, Sessions_Encrypted in Brave's, and four files in older ones.
PROFILE_FOLDER = "Default"
SAVED_TABS = (
    "Sessions",
    "Sessions_Encrypted",
    "Current Session",
    "Current Tabs",
    "Last Session",
    "Last Tabs",
)
# Signing out renames a profile to this first, so one it can't delete yet is found at startup.
REMOVED_PREFIX = "removed-"
PORT_TIMEOUT = 30.0  # seconds
EXIT_TIMEOUT = 15.0  # seconds
KILL_TIMEOUT = 10.0  # seconds
DELETE_TRIES = 20
DELETE_PAUSE = 0.5  # seconds
POLL = 0.1  # seconds
# Keeps a console window from opening for taskkill. Only Windows has it.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DevToolsPort:
    """Where a headless browser's DevTools listen: the port, and the browser target's path."""

    port: int
    browser_path: str


def profiles_root() -> Path:
    """In the data folder, so nothing that clears the cache deletes a kept profile."""
    return paths.data_dir() / PROFILES_FOLDER


def kept_profile(browser_id: str) -> Path:
    """The profile this browser keeps between sign-ins, created empty the first time."""
    profile = profiles_root() / browser_id
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ScmpError(ErrorKind.disk, f"can't create the sign-in profile: {error}") from error
    return profile


def forget_tabs(profile: Path) -> None:
    """Delete the tabs a kept profile saved, so the next window opens on its one page alone."""
    for name in SAVED_TABS:
        path = profile / PROFILE_FOLDER / name
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as error:
            # The window still opens. It may show an old tab too, and ask before closing both.
            log.warning("Couldn't delete the tabs a sign-in profile saved: %s", error)


def profile_in_use(profile: Path) -> bool:
    """Whether a browser runs on this profile, told by its lockfile with no process listing."""
    try:
        # Opened for writing without truncating, so a lockfile left behind is never changed.
        handle = os.open(profile / LOCK_FILE, os.O_WRONLY)
    except FileNotFoundError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        # Taken as in use, so the window is waited for rather than read while it may be open.
        log.warning("Couldn't check a sign-in profile's lockfile: %s", error)
        return True
    os.close(handle)
    return False


def start(
    browser: Path, profile: Path, url: str, flags: tuple[str, ...]
) -> subprocess.Popen[bytes]:
    """Start a separate browser on `profile`, a folder no other browser is using."""
    command = [str(browser), f"--user-data-dir={profile}", *flags, url]
    try:
        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as error:
        raise ScmpError(
            ErrorKind.internal, f"can't start {browser.name}: {error.strerror}"
        ) from error


def wait_for_port(process: subprocess.Popen[bytes], profile: Path) -> DevToolsPort:
    """The port and browser path a headless browser writes to DevToolsActivePort once it listens."""
    deadline = time.monotonic() + PORT_TIMEOUT
    while time.monotonic() < deadline:
        # A browser that handed its work to another one on the same profile exits here.
        if process.poll() is not None:
            raise ScmpError(
                ErrorKind.internal, f"the browser exited with {process.returncode} early"
            )
        lines = _lines(profile / PORT_FILE)
        if len(lines) >= 2 and lines[0].isdigit():
            return DevToolsPort(int(lines[0]), lines[1])
        time.sleep(POLL)
    raise ScmpError(ErrorKind.internal, f"the browser wrote no {PORT_FILE} in {PORT_TIMEOUT:.0f} s")


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def wait_for_exit(process: subprocess.Popen[bytes], timeout: float = EXIT_TIMEOUT) -> bool:
    """True when the process exits within `timeout`."""
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def stop(process: subprocess.Popen[bytes]) -> None:
    """Stop a browser this app started, and its child processes, by its process id."""
    stop_tree(process.pid)
    wait_for_exit(process, KILL_TIMEOUT)


def stop_tree(pid: int) -> None:
    """End one process and its descendants with taskkill, by process id only."""
    taskkill = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
    command = [str(taskkill), "/T", "/F", "/PID", str(pid)]
    try:
        subprocess.run(
            command,
            capture_output=True,
            creationflags=NO_WINDOW,
            timeout=KILL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("taskkill couldn't end the browser's process %d: %s", pid, error)


def processes_on(profile: Path) -> list[int] | None:
    """The ids of processes on this profile, or None when they can't be listed."""
    found = browser_processes.processes_on(profile)
    return None if found is None else [item.pid for item in found]


def browsers_on(profile: Path) -> list[int] | None:
    """The ids of browser processes on this profile, the ones that own its windows, or None."""
    found = browser_processes.processes_on(profile)
    return None if found is None else [item.pid for item in found if item.is_browser]


def stop_processes_on(profile: Path) -> bool:
    """Stop whatever still runs on this profile. False when what runs there can't be known."""
    return browser_processes.stop_all_on(profile)


def delete_profile(profile: Path, tries: int = DELETE_TRIES) -> bool:
    """Delete a sign-in profile, retrying while Windows still holds the browser's last files."""
    for attempt in range(tries):
        if attempt:
            time.sleep(DELETE_PAUSE)
        # What's left is checked below, so a file that's still locked isn't missed.
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists():
            return True
    if tries > 1:
        log.warning("A sign-in profile couldn't be deleted yet, as files in it are still in use")
    return False


def delete_profiles() -> bool:
    """Delete every kept profile, with the Google session in it, for signing out.

    False when one stays under its kept name, so it would still be used and not swept at startup.
    """
    forgotten = [forget_profile(profile) for profile in kept_profiles()]
    return all(forgotten)


def forget_profile(profile: Path) -> bool:
    """Stop what runs on a profile and delete it. True once nothing of it keeps its kept name.

    A profile set aside but not yet deleted counts, since the next start deletes the rest.
    """
    if not stop_processes_on(profile):
        log.warning("A browser may still run on a sign-in profile, so deleting it may fail")
    removed = set_aside(profile)
    deleted = delete_profile(removed)
    return deleted or removed != profile


def set_aside(profile: Path) -> Path:
    """Rename a profile for deleting, so no sign-in uses it again even if some of it stays."""
    removed = profile.with_name(f"{REMOVED_PREFIX}{uuid.uuid4().hex}")
    try:
        return profile.rename(removed)
    except OSError as error:
        log.warning("A sign-in profile couldn't be renamed, so it's deleted in place: %s", error)
        return profile


def kept_profiles() -> list[Path]:
    """The profiles kept for the next sign-in, leaving out those set aside for deleting."""
    return [item for item in _profile_folders() if not item.name.startswith(REMOVED_PREFIX)]


def has_kept_profiles() -> bool:
    """Whether a kept profile, with a Google session in it, is still there for signing out."""
    return bool(kept_profiles())


def remove_leftover_profiles() -> None:
    """Delete what a sign-out couldn't, at startup. Kept profiles stay."""
    for profile in _profile_folders():
        if not profile.name.startswith(REMOVED_PREFIX):
            continue
        log.info("Deleting a sign-in profile that signing out left behind")
        stop_processes_on(profile)
        delete_profile(profile)


def _profile_folders() -> list[Path]:
    try:
        return [item for item in profiles_root().iterdir() if item.is_dir()]
    except FileNotFoundError:
        return []
    except OSError as error:
        log.warning("Couldn't look in the sign-in profiles folder: %s", error)
        return []
