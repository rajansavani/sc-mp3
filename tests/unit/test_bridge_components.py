"""Tests for the YouTube support calls: the status in the snapshot, and the install's events."""

import hashlib
import io
import json
import logging
import threading
import zipfile
from collections.abc import Mapping, Sequence
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from scmp3 import __version__
from scmp3.app.bridge import Api
from scmp3.app.context import AppContext
from scmp3.app.events import Event, EventBus
from scmp3.core import paths
from scmp3.core.components import Components
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
