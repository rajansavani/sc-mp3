"""Tests for the shared app context and the engine version it reports."""

import importlib.metadata
import logging
from pathlib import Path

import pytest

from scmp3.app import context
from scmp3.app.context import AppContext, bundled_engine_version
from scmp3.app.events import EventBus
from scmp3.app.jobs import JobManager
from scmp3.core.history import History
from scmp3.core.models import Settings
from scmp3.core.settings import SettingsStore


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


def test_the_context_builds_a_job_manager_on_its_own_collaborators() -> None:
    app = AppContext.from_app_folders()
    assert isinstance(app.jobs, JobManager)
    assert app.jobs.views() == []


def test_the_apps_job_manager_follows_saved_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    heard: list[Settings] = []
    heard_numbers: list[int] = []

    def settings_changed(self: JobManager, saved: Settings, version: int | None = None) -> None:
        heard.append(saved)
        heard_numbers.append(version or 0)

    monkeypatch.setattr(JobManager, "settings_changed", settings_changed)
    app = AppContext.from_app_folders()
    saved = app.settings.update({"concurrent_downloads": 3})
    assert heard == [saved]
    assert heard_numbers == [1]


def test_a_test_can_give_the_context_its_own_job_manager() -> None:
    made: list[AppContext] = []

    def make_jobs(context: AppContext) -> JobManager:
        made.append(context)
        return JobManager.for_app(context.settings, context.history, context.events)

    app = AppContext(
        settings=SettingsStore(Path("settings.json")),
        history=History(Path("history.db")),
        events=EventBus(),
        make_jobs=make_jobs,
    )
    assert made == [app]


def test_opening_the_stores_creates_the_history_database() -> None:
    app = AppContext.from_app_folders()
    assert not app.history.path.exists()
    app.open_stores()
    assert app.history.path.is_file()
    assert app.history.recent(1) == []


def test_a_history_that_cant_be_opened_only_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocked = tmp_path / "a file, not a folder"
    blocked.write_bytes(b"")
    app = AppContext(
        settings=SettingsStore(tmp_path / "settings.json"),
        history=History(blocked / "history.db"),
        events=EventBus(),
    )
    with caplog.at_level(logging.WARNING, logger="scmp3.app.context"):
        app.open_stores()
    assert "The history couldn't be opened" in caplog.text
