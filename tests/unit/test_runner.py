"""Tests for running a helper program with a timeout and cancel, using small Python programs."""

import sys
import threading
import time
from pathlib import Path

import pytest

from scmp3.core import runner
from scmp3.core.errors import Cancelled, ErrorKind, ScmpError

KIND = ErrorKind.convert_failed

# Stands in for a Chocolatey or Scoop shim: it starts the real program and waits for it.
SHIM = """
import subprocess, sys
sys.exit(subprocess.Popen([sys.executable, *sys.argv[1:]]).wait())
"""
# Stands in for the real encoder: it keeps writing until it's killed, or for a minute at most.
WORKER = """
import sys, time
deadline = time.monotonic() + 60
with open(sys.argv[1], "a") as heartbeat:
    while time.monotonic() < deadline:
        heartbeat.write("x")
        heartbeat.flush()
        time.sleep(0.02)
"""
# Fails the way ffmpeg does: progress lines first, then the reason, naming its input file.
FAILING = """
import sys
print("banner", file=sys.stderr)
print("size=  10kB time=00:00:01", end="\\r", file=sys.stderr)
print("[in#0] Error opening input: Invalid data found", file=sys.stderr)
print(f"Error opening input file {sys.argv[1]}.", file=sys.stderr)
print("Error opening input files: Invalid data found", file=sys.stderr)
sys.exit(1)
"""


def python(*args: str) -> list[str]:
    """A command that runs this test run's Python."""
    return [sys.executable, *args]


def shim_command(folder: Path, heartbeat: Path) -> list[str]:
    """A command that runs WORKER behind SHIM, so the worker is a grandchild."""
    shim = folder / "shim.py"
    worker = folder / "worker.py"
    shim.write_text(SHIM)
    worker.write_text(WORKER)
    return python(str(shim), str(worker), str(heartbeat))


def cancel_when_written(path: Path, cancel: threading.Event) -> threading.Thread:
    """Set `cancel` as soon as `path` has data in it, from a background thread."""

    def watch() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size > 0:
                cancel.set()
                return
            time.sleep(0.01)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def assert_stopped_writing(heartbeat: Path) -> None:
    """Fail if anything is still writing to the heartbeat file."""
    size = heartbeat.stat().st_size
    time.sleep(0.4)
    assert heartbeat.stat().st_size == size


class SetAfterFirstCheck(threading.Event):
    """Reads as not set on its first check and as set after that, like a cancel during a run."""

    def __init__(self) -> None:
        super().__init__()
        self.checks = 0

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks > 1


def test_a_program_that_succeeds_returns_quietly() -> None:
    runner.run(python("-c", "pass"), timeout=30, error_kind=KIND)


def test_cancel_kills_the_whole_process_tree_behind_a_shim(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat.txt"
    cancel = threading.Event()
    watcher = cancel_when_written(heartbeat, cancel)
    with pytest.raises(Cancelled):
        runner.run(shim_command(tmp_path, heartbeat), timeout=30, error_kind=KIND, cancel=cancel)
    watcher.join(timeout=5)
    assert_stopped_writing(heartbeat)


def test_a_timeout_kills_the_whole_process_tree_behind_a_shim(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat.txt"
    with pytest.raises(ScmpError) as error_info:
        runner.run(shim_command(tmp_path, heartbeat), timeout=1.5, error_kind=KIND)
    assert error_info.value.kind is KIND
    assert error_info.value.detail == f"{Path(sys.executable).stem} didn't finish within 1.5 s"
    assert heartbeat.stat().st_size > 0
    assert_stopped_writing(heartbeat)


def test_a_cancel_set_before_the_start_runs_nothing(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        runner.run(
            python("-c", f"open({str(marker)!r}, 'w').close()"),
            timeout=30,
            error_kind=KIND,
            cancel=cancel,
        )
    assert not marker.exists()


def test_a_cancel_that_lands_as_the_program_exits_still_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A long poll means the only cancel check after the start is the one after the exit.
    monkeypatch.setattr(runner, "POLL_INTERVAL", 30)
    cancel = SetAfterFirstCheck()
    with pytest.raises(Cancelled):
        runner.run(python("-c", "pass"), timeout=60, error_kind=KIND, cancel=cancel)
    assert cancel.checks == 2


def test_a_failure_gives_the_last_lines_of_stderr_without_folders(tmp_path: Path) -> None:
    source = tmp_path / "Jörg 100% [x]" / "bad.wav"
    # UTF-8 output, as ffmpeg writes it.
    command = python("-X", "utf8", "-c", FAILING, str(source))
    with pytest.raises(ScmpError) as error_info:
        runner.run(command, timeout=30, error_kind=KIND)
    assert error_info.value.kind is KIND
    assert error_info.value.detail == (
        "[in#0] Error opening input: Invalid data found"
        " / Error opening input file bad.wav."
        " / Error opening input files: Invalid data found"
    )


def test_a_failure_without_output_says_so() -> None:
    with pytest.raises(ScmpError) as error_info:
        runner.run(python("-c", "raise SystemExit(3)"), timeout=30, error_kind=KIND)
    assert error_info.value.detail == f"{Path(sys.executable).stem} failed without saying why"


def test_a_program_that_wont_start_raises_the_given_error_kind(tmp_path: Path) -> None:
    with pytest.raises(ScmpError) as error_info:
        runner.run(
            [str(tmp_path / "no-deno.exe"), "--version"],
            timeout=30,
            error_kind=ErrorKind.engine_outdated,
        )
    assert error_info.value.kind is ErrorKind.engine_outdated
    assert error_info.value.detail is not None
    assert error_info.value.detail.startswith("can't start no-deno: ")
