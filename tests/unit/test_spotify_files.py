"""Tests for finding Spotify's local-files.bnk for each way Spotify is installed."""

import os
from pathlib import Path

from scmp3.app.spotify_files import LOCAL_FILES, local_files_location

STORE_PACKAGE = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0"


def account(users: Path, name: str = "kaia-user", *, with_list: bool = True) -> Path:
    folder = users / name
    folder.mkdir(parents=True)
    if with_list:
        (folder / LOCAL_FILES).write_bytes(b"list")
    return folder


def windows_env(tmp_path: Path) -> dict[str, str]:
    return {
        "APPDATA": str(tmp_path / "Roaming"),
        "LOCALAPPDATA": str(tmp_path / "Local"),
    }


def installer_users(tmp_path: Path) -> Path:
    return tmp_path / "Roaming" / "Spotify" / "Users"


def store_users(tmp_path: Path) -> Path:
    return tmp_path / "Local" / "Packages" / STORE_PACKAGE / "LocalState" / "Spotify" / "Users"


def find_on_windows(tmp_path: Path) -> Path | None:
    return local_files_location(platform="win32", env=windows_env(tmp_path), home=tmp_path)


def test_the_installer_versions_list_is_found(tmp_path: Path) -> None:
    folder = account(installer_users(tmp_path))
    assert find_on_windows(tmp_path) == folder / LOCAL_FILES


def test_the_microsoft_store_versions_list_is_found(tmp_path: Path) -> None:
    folder = account(store_users(tmp_path))
    assert find_on_windows(tmp_path) == folder / LOCAL_FILES


def test_with_both_versions_the_newest_list_wins(tmp_path: Path) -> None:
    old = account(installer_users(tmp_path)) / LOCAL_FILES
    new = account(store_users(tmp_path)) / LOCAL_FILES
    os.utime(old, (1_000_000_000, 1_000_000_000))
    assert find_on_windows(tmp_path) == new


def test_an_account_without_a_list_yet_gives_its_folder(tmp_path: Path) -> None:
    folder = account(installer_users(tmp_path), with_list=False)
    assert find_on_windows(tmp_path) == folder


def test_folders_that_arent_accounts_are_passed_over(tmp_path: Path) -> None:
    users = installer_users(tmp_path)
    (users / "cache").mkdir(parents=True)
    (users / "notes-user").parent.mkdir(parents=True, exist_ok=True)
    (users / "notes-user").write_text("a file, not a folder")
    assert find_on_windows(tmp_path) is None


def test_no_spotify_gives_none(tmp_path: Path) -> None:
    assert find_on_windows(tmp_path) is None
    assert local_files_location(platform="win32", env={}, home=tmp_path) is None


def test_macos_looks_in_application_support(tmp_path: Path) -> None:
    users = tmp_path / "Library" / "Application Support" / "Spotify" / "Users"
    folder = account(users)
    assert local_files_location(platform="darwin", env={}, home=tmp_path) == folder / LOCAL_FILES


def test_linux_looks_in_the_config_folder(tmp_path: Path) -> None:
    folder = account(tmp_path / ".config" / "spotify" / "Users")
    assert local_files_location(platform="linux", env={}, home=tmp_path) == folder / LOCAL_FILES
    elsewhere = tmp_path / "xdg"
    moved = account(elsewhere / "spotify" / "Users", "lou-user")
    env = {"XDG_CONFIG_HOME": str(elsewhere)}
    assert local_files_location(platform="linux", env=env, home=tmp_path) == moved / LOCAL_FILES
