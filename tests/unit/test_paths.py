"""Tests for the app folders, SCMP3_HOME and finding ffmpeg."""

import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import platformdirs
import pytest

from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError

FOLDERS: list[Callable[[], Path]] = [
    paths.config_dir,
    paths.data_dir,
    paths.cache_dir,
    paths.log_dir,
    paths.staging_dir,
    paths.components_dir,
    paths.default_library_dir,
]
FFMPEG_NAME = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda folder: folder.__name__)
def test_every_folder_resolves_under_scmp3_home(
    folder: Callable[[], Path], scmp3_home: Path
) -> None:
    assert folder().is_relative_to(scmp3_home)


def test_folders_under_scmp3_home_are_all_different(scmp3_home: Path) -> None:
    resolved = [folder() for folder in FOLDERS]
    assert len(set(resolved)) == len(resolved)


def test_getting_a_folder_creates_nothing(scmp3_home: Path) -> None:
    for folder in FOLDERS:
        folder()
    assert list(scmp3_home.iterdir()) == []


def test_every_test_gets_scmp3_home_without_asking() -> None:
    assert paths.HOME_ENV in os.environ
    assert paths.config_dir().parent.name.startswith("scmp3_home")


@pytest.mark.real_app_folders
def test_a_real_app_folders_test_runs_without_scmp3_home(scmp3_home: Path | None) -> None:
    assert scmp3_home is None
    assert paths.HOME_ENV not in os.environ


@pytest.mark.real_app_folders
@pytest.mark.parametrize("folder", FOLDERS, ids=lambda folder: folder.__name__)
def test_without_scmp3_home_folders_carry_the_app_name_once(folder: Callable[[], Path]) -> None:
    assert folder().parts.count("sc-mp3") == 1


@pytest.mark.real_app_folders
def test_without_scmp3_home_the_library_is_in_the_music_folder() -> None:
    assert paths.default_library_dir() == Path(platformdirs.user_music_dir()) / "sc-mp3"


def test_staging_and_components_sit_in_cache_and_data(scmp3_home: Path) -> None:
    assert paths.staging_dir().parent == paths.cache_dir()
    assert paths.components_dir().parent == paths.data_dir()


@pytest.fixture
def no_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Hide every ffmpeg on this machine, so a test only finds the ones it creates."""
    monkeypatch.delenv(paths.FFMPEG_ENV, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path / "checkout")
    monkeypatch.setattr(paths.shutil, "which", lambda name: None)
    return tmp_path


def make_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def make_dev_ffmpeg(root: Path) -> Path:
    tag = paths.platform_tag()
    assert tag is not None
    return make_file(root / "checkout" / "packaging" / "bin" / tag / FFMPEG_NAME)


def fake_path_ffmpeg(monkeypatch: pytest.MonkeyPatch, found: Path) -> None:
    monkeypatch.setattr(
        paths.shutil, "which", lambda name: str(found) if name == "ffmpeg" else None
    )


def test_ffmpeg_env_var_comes_first(no_ffmpeg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chosen = make_file(no_ffmpeg / "custom" / FFMPEG_NAME)
    monkeypatch.setenv(paths.FFMPEG_ENV, str(chosen))
    make_dev_ffmpeg(no_ffmpeg)
    fake_path_ffmpeg(monkeypatch, no_ffmpeg / "on-path" / FFMPEG_NAME)
    assert paths.ffmpeg_exe() == chosen


def test_ffmpeg_env_var_pointing_nowhere_is_skipped_with_a_warning(
    no_ffmpeg: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(paths.FFMPEG_ENV, str(no_ffmpeg / "missing" / FFMPEG_NAME))
    dev = make_dev_ffmpeg(no_ffmpeg)
    with caplog.at_level(logging.WARNING, logger=paths.__name__):
        assert paths.ffmpeg_exe() == dev
    assert paths.FFMPEG_ENV in caplog.text


def test_ffmpeg_next_to_the_frozen_app_beats_the_dev_checkout(
    no_ffmpeg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir = no_ffmpeg / "app"
    bundled = make_file(app_dir / FFMPEG_NAME)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app_dir / "sc-mp3.exe"))
    make_dev_ffmpeg(no_ffmpeg)
    assert paths.ffmpeg_exe() == bundled


def test_ffmpeg_in_the_pyinstaller_contents_folder_is_found(
    no_ffmpeg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = no_ffmpeg / "app" / "_internal"
    bundled = make_file(contents / FFMPEG_NAME)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(no_ffmpeg / "app" / "sc-mp3.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(contents), raising=False)
    assert paths.ffmpeg_exe() == bundled


def test_ffmpeg_in_the_dev_checkout_beats_path(
    no_ffmpeg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev = make_dev_ffmpeg(no_ffmpeg)
    fake_path_ffmpeg(monkeypatch, no_ffmpeg / "on-path" / FFMPEG_NAME)
    assert paths.ffmpeg_exe() == dev


def test_ffmpeg_on_path_is_the_last_resort(
    no_ffmpeg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_path = no_ffmpeg / "on-path" / FFMPEG_NAME
    fake_path_ffmpeg(monkeypatch, on_path)
    assert paths.ffmpeg_exe() == on_path


def test_missing_ffmpeg_is_a_convert_failed_error(no_ffmpeg: Path) -> None:
    with pytest.raises(ScmpError) as error_info:
        paths.ffmpeg_exe()
    assert error_info.value.kind is ErrorKind.convert_failed
    assert error_info.value.detail == "ffmpeg not found"


def test_platform_tag_names_this_machine() -> None:
    assert paths.platform_tag() in {
        "windows-x64",
        "windows-arm64",
        "macos-x64",
        "macos-arm64",
        "linux-x64",
        "linux-arm64",
    }


@pytest.mark.parametrize(
    ("machine", "arch"),
    [("AMD64", "x64"), ("x86_64", "x64"), ("arm64", "arm64"), ("aarch64", "arm64")],
)
def test_platform_tag_maps_cpu_names(
    machine: str, arch: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths.platform, "machine", lambda: machine)
    assert paths.platform_tag() == f"{paths.OS_NAMES[sys.platform]}-{arch}"


def test_platform_tag_is_none_for_a_cpu_we_dont_ship(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.platform, "machine", lambda: "riscv64")
    assert paths.platform_tag() is None


def test_write_text_atomically_writes_utf8_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "new" / "queue.json"
    paths.write_text_atomically(target, "Sälen 夜\n")
    paths.write_text_atomically(target, "second\n")
    assert target.read_bytes() == b"second\n"
    assert [entry.name for entry in target.parent.iterdir()] == ["queue.json"]


def test_a_failed_atomic_write_keeps_the_old_file_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "queue.json"
    target.write_text("old", encoding="utf-8")

    def failing_replace(src: str | Path, dst: str | Path) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(paths.os, "replace", failing_replace)
    with pytest.raises(ScmpError) as error_info:
        paths.write_text_atomically(target, "new")
    assert error_info.value.kind is ErrorKind.disk
    assert error_info.value.detail == "can't write queue.json: Access is denied"
    assert target.read_text(encoding="utf-8") == "old"
    assert [entry.name for entry in tmp_path.iterdir()] == ["queue.json"]


def names_from(monkeypatch: pytest.MonkeyPatch, *tokens: str) -> None:
    """Make the temp file names' random parts these, in order."""
    queue = iter(tokens)
    monkeypatch.setattr(paths.secrets, "token_hex", lambda size: next(queue))


def test_a_temp_file_is_new_open_and_named_with_the_prefix(tmp_path: Path) -> None:
    handle, temp = paths.create_temp_file(tmp_path, ".queue.json.", ".tmp")
    os.write(handle, b"a\nb")
    os.close(handle)
    assert temp.parent == tmp_path
    assert temp.name.startswith(".queue.json.") and temp.name.endswith(".tmp")
    # Binary, so Windows doesn't turn the newline into two bytes.
    assert temp.read_bytes() == b"a\nb"


def test_a_taken_temp_name_is_tried_again_with_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "x-taken").write_bytes(b"")
    names_from(monkeypatch, "taken", "free")
    handle, temp = paths.create_temp_file(tmp_path, "x-")
    os.close(handle)
    assert temp.name == "x-free"


def test_temp_names_give_up_after_a_few_taken_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "x-taken").write_bytes(b"")
    names_from(monkeypatch, *["taken"] * paths.TEMP_NAME_TRIES)
    with pytest.raises(FileExistsError):
        paths.create_temp_file(tmp_path, "x-")


def test_a_folder_that_refuses_new_files_fails_at_once_without_another_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def refuse(path: str | Path, flags: int, mode: int = 0o777) -> int:
        calls.append(str(path))
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(paths.os, "open", refuse)
    with pytest.raises(PermissionError):
        paths.create_temp_file(tmp_path, "x-")
    assert len(calls) == 1


def test_an_atomic_write_to_a_folder_that_refuses_new_files_is_a_disk_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "queue.json"
    target.write_text("old", encoding="utf-8")

    def refuse(path: str | Path, flags: int, mode: int = 0o777) -> int:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(paths.os, "open", refuse)
    with pytest.raises(ScmpError) as error_info:
        paths.write_text_atomically(target, "new")
    assert error_info.value.kind is ErrorKind.disk
    assert target.read_text(encoding="utf-8") == "old"


def test_backup_path_adds_the_time_and_a_counter_when_taken(tmp_path: Path) -> None:
    when = datetime(2026, 10, 18, 15, 30, 0)
    original = tmp_path / "history.db"
    first = paths.backup_path(original, when)
    assert first.name == "history.bad-20261018-153000.db"
    first.write_bytes(b"")
    assert paths.backup_path(original, when).name == "history.bad-20261018-153000-2.db"
