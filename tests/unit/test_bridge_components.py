"""Tests for the YouTube support calls: the status in the snapshot, and the install's events."""

import hashlib
import io
import json
import logging
import threading
import zipfile
from collections.abc import Callable, Mapping, Sequence
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from scmp3 import __version__
from scmp3.app.bridge import Api
from scmp3.app.components_view import components_view
from scmp3.app.context import AppContext
from scmp3.app.desktop import Desktop
from scmp3.app.events import Event, EventBus
from scmp3.app.update_checks import UpdateChecks
from scmp3.core import paths
from scmp3.core.components import Components
from scmp3.core.engine_start import EngineStart
from scmp3.core.engine_updates import EngineUpdates
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.settings import SettingsStore

ASSET = "deno-x86_64-pc-windows-msvc.zip"
API = "https://api.github.com/repos/denoland/deno/releases/latest"
DOWNLOADS = "https://github.com/denoland/deno/releases/download/v2.9.8"
# Long enough to come in several chunks, so the install reports progress more than once.
DENO_EXE = b"deno binary " * 100_000
FINISHED = ("installed", "failed")


def make_zip(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


DENO_ZIP = make_zip({"deno.exe": DENO_EXE})


class FakeResponse(io.BytesIO):
    """A response shaped like urllib's: a status, email-style headers and a readable body."""

    def __init__(self, body: bytes, headers: Mapping[str, str]) -> None:
        super().__init__(body)
        self.status = 200
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name] = value


class FakeDenoReleases:
    """GitHub's answers for a Deno 2.9.8 release, with the zip's bytes and checksum as given."""

    def __init__(self, zip_bytes: bytes = DENO_ZIP, published: bytes = DENO_ZIP) -> None:
        checksum = f"{hashlib.sha256(published).hexdigest()}  {ASSET}\n".encode()
        release = {
            "tag_name": "v2.9.8",
            "html_url": "https://github.com/denoland/deno/releases/tag/v2.9.8",
            "assets": [
                {"name": ASSET, "browser_download_url": f"{DOWNLOADS}/{ASSET}"},
                {
                    "name": f"{ASSET}.sha256sum",
                    "browser_download_url": f"{DOWNLOADS}/{ASSET}.sha256sum",
                },
            ],
        }
        self.files = {
            API: json.dumps(release).encode(),
            f"{DOWNLOADS}/{ASSET}": zip_bytes,
            f"{DOWNLOADS}/{ASSET}.sha256sum": checksum,
        }
        self.requests: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> FakeResponse:
        self.requests.append(url)
        if url not in self.files:
            raise ConnectionRefusedError(f"nothing serves {url}")
        body = self.files[url]
        return FakeResponse(body, {"Content-Length": str(len(body))})


class FakeRunner:
    """Stands in for runner.run: Deno's --version check passes, or raises what it's given."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def __call__(self, command: Sequence[str], *, timeout: float, error_kind: ErrorKind) -> None:
        if self.error is not None:
            raise self.error


class RecordingBus(EventBus):
    """An event bus that keeps every event instead of sending it to a window."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []
        self._changed = threading.Condition()

    def emit(self, event: Event) -> None:
        with self._changed:
            # Through JSON, as the window gets it.
            self.events.append(json.loads(json.dumps(event)))
            self._changed.notify_all()

    def youtube_states(self) -> list[dict[str, Any]]:
        return [event["components"]["youtube_support"] for event in self.events]

    def wait_until_finished(self) -> dict[str, Any]:
        """The last YouTube support status, once the install has finished one way or the other."""
        with self._changed:
            finished = self._changed.wait_for(
                lambda: bool(self.events) and self.youtube_states()[-1]["state"] in FINISHED,
                timeout=30,
            )
        assert finished, self.events
        return self.youtube_states()[-1]


def make_api(tmp_path: Path, web: FakeDenoReleases, run: FakeRunner | None = None) -> Api:
    components = Components(
        tmp_path / "components", opener=web, run=run or FakeRunner(), target="windows-x64"
    )
    context = AppContext(
        settings=SettingsStore(paths.config_dir() / "settings.json"),
        history=History(paths.data_dir() / "history.db"),
        events=RecordingBus(),
        engine_version=lambda: "2026.08.19",
        components=components,
    )
    return Api(context)


def bus(api: Api) -> RecordingBus:
    events = api._context.events
    assert isinstance(events, RecordingBus)
    return events


def youtube(state: str, **values: Any) -> dict[str, Any]:
    return {"state": state, "version": None, "pct": None, "error": None, **values}


def test_the_snapshot_says_youtube_support_is_missing_until_deno_is_installed(
    tmp_path: Path,
) -> None:
    api = make_api(tmp_path, FakeDenoReleases())
    assert api.snapshot()["components"]["youtube_support"] == youtube("missing")
    api._context.components.install_deno()
    expected = youtube("installed", version="2.9.8")
    assert api.snapshot()["components"]["youtube_support"] == expected


def test_installing_returns_at_once_then_reports_progress_and_the_version(
    tmp_path: Path,
) -> None:
    api = make_api(tmp_path, FakeDenoReleases())
    assert api.install_youtube_support() == {}
    assert bus(api).wait_until_finished() == youtube("installed", version="2.9.8")
    states = bus(api).youtube_states()
    assert states[0] == youtube("installing")
    progress = [state["pct"] for state in states if state["pct"] is not None]
    assert len(progress) > 1
    assert progress == sorted(progress)
    assert progress[-1] == 100.0
    assert api._context.components.active_path("deno") is not None


def test_every_event_carries_the_whole_components_object(tmp_path: Path) -> None:
    api = make_api(tmp_path, FakeDenoReleases())
    api.install_youtube_support()
    bus(api).wait_until_finished()
    for event in bus(api).events:
        assert event["type"] == "components"
        assert event["components"]["app_version"] == __version__
        assert event["components"]["engine_version"] == "2026.08.19"


class BlockingRunner(FakeRunner):
    """Holds Deno's check until the test lets it go, so the test can look mid-install."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.go_on = threading.Event()

    def __call__(self, command: Sequence[str], *, timeout: float, error_kind: ErrorKind) -> None:
        self.started.set()
        assert self.go_on.wait(30)


def test_the_snapshot_says_installing_while_an_install_runs(tmp_path: Path) -> None:
    run = BlockingRunner()
    api = make_api(tmp_path, FakeDenoReleases(), run)
    api.install_youtube_support()
    try:
        assert run.started.wait(30)
        assert api.snapshot()["components"]["youtube_support"] == youtube("installing")
    finally:
        run.go_on.set()
    bus(api).wait_until_finished()
    expected = youtube("installed", version="2.9.8")
    assert api.snapshot()["components"]["youtube_support"] == expected


def test_installing_when_installed_says_so_without_downloading(tmp_path: Path) -> None:
    web = FakeDenoReleases()
    api = make_api(tmp_path, web)
    api._context.components.install_deno()
    web.requests.clear()
    assert api.install_youtube_support() == {}
    # Answered before the call returns, with no thread and no request.
    assert bus(api).youtube_states() == [youtube("installed", version="2.9.8")]
    assert web.requests == []


def test_a_tampered_download_reports_a_bad_download(tmp_path: Path) -> None:
    api = make_api(tmp_path, FakeDenoReleases(zip_bytes=make_zip({"deno.exe": b"tampered"})))
    api.install_youtube_support()
    assert bus(api).wait_until_finished() == youtube("failed", error="bad_download")
    assert api.snapshot()["components"]["youtube_support"] == youtube("missing")


def test_a_deno_that_doesnt_run_reports_a_bad_download(tmp_path: Path) -> None:
    run = FakeRunner(ScmpError(ErrorKind.bad_download, "can't start deno"))
    api = make_api(tmp_path, FakeDenoReleases(), run)
    api.install_youtube_support()
    assert bus(api).wait_until_finished() == youtube("failed", error="bad_download")


def test_no_connection_reports_offline(tmp_path: Path) -> None:
    web = FakeDenoReleases()
    web.files.clear()
    api = make_api(tmp_path, web)
    api.install_youtube_support()
    assert bus(api).wait_until_finished() == youtube("failed", error="offline")


def test_an_unexpected_error_reports_internal_and_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    api = make_api(tmp_path, FakeDenoReleases(), FakeRunner(RuntimeError("a bug")))
    with caplog.at_level(logging.ERROR, logger="scmp3.app.bridge.components"):
        api.install_youtube_support()
        assert bus(api).wait_until_finished() == youtube("failed", error="internal")
    assert "Installing YouTube support failed unexpectedly" in caplog.text
    assert "a bug" in caplog.text


# Engine updates and the app's latest release

LATEST = "2026.10.10.233141"
NIGHTLY = "yt-dlp/yt-dlp-nightly-builds"
ZIPAPP = b"PK the nightly zipapp"
APP_API = "https://api.github.com/repos/rajansavani/sc-mp3/releases/latest"
APP_PAGE = "https://github.com/rajansavani/sc-mp3/releases/tag/v9.0.0"


class FakeGitHub:
    """GitHub with sc-mp3's latest release and the latest yt-dlp on both channels."""

    def __init__(self, engine_tag: str = LATEST) -> None:
        self.files: dict[str, bytes] = {}
        app = {"tag_name": "v9.0.0", "html_url": APP_PAGE, "assets": []}
        self.files[APP_API] = json.dumps(app).encode()
        for repo in ("yt-dlp/yt-dlp", NIGHTLY):
            self.engine(repo, engine_tag)
        self.requests: list[str] = []

    def engine(self, repo: str, tag: str) -> None:
        downloads = f"https://github.com/{repo}/releases/download/{tag}"
        assets = [
            {"name": "yt-dlp", "browser_download_url": f"{downloads}/yt-dlp"},
            {"name": "SHA2-256SUMS", "browser_download_url": f"{downloads}/SHA2-256SUMS"},
        ]
        page = f"https://github.com/{repo}/releases/tag/{tag}"
        release = {"tag_name": tag, "html_url": page, "assets": assets}
        api = f"https://api.github.com/repos/{repo}/releases/latest"
        self.files[api] = json.dumps(release).encode()
        self.files[f"{downloads}/yt-dlp"] = ZIPAPP
        sums = f"{hashlib.sha256(ZIPAPP).hexdigest()}  yt-dlp\n".encode()
        self.files[f"{downloads}/SHA2-256SUMS"] = sums

    def __call__(self, url: str, headers: Mapping[str, str]) -> FakeResponse:
        self.requests.append(url)
        if url not in self.files:
            raise ConnectionRefusedError(f"nothing serves {url}")
        body = self.files[url]
        return FakeResponse(body, {"Content-Length": str(len(body))})


class ShownFiles:
    """Stands in for starting the file manager, keeping each command it was given."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> None:
        self.commands.append(command)


def engine_api(
    tmp_path: Path,
    web: FakeGitHub,
    *,
    started: EngineStart | None = None,
    check: FakeRunner | None = None,
    shown: ShownFiles | None = None,
) -> Api:
    """An API whose update checks reach this fake GitHub, checking each update with `check`."""

    def make_updates(context: AppContext) -> UpdateChecks:
        engine = EngineUpdates(
            context.components,
            built_in="2026.8.19",
            in_use=context.engine_start.update,
            opener=web,
            run=check or FakeRunner(),
            command=lambda: ["sc-mp3.exe"],
        )

        def tell_the_ui() -> None:
            context.events.emit({"type": "components", "components": components_view(context)})

        return UpdateChecks(
            context.settings, engine, app_version="0.1.0", opener=web, on_change=tell_the_ui
        )

    context = AppContext(
        settings=SettingsStore(paths.config_dir() / "settings.json"),
        history=History(paths.data_dir() / "history.db"),
        events=RecordingBus(),
        engine_version=lambda: "2026.8.19",
        engine_start=started or EngineStart(),
        components=Components(tmp_path / "components", opener=web),
        desktop=Desktop(platform="win32", spawn=shown or ShownFiles()),
        make_updates=make_updates,
    )
    return Api(context)


def engine_part(api: Api) -> dict[str, Any]:
    return api.snapshot()["components"]["engine"]


def last_components(api: Api) -> dict[str, Any]:
    return bus(api).events[-1]["components"]


def outcome_of(api: Api, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Start Check now or Update now, which returns at once, and wait for its outcome's event."""
    events = bus(api)
    assert call() == {}

    def finished() -> bool:
        updates = [event["components"]["updates"] for event in events.events]
        return bool(updates) and updates[-1]["running"] is None and updates[-1]["last"] is not None

    with events._changed:
        assert events._changed.wait_for(finished, timeout=30), events.events
    return last_components(api)["updates"]["last"]


def update_now(api: Api) -> dict[str, Any]:
    return outcome_of(api, api.update_engine)["engine"]


def check_now(api: Api) -> dict[str, Any]:
    outcome = outcome_of(api, api.check_updates)
    return {"app": outcome["app"], "engine": outcome["engine"]}


def test_the_snapshot_says_the_built_in_engine_runs_and_a_restart_changes_nothing(
    tmp_path: Path,
) -> None:
    api = engine_api(tmp_path, FakeGitHub())
    components = api.snapshot()["components"]
    assert components["engine_version"] == "2026.8.19"
    assert components["engine"] == {
        "version": "2026.8.19",
        "source": "built_in",
        "channel": None,
        "built_in_version": "2026.8.19",
        "restart_to": None,
        "failed_version": None,
        "failed_now": None,
    }
    assert components["app_update"] == {"state": "unknown", "version": None, "url": None}
    assert components["updates"] == {"running": None, "pct": None, "last": None}


def test_update_now_returns_at_once_and_reports_progress_then_the_outcome(tmp_path: Path) -> None:
    api = engine_api(tmp_path, FakeGitHub())
    api.update_settings({"engine_channel": "nightly"})
    found = update_now(api)
    assert found == {"version": LATEST, "installed": True, "refused": None}
    updates = [event["components"]["updates"] for event in bus(api).events]
    assert updates[0]["running"] == "update"
    assert [state["pct"] for state in updates if state["pct"] is not None][-1] == 100.0
    expected = {"version": LATEST, "source": "updated"}
    assert last_components(api)["engine"]["restart_to"] == expected
    assert engine_part(api)["restart_to"] == expected
    # The running engine doesn't change until the restart.
    assert engine_part(api)["version"] == "2026.8.19"


def test_each_outcome_has_its_own_number(tmp_path: Path) -> None:
    api = engine_api(tmp_path, FakeGitHub())
    first = outcome_of(api, api.update_engine)
    second = outcome_of(api, api.check_updates)
    assert (first["id"], first["task"]) == (1, "update")
    assert (second["id"], second["task"]) == (2, "check")
    # The last outcome stays in the snapshot, so a later event can't hide it.
    assert api.snapshot()["components"]["updates"]["last"]["id"] == 2


def test_after_the_restart_the_update_runs_and_settings_shows_it(tmp_path: Path) -> None:
    api = engine_api(tmp_path, FakeGitHub())
    api.update_settings({"engine_channel": "nightly"})
    update_now(api)
    restarted = engine_api(tmp_path, FakeGitHub(), started=EngineStart(update=LATEST))
    components = restarted.snapshot()["components"]
    assert components["engine_version"] == LATEST
    assert components["engine"]["source"] == "updated"
    assert components["engine"]["channel"] == "nightly"
    assert components["engine"]["restart_to"] is None


def test_update_now_says_when_the_engine_is_up_to_date(tmp_path: Path) -> None:
    api = engine_api(tmp_path, FakeGitHub(engine_tag="2026.08.19"))
    assert update_now(api) == {"version": "2026.08.19", "installed": False, "refused": None}
    assert engine_part(api)["restart_to"] is None


def test_update_now_offline_gives_the_error_kind(tmp_path: Path) -> None:
    web = FakeGitHub()
    web.files.clear()
    assert update_now(engine_api(tmp_path, web)) == {"error": "offline"}


def test_an_update_that_fails_its_check_gives_a_bad_download(tmp_path: Path) -> None:
    check = FakeRunner(ScmpError(ErrorKind.bad_download, "RuntimeError: a damaged update"))
    api = engine_api(tmp_path, FakeGitHub(), check=check)
    assert update_now(api) == {"error": "bad_download"}
    assert engine_part(api)["restart_to"] is None


def test_an_unexpected_error_in_a_task_is_internal_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    api = engine_api(tmp_path, FakeGitHub())

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(api._context.updates, "check_now", broken)
    assert check_now(api) == {"app": {"error": "internal"}, "engine": {"error": "internal"}}
    assert "The check update task failed unexpectedly" in caplog.text


def test_a_second_task_while_one_runs_is_ignored(tmp_path: Path) -> None:
    check = BlockingRunner()
    api = engine_api(tmp_path, FakeGitHub(), check=check)
    assert api.update_engine() == {}
    try:
        assert check.started.wait(30)
        assert api.check_updates() == {}
        assert api.snapshot()["components"]["updates"]["running"] == "update"
    finally:
        check.go_on.set()
    outcome = outcome_of(api, lambda: {})
    assert outcome["task"] == "update"


def test_revert_while_an_update_runs_goes_back_to_built_in_after_a_restart(
    tmp_path: Path,
) -> None:
    update_now(engine_api(tmp_path, FakeGitHub()))
    api = engine_api(tmp_path, FakeGitHub(), started=EngineStart(update=LATEST))
    assert api.revert_engine() == {}
    expected = {"version": "2026.8.19", "source": "built_in"}
    assert last_components(api)["engine"]["restart_to"] == expected
    assert engine_part(api)["version"] == LATEST


def test_revert_before_the_restart_cancels_the_waiting_update(tmp_path: Path) -> None:
    api = engine_api(tmp_path, FakeGitHub())
    update_now(api)
    api.revert_engine()
    assert engine_part(api)["restart_to"] is None


def test_a_reverted_version_is_refused_as_reverted_by_check_now(tmp_path: Path) -> None:
    api = engine_api(tmp_path, FakeGitHub())
    update_now(api)
    api.revert_engine()
    assert check_now(api)["engine"] == {
        "version": LATEST,
        "installed": False,
        "refused": "reverted",
    }


def mark_latest_bad(api: Api) -> None:
    upcoming = api._context.updates.engine.next_update()
    assert upcoming is not None
    api._context.components.set_status(upcoming, "bad")


def test_an_update_that_failed_at_start_up_shows_with_its_version(tmp_path: Path) -> None:
    api = engine_api(tmp_path, FakeGitHub())
    update_now(api)
    mark_latest_bad(api)
    failed = engine_api(tmp_path, FakeGitHub(), started=EngineStart(failed=LATEST))
    part = engine_part(failed)
    assert (part["failed_version"], part["failed_now"]) == (LATEST, LATEST)
    assert part["restart_to"] is None
    # At the next start it's still the newest update, but it's no longer news.
    later = engine_part(engine_api(tmp_path, FakeGitHub()))
    assert (later["failed_version"], later["failed_now"]) == (LATEST, None)


def test_revert_clears_the_notice_of_an_update_that_failed_at_start_up(tmp_path: Path) -> None:
    api = engine_api(tmp_path, FakeGitHub())
    update_now(api)
    mark_latest_bad(api)
    failed = engine_api(tmp_path, FakeGitHub(), started=EngineStart(failed=LATEST))
    assert failed.revert_engine() == {}
    assert engine_part(failed)["failed_version"] is None
    assert last_components(failed)["engine"]["failed_version"] is None


def test_check_now_reports_both_checks(tmp_path: Path) -> None:
    api = engine_api(tmp_path, FakeGitHub(engine_tag="2026.08.19"))
    assert check_now(api) == {
        "app": {"state": "available", "version": "9.0.0", "url": APP_PAGE},
        "engine": {"version": "2026.08.19", "installed": False, "refused": None},
    }
    assert api.snapshot()["components"]["app_update"]["url"] == APP_PAGE


def test_check_now_installs_a_newer_engine_on_the_channel_in_settings(tmp_path: Path) -> None:
    web = FakeGitHub(engine_tag="2026.08.19")
    web.engine(NIGHTLY, LATEST)
    api = engine_api(tmp_path, web)
    api.update_settings({"engine_channel": "nightly"})
    assert check_now(api)["engine"]["installed"] is True


def test_check_now_with_engine_checks_off_only_asks_for_the_app(tmp_path: Path) -> None:
    web = FakeGitHub()
    api = engine_api(tmp_path, web)
    api.update_settings({"check_engine_updates": False})
    assert check_now(api)["engine"] is None
    assert web.requests == [APP_API]


def test_check_now_offline_gives_each_error(tmp_path: Path) -> None:
    web = FakeGitHub()
    web.files.clear()
    assert check_now(engine_api(tmp_path, web)) == {
        "app": {"error": "offline"},
        "engine": {"error": "offline"},
    }


def test_open_logs_shows_the_log_file(tmp_path: Path, scmp3_home: Path) -> None:
    log_file = scmp3_home / "logs" / "sc-mp3.log"
    log_file.parent.mkdir()
    log_file.write_text("")
    shown = ShownFiles()
    assert engine_api(tmp_path, FakeGitHub(), shown=shown).open_logs() == {}
    assert shown.commands == [["explorer", "/select,", str(log_file)]]


def test_open_logs_without_a_logs_folder_says_its_missing(tmp_path: Path) -> None:
    assert engine_api(tmp_path, FakeGitHub()).open_logs() == {"error": "file_missing"}
