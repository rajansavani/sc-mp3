"""Tests for the probe's screenshot folder, with no window."""

import sys
from pathlib import Path

import pytest

if sys.platform != "win32":
    pytest.skip("the probe loads WebView2 through pythonnet", allow_module_level=True)

from probe import new_shots_folder


def test_new_shots_folder_creates_a_missing_folder(tmp_path: Path) -> None:
    folder = new_shots_folder(tmp_path / "does-not-exist-yet")

    assert folder.is_dir()


def test_new_shots_folder_never_reuses_an_earlier_run(tmp_path: Path) -> None:
    first = new_shots_folder(tmp_path)
    second = new_shots_folder(tmp_path)

    assert first != second
    assert first.is_dir() and second.is_dir()
