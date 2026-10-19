"""Tests for moving files into the library, re-tagging in place, and cleaning up after a crash."""

import errno
import logging
import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from scmp3.core import placing
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.placing import atomic_place, remove_leftovers, retag_in_place

NEW = b"ID3 the new audio"
OLD = b"ID3 the old audio"
# A fixed "now" for remove_leftovers, so file ages don't depend on the real clock.
NOW = 2_000_000_000.0


def make_file(path: Path, content: bytes = b"", *, modified: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if modified is not None:
        os.utime(path, (modified, modified))
    return path


def names_in(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


def fail_on_purpose(message: str) -> Callable[..., None]:
    """A stand-in that fails the test if it's ever called."""

    def stand_in(*args: object) -> None:
        pytest.fail(message)

    return stand_in


def copy_half_then_run_out_of_space(source: str | Path, target: str | Path) -> None:
    """A copyfile that writes half the bytes, then fails like a full disk."""
    data = Path(source).read_bytes()
    Path(target).write_bytes(data[: len(data) // 2])
    raise OSError(errno.ENOSPC, "No space left on device")


def lock_error(winerror: int) -> OSError:
    """What Windows raises while Defender or the search indexer has a file open for a moment."""
    error = PermissionError(13, "The process cannot access the file")
    error.winerror = winerror
    return error


def access_denied() -> OSError:
    """A permission error that isn't a brief lock, so it's never retried."""
    return PermissionError(13, "Access is denied")


class RecordingSleep:
    """A stand-in for time.sleep that only notes how long it was asked to wait."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


@pytest.fixture
def sleep() -> RecordingSleep:
    return RecordingSleep()


def fail_unlink_of(monkeypatch: pytest.MonkeyPatch, refused: Path) -> None:
    """Make deleting `refused` fail, as Windows does while another program has it open."""
    real_unlink = Path.unlink

    def unlink(path: Path, missing_ok: bool = False) -> None:
        if path == refused:
            raise access_denied()
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def test_atomic_place_moves_the_file_into_a_new_folder(tmp_path: Path) -> None:
    src = make_file(tmp_path / "staging" / "out.mp3", NEW)
    dest = tmp_path / "library" / "Kaia - Song.mp3"
    assert atomic_place(src, dest) == dest
    assert dest.read_bytes() == NEW
    assert not src.exists()
    assert names_in(dest.parent) == ["Kaia - Song.mp3"]


def test_on_the_same_drive_the_file_is_renamed_straight_into_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = make_file(tmp_path / "staging" / "out.mp3", NEW)
    dest = tmp_path / "library" / "Kaia - Song.mp3"
    renames: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(source: str | Path, target: str | Path) -> None:
        renames.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(placing.os, "replace", recording_replace)
    monkeypatch.setattr(placing.shutil, "copyfile", fail_on_purpose("copied on the same drive"))
    atomic_place(src, dest)
    assert renames == [(src, dest)]


def test_atomic_place_replaces_a_file_already_there(tmp_path: Path) -> None:
    src = make_file(tmp_path / "staging" / "out.mp3", NEW)
    dest = make_file(tmp_path / "library" / "Kaia - Song.mp3", OLD)
    atomic_place(src, dest)
    assert dest.read_bytes() == NEW


def test_a_failed_rename_leaves_the_source_and_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleep: RecordingSleep
) -> None:
    src = make_file(tmp_path / "staging" / "out.mp3", NEW)
    dest = make_file(tmp_path / "library" / "Kaia - Song.mp3", OLD)

    def denied_replace(source: str | Path, target: str | Path) -> None:
        raise access_denied()

    monkeypatch.setattr(placing.os, "replace", denied_replace)
    with pytest.raises(ScmpError) as error_info:
        atomic_place(src, dest, sleep=sleep)
    assert error_info.value.kind is ErrorKind.disk
    # Only the file's name goes in the detail, not the folders above it.
    assert error_info.value.detail == "can't write Kaia - Song.mp3: Access is denied"
    assert sleep.waits == []
    assert dest.read_bytes() == OLD
    assert src.read_bytes() == NEW
    assert names_in(dest.parent) == ["Kaia - Song.mp3"]


def test_a_rename_error_from_the_same_drive_isnt_retried_as_a_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = make_file(tmp_path / "staging" / "out.mp3", NEW)
    dest = tmp_path / "library" / "Kaia - Song.mp3"

    def denied_replace(source: str | Path, target: str | Path) -> None:
        raise access_denied()

    monkeypatch.setattr(placing.os, "replace", denied_replace)
    monkeypatch.setattr(placing.shutil, "copyfile", fail_on_purpose("copied after a denied rename"))
    with pytest.raises(ScmpError):
        atomic_place(src, dest)
    assert src.read_bytes() == NEW
    assert names_in(dest.parent) == []


def test_a_missing_source_is_a_disk_error_that_leaves_nothing(tmp_path: Path) -> None:
    dest = tmp_path / "library" / "Kaia - Song.mp3"
    with pytest.raises(ScmpError) as error_info:
        atomic_place(tmp_path / "staging" / "gone.mp3", dest)
    assert error_info.value.kind is ErrorKind.disk
    assert names_in(dest.parent) == []


@pytest.mark.parametrize("winerror", [5, 32])
def test_a_brief_lock_is_waited_out(
    winerror: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleep: RecordingSleep
) -> None:
    src = make_file(tmp_path / "staging" / "out.mp3", NEW)
    dest = make_file(tmp_path / "library" / "Kaia - Song.mp3", OLD)
    failures_left = 2
    real_replace = os.replace

    def locked_twice_replace(source: str | Path, target: str | Path) -> None:
        nonlocal failures_left
        if failures_left:
            failures_left -= 1
            raise lock_error(winerror)
        real_replace(source, target)

    monkeypatch.setattr(placing.os, "replace", locked_twice_replace)
    atomic_place(src, dest, sleep=sleep)
    assert sleep.waits == [0.1, 0.2]
    assert dest.read_bytes() == NEW


def test_a_lock_that_doesnt_clear_is_a_disk_error_after_about_2_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleep: RecordingSleep
) -> None:
    src = make_file(tmp_path / "staging" / "out.mp3", NEW)
    dest = make_file(tmp_path / "library" / "Kaia - Song.mp3", OLD)
    tries: list[Path] = []

    def locked_replace(source: str | Path, target: str | Path) -> None:
        tries.append(Path(target))
        raise lock_error(32)

    monkeypatch.setattr(placing.os, "replace", locked_replace)
    with pytest.raises(ScmpError) as error_info:
        atomic_place(src, dest, sleep=sleep)
    assert error_info.value.kind is ErrorKind.disk
    assert len(tries) == 6
    assert len(sleep.waits) == 5
    assert sum(sleep.waits) == pytest.approx(2.0)
    assert dest.read_bytes() == OLD
    assert src.read_bytes() == NEW


def exdev_error() -> OSError:
    return OSError(errno.EXDEV, "Invalid cross-device link")


def not_same_device_error() -> OSError:
    """The error Windows raises, checked by its winerror alone."""
    error = OSError(0, "The system cannot move the file to a different disk drive")
    error.winerror = placing.ERROR_NOT_SAME_DEVICE
    return error


@pytest.fixture(params=[exdev_error, not_same_device_error], ids=["exdev", "winerror_17"])
def two_drives(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Two folders that os.replace treats as separate drives, refusing to rename between them."""
    make_error: Callable[[], OSError] = request.param
    real_replace = os.replace

    def drive_of(path: str | Path) -> str:
        return Path(path).relative_to(tmp_path).parts[0]

    def cross_drive_replace(source: str | Path, target: str | Path) -> None:
        if drive_of(source) != drive_of(target):
            raise make_error()
        real_replace(source, target)

    monkeypatch.setattr(placing.os, "replace", cross_drive_replace)
    return tmp_path / "C", tmp_path / "D"


def test_atomic_place_copies_a_file_from_another_drive(two_drives: tuple[Path, Path]) -> None:
    drive_c, drive_d = two_drives
    src = make_file(drive_c / "staging" / "out.mp3", NEW)
    dest = drive_d / "Music" / "Kaia - Song.mp3"
    assert atomic_place(src, dest) == dest
    assert dest.read_bytes() == NEW
    assert not src.exists()
    assert names_in(dest.parent) == ["Kaia - Song.mp3"]


def test_from_another_drive_the_copy_goes_through_a_hidden_partial_file(
    two_drives: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    drive_c, drive_d = two_drives
    src = make_file(drive_c / "staging" / "out.mp3", NEW)
    dest = drive_d / "Music" / "Kaia - Song.mp3"
    copies: list[tuple[Path, Path]] = []
    real_copyfile = shutil.copyfile

    def recording_copyfile(source: str | Path, target: str | Path) -> None:
        copies.append((Path(source), Path(target)))
        real_copyfile(source, target)

    monkeypatch.setattr(placing.shutil, "copyfile", recording_copyfile)
    atomic_place(src, dest)
    assert copies == [(src, dest.parent / ".Kaia - Song.mp3.partial")]


def test_a_copy_overwrites_a_partial_file_left_by_a_crash(two_drives: tuple[Path, Path]) -> None:
    drive_c, drive_d = two_drives
    src = make_file(drive_c / "out.mp3", NEW)
    dest = drive_d / "Kaia - Song.mp3"
    make_file(drive_d / ".Kaia - Song.mp3.partial", b"half")
    atomic_place(src, dest)
    assert dest.read_bytes() == NEW
    assert names_in(drive_d) == ["Kaia - Song.mp3"]


def test_a_copy_that_fails_partway_leaves_no_partial_and_changes_nothing(
    two_drives: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    drive_c, drive_d = two_drives
    src = make_file(drive_c / "out.mp3", NEW)
    dest = make_file(drive_d / "Kaia - Song.mp3", OLD)
    monkeypatch.setattr(placing.shutil, "copyfile", copy_half_then_run_out_of_space)
    with pytest.raises(ScmpError) as error_info:
        atomic_place(src, dest)
    assert error_info.value.kind is ErrorKind.disk
    assert dest.read_bytes() == OLD
    assert src.read_bytes() == NEW
    assert names_in(drive_d) == ["Kaia - Song.mp3"]


def test_a_failed_swap_after_a_copy_deletes_the_copy(
    two_drives: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    drive_c, drive_d = two_drives
    src = make_file(drive_c / "out.mp3", NEW)
    dest = make_file(drive_d / "Kaia - Song.mp3", OLD)
    cross_drive_replace = placing.os.replace

    def locked_dest_replace(source: str | Path, target: str | Path) -> None:
        if Path(source).parent == dest.parent:
            raise access_denied()
        cross_drive_replace(source, target)

    monkeypatch.setattr(placing.os, "replace", locked_dest_replace)
    with pytest.raises(ScmpError):
        atomic_place(src, dest)
    assert dest.read_bytes() == OLD
    assert src.read_bytes() == NEW
    assert names_in(drive_d) == ["Kaia - Song.mp3"]


def test_a_source_that_cant_be_deleted_after_a_copy_is_only_logged(
    two_drives: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    drive_c, drive_d = two_drives
    src = make_file(drive_c / "out.mp3", NEW)
    dest = drive_d / "Kaia - Song.mp3"
    fail_unlink_of(monkeypatch, src)
    with caplog.at_level(logging.WARNING, logger=placing.__name__):
        assert atomic_place(src, dest) == dest
    assert dest.read_bytes() == NEW
    assert "Couldn't delete out.mp3: Access is denied" in caplog.text


def test_a_partial_that_cant_be_deleted_is_logged_and_the_first_error_raised(
    two_drives: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    drive_c, drive_d = two_drives
    src = make_file(drive_c / "out.mp3", NEW)
    dest = drive_d / "Kaia - Song.mp3"
    monkeypatch.setattr(placing.shutil, "copyfile", copy_half_then_run_out_of_space)
    fail_unlink_of(monkeypatch, drive_d / ".Kaia - Song.mp3.partial")
    with (
        caplog.at_level(logging.WARNING, logger=placing.__name__),
        pytest.raises(ScmpError) as error_info,
    ):
        atomic_place(src, dest)
    assert error_info.value.detail == "can't write Kaia - Song.mp3: No space left on device"
    assert "Couldn't delete .Kaia - Song.mp3.partial" in caplog.text
    assert src.read_bytes() == NEW


def test_retag_writes_to_a_hidden_copy_then_swaps_it_in(tmp_path: Path) -> None:
    song = make_file(tmp_path / "Kaia - Song.mp3", OLD)
    seen: list[tuple[Path, bytes]] = []

    def write_tags(copy: Path) -> None:
        seen.append((copy, copy.read_bytes()))
        copy.write_bytes(NEW)

    retag_in_place(song, write_tags)
    assert seen == [(tmp_path / ".Kaia - Song.mp3.retag.tmp", OLD)]
    assert song.read_bytes() == NEW
    assert names_in(tmp_path) == ["Kaia - Song.mp3"]


def test_retag_gives_the_file_a_new_modification_time(tmp_path: Path) -> None:
    long_ago = 1_000_000_000
    song = make_file(tmp_path / "Kaia - Song.mp3", OLD, modified=long_ago)
    retag_in_place(song, lambda copy: None)
    assert song.stat().st_mtime > long_ago


def test_a_tag_write_that_fails_partway_leaves_the_original_and_no_temp_file(
    tmp_path: Path,
) -> None:
    song = make_file(tmp_path / "Kaia - Song.mp3", OLD)

    def write_half_then_fail(copy: Path) -> None:
        copy.write_bytes(NEW[:4])
        raise ValueError("bad frame")

    with pytest.raises(ValueError, match="bad frame"):
        retag_in_place(song, write_half_then_fail)
    assert song.read_bytes() == OLD
    assert names_in(tmp_path) == ["Kaia - Song.mp3"]


def test_a_tagger_error_passes_through_unchanged(tmp_path: Path) -> None:
    song = make_file(tmp_path / "Kaia - Song.mp3", OLD)
    tagger_error = ScmpError(ErrorKind.internal, "can't read the ID3 header")

    def write_tags(copy: Path) -> None:
        raise tagger_error

    with pytest.raises(ScmpError) as error_info:
        retag_in_place(song, write_tags)
    assert error_info.value is tagger_error
    assert names_in(tmp_path) == ["Kaia - Song.mp3"]


def test_a_disk_error_while_tagging_is_a_disk_error(tmp_path: Path) -> None:
    song = make_file(tmp_path / "Kaia - Song.mp3", OLD)

    def write_tags(copy: Path) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    with pytest.raises(ScmpError) as error_info:
        retag_in_place(song, write_tags)
    assert error_info.value.kind is ErrorKind.disk
    assert error_info.value.detail == "can't write Kaia - Song.mp3: No space left on device"
    assert song.read_bytes() == OLD
    assert names_in(tmp_path) == ["Kaia - Song.mp3"]


def test_a_copy_for_retagging_that_fails_partway_leaves_the_original_and_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song = make_file(tmp_path / "Kaia - Song.mp3", OLD)
    monkeypatch.setattr(placing.shutil, "copyfile", copy_half_then_run_out_of_space)
    with pytest.raises(ScmpError) as error_info:
        retag_in_place(song, fail_on_purpose("tagged a copy that failed"))
    assert error_info.value.kind is ErrorKind.disk
    assert song.read_bytes() == OLD
    assert names_in(tmp_path) == ["Kaia - Song.mp3"]


def test_a_failed_swap_after_retagging_leaves_the_original_and_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleep: RecordingSleep
) -> None:
    song = make_file(tmp_path / "Kaia - Song.mp3", OLD)

    def denied_replace(source: str | Path, target: str | Path) -> None:
        raise access_denied()

    monkeypatch.setattr(placing.os, "replace", denied_replace)
    with pytest.raises(ScmpError) as error_info:
        retag_in_place(song, lambda copy: copy.write_bytes(NEW), sleep=sleep)
    assert error_info.value.kind is ErrorKind.disk
    assert sleep.waits == []
    assert song.read_bytes() == OLD
    assert names_in(tmp_path) == ["Kaia - Song.mp3"]


def test_retag_waits_out_a_brief_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleep: RecordingSleep
) -> None:
    song = make_file(tmp_path / "Kaia - Song.mp3", OLD)
    failures_left = 1
    real_replace = os.replace

    def locked_once_replace(source: str | Path, target: str | Path) -> None:
        nonlocal failures_left
        if failures_left:
            failures_left -= 1
            raise lock_error(32)
        real_replace(source, target)

    monkeypatch.setattr(placing.os, "replace", locked_once_replace)
    retag_in_place(song, lambda copy: copy.write_bytes(NEW), sleep=sleep)
    assert sleep.waits == [0.1]
    assert song.read_bytes() == NEW


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no permission bits")
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o664, 0o444])
def test_retag_keeps_the_files_permissions(mode: int, tmp_path: Path) -> None:
    song = make_file(tmp_path / "Kaia - Song.mp3", OLD)
    song.chmod(mode)
    retag_in_place(song, lambda copy: copy.write_bytes(NEW))
    assert song.read_bytes() == NEW
    assert stat.S_IMODE(song.stat().st_mode) == mode


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to replace it")
def test_a_read_only_file_on_windows_is_left_as_it_was(
    tmp_path: Path, sleep: RecordingSleep
) -> None:
    song = make_file(tmp_path / "Kaia - Song.mp3", OLD)
    song.chmod(stat.S_IREAD)
    try:
        with pytest.raises(ScmpError) as error_info:
            retag_in_place(song, lambda copy: copy.write_bytes(NEW), sleep=sleep)
        assert error_info.value.kind is ErrorKind.disk
        assert song.read_bytes() == OLD
        assert names_in(tmp_path) == ["Kaia - Song.mp3"]
    finally:
        song.chmod(stat.S_IREAD | stat.S_IWRITE)


def make_old(path: Path) -> Path:
    """A temp file last written 10 minutes and a second ago."""
    return make_file(path, b"half", modified=NOW - placing.LEFTOVER_AGE - 1)


def test_remove_leftovers_deletes_old_temp_files_in_the_library_and_album_folders(
    tmp_path: Path,
) -> None:
    partial = make_old(tmp_path / ".Kaia - Song.mp3.partial")
    retag = make_old(tmp_path / "Kaia - Night Shift" / ".03 Kaia - Song.mp3.retag.tmp")
    removed = remove_leftovers(tmp_path, now=lambda: NOW)
    assert sorted(removed) == sorted([partial, retag])
    assert not partial.exists()
    assert not retag.exists()


@pytest.mark.parametrize(
    "name",
    [
        "Kaia - Song.mp3.partial",
        ".Kaia - Song.mp3.part",
        ".Kaia - Song.mp3.partial.bak",
        ".Kaia - Song.mp3.PARTIAL",
        ".Kaia - Song.mp3.tmp",
        ".Kaia - Song.mp3.retag.tmp2",
        "Kaia - Song.mp3.retag.tmp",
        ".notes.partial",
        "..Kaia - Song.mp3.partial",
        ".partial",
        ".retag.tmp",
        "Kaia - Song.mp3",
    ],
)
def test_remove_leftovers_leaves_look_alike_names_alone(name: str, tmp_path: Path) -> None:
    kept = make_old(tmp_path / name)
    assert remove_leftovers(tmp_path, now=lambda: NOW) == []
    assert kept.exists()


def test_remove_leftovers_leaves_temp_files_a_running_save_may_still_use(tmp_path: Path) -> None:
    fresh = make_file(
        tmp_path / ".Kaia - Song.mp3.partial", modified=NOW - placing.LEFTOVER_AGE + 1
    )
    assert remove_leftovers(tmp_path, now=lambda: NOW) == []
    assert fresh.exists()


def test_remove_leftovers_leaves_a_folder_with_a_temp_name_alone(tmp_path: Path) -> None:
    folder = tmp_path / ".Kaia - Song.mp3.partial"
    folder.mkdir()
    os.utime(folder, (NOW - 3600, NOW - 3600))
    assert remove_leftovers(tmp_path, now=lambda: NOW) == []
    assert folder.is_dir()


def test_remove_leftovers_before_the_first_save_does_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=placing.__name__):
        assert remove_leftovers(tmp_path / "not yet made", now=lambda: NOW) == []
    assert caplog.text == ""


def test_remove_leftovers_logs_a_file_it_cant_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stuck = make_old(tmp_path / ".Kaia - Song.mp3.partial")
    fail_unlink_of(monkeypatch, stuck)
    with caplog.at_level(logging.WARNING, logger=placing.__name__):
        assert remove_leftovers(tmp_path, now=lambda: NOW) == []
    assert "Couldn't delete .Kaia - Song.mp3.partial" in caplog.text
