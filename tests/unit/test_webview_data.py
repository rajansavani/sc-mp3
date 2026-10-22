"""Tests for WebView2's data folder: where it goes, and deleting it once the window has closed."""

import logging
import shutil
from pathlib import Path

import pytest

from scmp3.app import webview_data
from scmp3.app.webview_data import EarlyDeleteFilter, data_folder, remove_data_folder
from scmp3.core import paths


def make_folder(path: Path) -> Path:
    (path / "EBWebView").mkdir(parents=True)
    (path / "EBWebView" / "lockfile").write_bytes(b"")
    return path


def test_the_folder_is_in_sc_mp3s_cache() -> None:
    assert data_folder() == paths.cache_dir() / "webview"


def test_a_folder_no_longer_in_use_is_deleted(tmp_path: Path) -> None:
    folder = make_folder(tmp_path / "webview")
    assert remove_data_folder(folder, sleep=lambda seconds: None) is True
    assert not folder.exists()
    # Already gone counts as done.
    assert remove_data_folder(folder, sleep=lambda seconds: None) is True


def test_deleting_is_tried_again_while_webview2_still_holds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = make_folder(tmp_path / "webview")
    real_rmtree = shutil.rmtree
    busy = [PermissionError(13, "The process cannot access the file")] * 2
    pauses: list[float] = []

    def rmtree(path: Path) -> None:
        if busy:
            raise busy.pop()
        real_rmtree(path)

    monkeypatch.setattr(webview_data.shutil, "rmtree", rmtree)
    assert remove_data_folder(folder, sleep=pauses.append) is True
    assert pauses == [webview_data.DELETE_PAUSE] * 2
    assert not folder.exists()


def test_a_folder_still_in_use_after_every_try_is_left_for_next_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    folder = make_folder(tmp_path / "webview")

    def rmtree(path: Path) -> None:
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(webview_data.shutil, "rmtree", rmtree)
    with caplog.at_level(logging.DEBUG):
        assert remove_data_folder(folder, sleep=lambda seconds: None, tries=3) is False
    assert "still in use: The process cannot access the file" in caplog.text
    assert folder.exists()


def test_pywebviews_early_delete_warning_is_dropped_and_others_kept() -> None:
    early = logging.LogRecord(
        "pywebview",
        logging.WARNING,
        __file__,
        1,
        "Failed to delete user data folder: %s",
        ("x",),
        None,
    )
    other = logging.LogRecord(
        "pywebview", logging.WARNING, __file__, 1, "Something else", None, None
    )
    assert EarlyDeleteFilter().filter(early) is False
    assert EarlyDeleteFilter().filter(other) is True
