"""Tests for finding the processes on a sign-in profile and ending them only while they're ours."""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scmp3.app import browser_processes, chromium
from scmp3.app.browser_processes import Listed

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="the sign-in window is Windows only"
)


def listing_of(*lines: str) -> Any:
    def run(*args: Any, **options: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="\n".join(lines))

    return run


def test_only_processes_naming_the_profile_are_ours(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = tmp_path / "chrome"
    monkeypatch.setattr(
        browser_processes.subprocess,
        "run",
        listing_of(
            f"11\t100\tchrome.exe --user-data-dir={profile} --headless=new",
            "12\t100\tC:\\Program Files\\Edge\\msedge.exe --profile-directory=Default",
            f'13\t100\tchrome.exe --type=crashpad-handler "--database={profile}\\Crashpad"',
            # Chromium's profile starts with Chrome's name, but isn't Chrome's.
            f"14\t100\tchrome.exe --user-data-dir={tmp_path / 'chromium'}",
            f"15\t100\tchrome.exe --user-data-dir={str(profile).upper()}",
            "not a process line",
        ),
    )
    assert chromium.processes_on(profile) == [11, 13, 15]
    # The crash handler is a helper, so only the browsers own windows.
    assert chromium.browsers_on(profile) == [11, 15]


def test_a_listing_that_fails_is_unknown_rather_than_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def blocked(*args: Any, **options: Any) -> subprocess.CompletedProcess[str]:
        raise PermissionError("blocked by policy")

    monkeypatch.setattr(browser_processes.subprocess, "run", blocked)
    assert chromium.processes_on(tmp_path) is None
    assert chromium.browsers_on(tmp_path) is None
    assert chromium.stop_processes_on(tmp_path) is False


def test_powershell_is_started_by_its_full_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    monkeypatch.setattr(browser_processes.subprocess, "run", run)
    browser_processes.processes_on(tmp_path)
    assert commands[0][0] == str(
        tmp_path / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )


def test_stopping_ends_the_browsers_first_then_lists_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = Listed(2, 100, "brave.exe --type=renderer")
    browser = Listed(1, 100, "brave.exe")
    listings = [[helper, browser], [helper], []]
    ended: list[int] = []
    monkeypatch.setattr(browser_processes, "processes_on", lambda profile: listings.pop(0))
    monkeypatch.setattr(browser_processes, "end", lambda item: ended.append(item.pid))
    assert browser_processes.stop_all_on(tmp_path) is True
    assert ended == [1, 2, 2]


def test_stopping_says_so_when_the_listing_fails_partway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    listings: list[list[Listed] | None] = [[Listed(1, 100, "brave.exe")], None]
    monkeypatch.setattr(browser_processes, "processes_on", lambda profile: listings.pop(0))
    monkeypatch.setattr(browser_processes, "end", lambda item: None)
    assert browser_processes.stop_all_on(tmp_path) is False


class FakeHandles:
    """Stands in for the handle calls, with each process id's start time."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, started: dict[int, int]) -> None:
        self.started = started
        self.ended: list[int] = []
        self.closed: list[int] = []
        monkeypatch.setattr(browser_processes, "CAN_END", True)
        monkeypatch.setattr(browser_processes, "_open", lambda pid: pid if pid in started else None)
        monkeypatch.setattr(browser_processes, "_say_why_not_opened", lambda pid: None)
        monkeypatch.setattr(browser_processes, "_creation_time", lambda handle: started[handle])
        # The ids of processes that have ended, though their handles still open.
        self.gone: set[int] = set()
        monkeypatch.setattr(browser_processes, "_has_ended", lambda handle: handle in self.gone)
        monkeypatch.setattr(browser_processes, "_terminate", self.ended.append)
        monkeypatch.setattr(browser_processes, "_close", self.closed.append)


def test_a_process_still_as_listed_is_ended_through_its_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles = FakeHandles(monkeypatch, {7: 1_000_000})
    # CIM's time is to the microsecond, so it can be a few 100 ns steps off Windows' own.
    browser_processes.end(Listed(7, 1_000_000 - 2, "brave.exe"))
    assert handles.ended == [7]
    assert handles.closed == [7]


def test_an_id_another_program_now_has_is_never_ended(monkeypatch: pytest.MonkeyPatch) -> None:
    handles = FakeHandles(monkeypatch, {7: 2_000_000_000})
    browser_processes.end(Listed(7, 1_000_000, "brave.exe"))
    assert handles.ended == []
    assert handles.closed == [7]


def test_a_helper_that_ended_with_its_browser_isnt_ended_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles = FakeHandles(monkeypatch, {7: 1_000_000})
    handles.gone.add(7)
    browser_processes.end(Listed(7, 1_000_000, "brave.exe --type=renderer"))
    assert handles.ended == []
    assert handles.closed == [7]


@pytest.mark.parametrize(("listed", "created"), [(0, 0), (0, 5), (1_000_000, -1), (0, -1)])
def test_an_unknown_start_time_never_counts_as_the_same(
    monkeypatch: pytest.MonkeyPatch, listed: int, created: int
) -> None:
    handles = FakeHandles(monkeypatch, {7: created})
    browser_processes.end(Listed(7, listed, "brave.exe"))
    assert handles.ended == []


def test_a_process_that_has_gone_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    handles = FakeHandles(monkeypatch, {})
    browser_processes.end(Listed(7, 1_000_000, "brave.exe"))
    assert handles.ended == [] and handles.closed == []


def sleeper(profile: Path) -> subprocess.Popen[bytes]:
    """A child process whose command line names the profile, as a browser's would."""
    code = "import time; time.sleep(60)"
    return subprocess.Popen([sys.executable, "-c", code, f"--user-data-dir={profile}"])


@windows_only
def test_a_real_process_on_the_profile_is_found_and_ended(tmp_path: Path) -> None:
    profile = tmp_path / "brave"
    child = sleeper(profile)
    try:
        # A venv's python.exe can start the interpreter as a child that names the profile too.
        assert child.pid in (chromium.processes_on(profile) or [])
        assert chromium.stop_processes_on(profile) is True
        assert child.wait(timeout=10) is not None
        assert chromium.processes_on(profile) == []
    finally:
        browser_processes.stop_all_on(profile)


@windows_only
def test_a_real_process_listed_with_another_start_time_keeps_running(tmp_path: Path) -> None:
    profile = tmp_path / "brave"
    child = sleeper(profile)
    try:
        found = browser_processes.processes_on(profile) or []
        listed = next(item for item in found if item.pid == child.pid)
        # As if the listed process had gone, and Windows had given its id to this one.
        browser_processes.end(Listed(child.pid, listed.started - 10_000_000, listed.command))
        assert child.poll() is None
        browser_processes.end(listed)
        assert child.wait(timeout=10) is not None
    finally:
        browser_processes.stop_all_on(profile)


@windows_only
def test_a_real_process_that_already_ended_isnt_reported_as_unstoppable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    profile = tmp_path / "brave"
    child = sleeper(profile)
    try:
        found = browser_processes.processes_on(profile) or []
        listed = next(item for item in found if item.pid == child.pid)
        child.kill()
        child.wait(timeout=10)
        # The Popen still holds the ended process's handle, so it opens as a helper's would.
        browser_processes.end(listed)
        assert "couldn't be stopped" not in caplog.text
    finally:
        browser_processes.stop_all_on(profile)
