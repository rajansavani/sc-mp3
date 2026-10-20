"""Tests for running one copy of the app at a time."""

import logging
from pathlib import Path

import pytest

from scmp3.app import instance
from scmp3.app.message_box import MB_ICONINFORMATION, MB_OK
from scmp3.core import paths
from scmp3.core.filelock import FileLock


def test_a_second_copy_finds_the_first_one_open() -> None:
    lock_path = paths.data_dir() / instance.INSTANCE_LOCK
    first = FileLock(lock_path, timeout=0)
    assert instance.another_copy_is_open(first) is False
    second = FileLock(lock_path, timeout=0)
    assert instance.another_copy_is_open(second) is True
    first.release()
    assert instance.another_copy_is_open(second) is False
    second.release()


def test_a_folder_without_file_locks_still_opens_the_app(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocked = tmp_path / "a file, not a folder"
    blocked.write_bytes(b"")
    with caplog.at_level(logging.WARNING, logger="scmp3.app.instance"):
        assert instance.another_copy_is_open(FileLock(blocked / "sc-mp3.lock", timeout=0)) is False
    assert "Can't tell whether sc-mp3 is open already" in caplog.text


def test_a_second_copy_on_windows_says_so_in_a_message_box() -> None:
    shown: list[tuple[str, str, int]] = []

    def message_box(text: str, title: str, flags: int) -> int:
        shown.append((text, title, flags))
        return 1

    instance.say_already_open(message_box, platform="win32")
    assert shown == [("sc-mp3 is already open.", "sc-mp3", MB_OK | MB_ICONINFORMATION)]


def test_a_second_copy_elsewhere_says_so_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    instance.say_already_open(platform="linux")
    assert capsys.readouterr().err.splitlines() == ["sc-mp3 is already open."]
