"""The processes on a sign-in profile: finding them by command line, and ending them safely.

Windows only, like the sign-in window. A process is ended only through a handle opened on it, and
only when that handle's creation time still matches the listing, so an id Windows has reused for
another program is never ended.
"""

import functools
import logging
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The browser's helper processes, such as the crash handler, carry this flag. The browser doesn't.
HELPER_FLAG = "--type="
# Each process's id, creation time and command line, tab-separated. The time is in 100 ns steps
# since 1601 in UTC, as GetProcessTimes gives it, or 0 when CIM doesn't know it.
LIST_PROCESSES = (
    "Get-CimInstance Win32_Process | ForEach-Object { $started = 0; "
    "if ($_.CreationDate) { $started = $_.CreationDate.ToFileTimeUtc() }; "
    '"$($_.ProcessId)`t$started`t$($_.CommandLine)" }'
)
LIST_TIMEOUT = 30.0  # seconds
# CIM gives creation times to the microsecond, and Windows to 100 ns.
SAME_START = 10  # 100 ns steps
END_WAIT = 5000  # milliseconds
# Each round ends what's listed, and a helper may outlive its browser by a moment.
STOP_ROUNDS = 3
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Only Windows has the sign-in window, and the calls that end a process through a handle.
CAN_END = sys.platform == "win32"
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
ERROR_INVALID_PARAMETER = 87  # what OpenProcess says for an id no process has
WAIT_OBJECT_0 = 0  # what WaitForSingleObject says for a process that has ended

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Listed:
    """One running process, as the listing saw it."""

    pid: int
    started: int  # creation time, in 100 ns steps since 1601 in UTC
    command: str

    @property
    def is_browser(self) -> bool:
        """The browser itself owns the windows; its helpers carry --type=."""
        return HELPER_FLAG not in self.command


def powershell_exe() -> Path:
    """Windows PowerShell by its full path, so no other program of that name runs instead."""
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return Path(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def processes_on(profile: Path) -> list[Listed] | None:
    """Each process whose command line names this profile, or None when they can't be listed."""
    listing = _list_all()
    if listing is None:
        return None
    # The profile's folder, not one whose name starts the same, as chromium does after chrome.
    names_profile = re.compile(re.escape(str(profile)) + r'(?=["\s\\]|$)', re.IGNORECASE)
    return [item for item in listing if names_profile.search(item.command)]


def _list_all() -> list[Listed] | None:
    command = [str(powershell_exe()), "-NoProfile", "-NonInteractive", "-Command", LIST_PROCESSES]
    try:
        output = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
            timeout=LIST_TIMEOUT,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        log.warning("Couldn't list the processes that may use a sign-in profile: %s", error)
        return None
    return parse_listing(output.splitlines())


def parse_listing(lines: Iterable[str]) -> list[Listed]:
    """The processes in the listing's lines, skipping any line that isn't one."""
    found: list[Listed] = []
    for line in lines:
        pid, _, rest = line.partition("\t")
        started, _, command = rest.partition("\t")
        if pid.isdigit() and started.isdigit():
            found.append(Listed(int(pid), int(started), command))
    return found


def stop_all_on(profile: Path) -> bool:
    """End every process on this profile, browsers first. False when what runs can't be known."""
    for _ in range(STOP_ROUNDS):
        found = processes_on(profile)
        if found is None:
            return False
        if not found:
            return True
        # Ending a browser usually ends its helpers, so they're often gone by the next round.
        for item in sorted(found, key=lambda item: not item.is_browser):
            end(item)
    return processes_on(profile) == []


def end(item: Listed) -> None:
    """End one listed process, but only while it's still the process that was listed."""
    if not CAN_END:
        return
    handle = _open(item.pid)
    if handle is None:
        _say_why_not_opened(item.pid)
        return
    try:
        if not _same_start(item, _creation_time(handle)):
            log.info("Process %d is another program now, so it's left running", item.pid)
            return
        # A helper often ends with its browser, after the listing but before its turn here.
        if _has_ended(handle):
            return
        log.info("Stopping process %d, which still uses a sign-in profile", item.pid)
        _terminate(handle)
    finally:
        _close(handle)


def _same_start(item: Listed, created: int) -> bool:
    """Whether a handle's process started when the listed one did. An unknown time never does.

    An id is only reused once its process has gone, so a new start time means a new program.
    """
    if item.started <= 0 or created < 0:
        return False
    return abs(created - item.started) <= SAME_START


@functools.cache
def _kernel32() -> Any:
    """kernel32, with the types the calls below need. Windows only."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32


def _open(pid: int) -> int | None:
    """A handle that can check and end the process, or None when it can't be opened."""
    access = PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    handle = _kernel32().OpenProcess(access, False, pid)
    return handle or None


def _say_why_not_opened(pid: int) -> None:
    """Log why OpenProcess failed, unless the process had already gone."""
    import ctypes

    error = ctypes.get_last_error()
    if error != ERROR_INVALID_PARAMETER:
        log.warning("Process %d on a sign-in profile couldn't be opened to stop it: %d", pid, error)


def _creation_time(handle: int) -> int:
    """When the process started, in 100 ns steps since 1601 in UTC, or -1 when unknown."""
    import ctypes
    from ctypes import wintypes

    times = [wintypes.FILETIME() for _ in range(4)]
    get_times = _kernel32().GetProcessTimes
    if not get_times(wintypes.HANDLE(handle), *(ctypes.byref(time) for time in times)):
        return -1
    created = times[0]
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def _close(handle: int) -> None:
    _kernel32().CloseHandle(handle)


def _has_ended(handle: int) -> bool:
    """Whether the process has ended, though its handle still opens."""
    from ctypes import wintypes

    return _kernel32().WaitForSingleObject(wintypes.HANDLE(handle), 0) == WAIT_OBJECT_0


def _terminate(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = _kernel32()
    if not kernel32.TerminateProcess(wintypes.HANDLE(handle), 1):
        log.warning("A process on a sign-in profile couldn't be stopped")
        return
    kernel32.WaitForSingleObject(wintypes.HANDLE(handle), END_WAIT)
