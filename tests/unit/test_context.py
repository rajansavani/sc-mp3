"""Tests for the shared app context and the engine version it reports."""

import importlib.metadata
import logging
from pathlib import Path

import pytest

from scmp3.app import context
from scmp3.app.context import AppContext, bundled_engine_version


def test_the_app_context_uses_the_app_folders(scmp3_home: Path) -> None:
    app = AppContext.from_app_folders()
    assert app.settings.path.is_relative_to(scmp3_home)
    assert app.history.path.is_relative_to(scmp3_home)


def test_the_engine_version_comes_from_package_metadata() -> None:
    assert bundled_engine_version() == importlib.metadata.version("yt-dlp")


def test_missing_engine_metadata_leaves_the_version_empty_and_says_why(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def no_metadata(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(context.importlib.metadata, "version", no_metadata)
    with caplog.at_level(logging.WARNING, logger="scmp3.app.context"):
        assert bundled_engine_version() is None
    assert "yt-dlp's package metadata is missing" in caplog.text
