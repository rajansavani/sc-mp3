"""Running a helper program such as ffmpeg to the end, with a timeout and cancel.

Cancel and timeout kill the program's whole process tree, because package managers' shims, such
as Chocolatey's and Scoop's, start the real program as a child.
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scmp3.core.errors import Cancelled, ErrorKind, ScmpError

POLL_INTERVAL = 0.1  # seconds between checks for a cancel
KILL_WAIT = 10  # seconds to wait for killed processes to exit
# Programs such as ffmpeg explain a failure in their last few lines.
ERROR_LINES = 3

log = logging.getLogger(__name__)


def run(
    command: Sequence[str],
    *,
    timeout: float,
    error_kind: ErrorKind,
    cancel: threading.Event | None = None,
) -> None:
    """Run a program to the end.

    Raises Cancelled when `cancel` is set, before, during or after the run. Raises
    ScmpError(error_kind) when the program can't start, fails or runs past `timeout` seconds.
    """
    _stop_if_cancelled(cancel)
    process = _start(command, error_kind)
    try:
        stderr = _wait(process, cancel, timeout)
        if stderr is None:
            raise ScmpError(error_kind, f"{_name(command)} didn't finish within {timeout:g} s")
    except BaseException:
        _kill_tree(process)
        raise
    # A cancel that arrives as the program exits still counts.
    _stop_if_cancelled(cancel)
    if process.returncode == 0:
        return
    text = stderr.decode("utf-8", errors="replace")
    log.debug("%s exited with %s:\n%s", _name(command), process.returncode, text)
    raise ScmpError(error_kind, _failure_summary(text, command))


def _name(command: Sequence[str]) -> str:
    """The program's name for messages, such as ffmpeg."""
    return Path(command[0]).stem


def _stop_if_cancelled(cancel: threading.Event | None) -> None:
    """Raise Cancelled when the user has asked to stop."""
    if cancel is not None and cancel.is_set():
        raise Cancelled


def _start(command: Sequence[str], error_kind: ErrorKind) -> subprocess.Popen[bytes]:
    """Start the program in a way that lets its whole process tree be killed."""
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            **_tree_options(),
        )
    except OSError as error:
        reason = error.strerror or error
        raise ScmpError(error_kind, f"can't start {_name(command)}: {reason}") from error


def _tree_options() -> dict[str, Any]:
    """Popen options for a process tree that can be killed as a whole."""
    if sys.platform == "win32":
        # taskkill /T finds the tree on its own. This only keeps a console window from opening.
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    # The new session's process group holds the program and anything it starts.
    return {"start_new_session": True}


def _wait(
    process: subprocess.Popen[bytes], cancel: threading.Event | None, timeout: float
) -> bytes | None:
    """Wait for the program to exit and return its stderr, or None once `timeout` has passed.

    Raises Cancelled on cancel.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            # communicate keeps reading stderr, so a full pipe can't stall the program.
            return process.communicate(timeout=POLL_INTERVAL)[1]
        except subprocess.TimeoutExpired:
            # Still running. Retrying communicate later loses no output.
            _stop_if_cancelled(cancel)
    return None


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill the process and everything it started, then wait for them to exit."""
    if sys.platform == "win32":
        _taskkill_tree(process.pid)
    else:
        _kill_group(process.pid)
    # In case the tree kill missed the top process. It does nothing once the process is gone.
    process.kill()
    try:
        # stderr reaches its end only once every process holding it has exited.
        process.communicate(timeout=KILL_WAIT)
    except subprocess.TimeoutExpired:
        log.warning("Processes were still running %d s after being killed", KILL_WAIT)


def _taskkill_tree(pid: int) -> None:
    """End a process and its descendants on Windows.

    taskkill finds descendants by parent process id, which Windows reuses. That's fine for the
    shims we know, which wait for their child; a job object would avoid it.
    """
    # Windows reads environment names in any case. The full path keeps PATH out of it.
    taskkill = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
    command = [str(taskkill), "/T", "/F", "/PID", str(pid)]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=KILL_WAIT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("taskkill couldn't end the process tree: %s", error)
        return
    if result.returncode != 0:
        # It also fails when the processes exited just before, which is harmless.
        output = result.stdout.decode(errors="replace").strip()
        log.debug("taskkill exited with %d: %s", result.returncode, output)


def _kill_group(pid: int) -> None:
    """End the process group that `pid` leads, on macOS and Linux."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Every process in the group has already exited. macOS can give EPERM, not ESRCH, while
        # the exited processes aren't reaped yet.
        return


def _failure_summary(stderr: str, command: Sequence[str]) -> str:
    """The last lines of the program's stderr, which say what went wrong, with folders left out."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    summary = " / ".join(lines[-ERROR_LINES:]) or f"{_name(command)} failed without saying why"
    # Full paths only go in debug logs, so files are named without their folders.
    for argument in command[1:]:
        if os.path.isabs(argument):
            summary = summary.replace(argument, Path(argument).name)
    return summary
