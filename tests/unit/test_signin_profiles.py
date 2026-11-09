"""Tests for the browser profiles the sign-in window keeps between sign-ins, and deleting them."""

import sys
from pathlib import Path

import pytest

from scmp3.app import chromium
from scmp3.core import paths


@pytest.fixture
def stopped(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The profiles whose processes were stopped, by folder name. Nothing real runs on them."""
    names: list[str] = []

    def stop_processes_on(profile: Path) -> bool:
        names.append(profile.name)
        return True

    monkeypatch.setattr(chromium, "stop_processes_on", stop_processes_on)
    return names


def kept(browser_id: str) -> Path:
    profile = chromium.kept_profile(browser_id)
    (profile / "Default").mkdir(exist_ok=True)
    return profile


def test_each_browser_keeps_one_profile_in_the_data_folder() -> None:
    brave, edge = chromium.kept_profile("brave"), chromium.kept_profile("edge")
    assert brave == paths.data_dir() / "signin" / "brave"
    assert edge.parent == brave.parent and edge != brave
    assert brave.is_dir()


def test_a_kept_profile_is_the_same_folder_with_what_it_held(stopped: list[str]) -> None:
    profile = kept("brave")
    (profile / "Default" / "Cookies").write_bytes(b"Google's session")
    assert chromium.kept_profile("brave") == profile
    assert (profile / "Default" / "Cookies").exists()


def test_the_startup_sweep_keeps_the_kept_profiles(stopped: list[str]) -> None:
    profile = kept("brave")
    chromium.remove_leftover_profiles()
    assert profile.is_dir()
    assert stopped == []


def test_the_startup_sweep_deletes_what_a_sign_out_left(stopped: list[str]) -> None:
    left = chromium.profiles_root() / f"{chromium.REMOVED_PREFIX}abc"
    (left / "Default").mkdir(parents=True)
    chromium.remove_leftover_profiles()
    assert not left.exists()
    assert stopped == [left.name]


def test_signing_out_deletes_every_kept_profile(stopped: list[str]) -> None:
    kept("brave")
    kept("edge")
    chromium.delete_profiles()
    assert list(chromium.profiles_root().iterdir()) == []
    assert sorted(stopped) == ["brave", "edge"]


def test_a_profile_that_cant_be_renamed_is_deleted_in_place(
    stopped: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = kept("brave")

    def refuse(self: Path, target: Path) -> Path:
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "rename", refuse)
    chromium.forget_profile(profile)
    assert not profile.exists()


def test_a_profile_still_in_use_is_set_aside_for_the_next_start(
    stopped: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = kept("brave")
    monkeypatch.setattr(chromium, "delete_profile", lambda profile, tries=1: False)
    chromium.forget_profile(profile)
    [left] = list(chromium.profiles_root().iterdir())
    assert left.name.startswith(chromium.REMOVED_PREFIX)
    # A new sign-in starts on an empty profile rather than the one set aside.
    assert list(chromium.kept_profile("brave").iterdir()) == []


def test_forgetting_tabs_deletes_the_saved_tabs_and_keeps_the_rest() -> None:
    profile = kept("brave")
    default = profile / "Default"
    for folder in ("Sessions", "Sessions_Encrypted", "Session Storage"):
        (default / folder).mkdir()
        (default / folder / "Tabs_1").write_bytes(b"saved tabs")
    for name in ("Current Session", "Last Tabs", "Cookies"):
        (default / name).write_bytes(b"saved")
    chromium.forget_tabs(profile)
    # Session Storage is the pages' own sessionStorage, not the tabs.
    assert sorted(item.name for item in default.iterdir()) == ["Cookies", "Session Storage"]


def test_forgetting_tabs_on_a_new_profile_is_fine() -> None:
    profile = chromium.kept_profile("edge")
    chromium.forget_tabs(profile)
    assert list(profile.iterdir()) == []


def test_saved_tabs_that_cant_be_deleted_dont_stop_the_window(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    profile = kept("brave")
    (profile / "Default" / "Sessions").mkdir()

    def refuse(path: Path) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr(chromium.shutil, "rmtree", refuse)
    chromium.forget_tabs(profile)
    assert "Couldn't delete the tabs a sign-in profile saved" in caplog.text


def test_no_profiles_folder_is_fine(stopped: list[str]) -> None:
    chromium.remove_leftover_profiles()
    chromium.delete_profiles()
    assert stopped == []


def test_a_profile_with_no_lockfile_has_no_browser_on_it() -> None:
    assert chromium.profile_in_use(kept("brave")) is False


def test_a_lockfile_left_behind_doesnt_count_and_isnt_changed() -> None:
    profile = kept("brave")
    (profile / chromium.LOCK_FILE).write_bytes(b"left")
    assert chromium.profile_in_use(profile) is False
    assert (profile / chromium.LOCK_FILE).read_bytes() == b"left"


@pytest.mark.skipif(sys.platform != "win32", reason="Chromium's lockfile is Windows only")
def test_a_lockfile_held_as_a_browser_holds_it_means_one_runs() -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    profile = kept("brave")
    lock = profile / chromium.LOCK_FILE
    # As Chromium opens it: for writing, shared with nothing, deleted when closed.
    generic_write, create_always, delete_on_close = 0x40000000, 2, 0x04000000
    handle = kernel32.CreateFileW(
        str(lock), generic_write, 0, None, create_always, delete_on_close, None
    )
    assert handle not in (None, wintypes.HANDLE(-1).value)
    try:
        assert chromium.profile_in_use(profile) is True
    finally:
        kernel32.CloseHandle(handle)
    assert not lock.exists()
    assert chromium.profile_in_use(profile) is False
