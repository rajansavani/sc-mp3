"""Tests for the update checks: when they run, what they remember, and the app's latest release."""

import hashlib
import io
import json
import threading
from collections.abc import Mapping, Sequence
from email.message import Message
from pathlib import Path

import pytest

from scmp3.app.update_checks import DAY, RETRY_AFTER, AppUpdate, UpdateChecks
from scmp3.core.components import Components
from scmp3.core.engine_updates import EngineUpdate, EngineUpdates
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.settings import SettingsStore

NOW = 1_800_000_000.0
APP_API = "https://api.github.com/repos/rajansavani/sc-mp3/releases/latest"
APP_PAGE = "https://github.com/rajansavani/sc-mp3/releases/tag/v0.2.0"
ENGINE_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
NIGHTLY_API = "https://api.github.com/repos/yt-dlp/yt-dlp-nightly-builds/releases/latest"
ZIPAPP = b"PK zipapp"


class FakeResponse(io.BytesIO):
    """A response shaped like urllib's: a status, email-style headers and a readable body."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        super().__init__(body)
        self.status = status
        self.headers = Message()


def release(repo: str, tag: str, *, engine: bool = False) -> dict[str, object]:
    downloads = f"https://github.com/{repo}/releases/download/{tag}"
    assets = []
    if engine:
        assets = [
            {"name": "yt-dlp", "browser_download_url": f"{downloads}/yt-dlp"},
            {"name": "SHA2-256SUMS", "browser_download_url": f"{downloads}/SHA2-256SUMS"},
        ]
    page = f"https://github.com/{repo}/releases/tag/{tag}"
    return {"tag_name": tag, "html_url": page, "assets": assets}


class FakeGitHub:
    """sc-mp3's and yt-dlp's latest releases, changeable by the test, with every URL asked for."""

    def __init__(self) -> None:
        self.answers: dict[str, tuple[int, bytes]] = {}
        self.requests: list[str] = []
        self.app_release("v0.0.1")
        self.engine_release("yt-dlp/yt-dlp", ENGINE_API, "2026.08.19")
        self.engine_release("yt-dlp/yt-dlp-nightly-builds", NIGHTLY_API, "2026.08.19")

    def app_release(self, tag: str | None) -> None:
        if tag is None:
            self.answers[APP_API] = (404, b'{"message": "Not Found"}')
            return
        self.answers[APP_API] = (200, json.dumps(release("rajansavani/sc-mp3", tag)).encode())

    def engine_release(self, repo: str, api: str, tag: str) -> None:
        downloads = f"https://github.com/{repo}/releases/download/{tag}"
        self.answers[api] = (200, json.dumps(release(repo, tag, engine=True)).encode())
        self.answers[f"{downloads}/yt-dlp"] = (200, ZIPAPP)
        sums = f"{hashlib.sha256(ZIPAPP).hexdigest()}  yt-dlp\n".encode()
        self.answers[f"{downloads}/SHA2-256SUMS"] = (200, sums)

    def __call__(self, url: str, headers: Mapping[str, str]) -> FakeResponse:
        self.requests.append(url)
        if url not in self.answers:
            raise ConnectionRefusedError(f"nothing serves {url}")
        status, body = self.answers[url]
        return FakeResponse(body, status)

    def api_requests(self) -> list[str]:
        return [url for url in self.requests if url.startswith("https://api.github.com")]


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class PassingCheck:
    """Stands in for the child process that checks an engine update."""

    def __call__(self, command: Sequence[str], *, timeout: float, error_kind: ErrorKind) -> None:
        pass


@pytest.fixture
def web() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


def make_checks(
    tmp_path: Path,
    web: FakeGitHub,
    clock: Clock,
    store: SettingsStore,
    on_change: list[str] | None = None,
) -> UpdateChecks:
    engine = EngineUpdates(
        Components(tmp_path / "components", opener=web, clock=clock),
        built_in="2026.8.19",
        opener=web,
        clock=clock,
        run=PassingCheck(),
        command=lambda: ["sc-mp3.exe"],
    )
    changes = on_change if on_change is not None else []
    return UpdateChecks(
        store,
        engine,
        app_version="0.1.0",
        opener=web,
        clock=clock,
        state_path=tmp_path / "update-checks.json",
        on_change=lambda: changes.append("changed"),
        first_delay=0,
    )


@pytest.fixture
def checks(tmp_path: Path, web: FakeGitHub, clock: Clock, store: SettingsStore) -> UpdateChecks:
    return make_checks(tmp_path, web, clock, store)


# The daily checks


def test_the_first_daily_run_checks_both_and_waits_a_day(
    checks: UpdateChecks, web: FakeGitHub
) -> None:
    assert checks.run_due() == DAY
    assert web.api_requests() == [APP_API, ENGINE_API]


def test_nothing_is_asked_again_within_a_day(
    checks: UpdateChecks, web: FakeGitHub, clock: Clock
) -> None:
    checks.run_due()
    web.requests.clear()
    clock.now = NOW + DAY - 600
    assert checks.run_due() == pytest.approx(600)
    assert web.requests == []
    clock.now = NOW + DAY
    checks.run_due()
    assert web.api_requests() == [APP_API, ENGINE_API]


def test_the_time_of_each_check_survives_a_restart(
    tmp_path: Path, web: FakeGitHub, clock: Clock, store: SettingsStore
) -> None:
    make_checks(tmp_path, web, clock, store).run_due()
    web.requests.clear()
    make_checks(tmp_path, web, clock, store).run_due()
    assert web.requests == []


def test_a_clock_turned_back_makes_the_checks_due(
    checks: UpdateChecks, web: FakeGitHub, clock: Clock
) -> None:
    checks.run_due()
    web.requests.clear()
    clock.now = NOW - 3600
    checks.run_due()
    assert web.api_requests() == [APP_API, ENGINE_API]


@pytest.mark.parametrize(
    ("turned_off", "asked"),
    [("check_app_updates", [ENGINE_API]), ("check_engine_updates", [APP_API])],
)
def test_each_daily_check_can_be_turned_off(
    checks: UpdateChecks, web: FakeGitHub, store: SettingsStore, turned_off: str, asked: list[str]
) -> None:
    store.update({turned_off: False})
    checks.run_due()
    assert web.api_requests() == asked


def test_with_both_checks_off_nothing_is_asked(
    checks: UpdateChecks, web: FakeGitHub, store: SettingsStore
) -> None:
    store.update({"check_app_updates": False, "check_engine_updates": False})
    assert checks.run_due() == DAY
    assert web.requests == []


def test_a_check_that_didnt_finish_is_tried_again_in_an_hour(
    checks: UpdateChecks, web: FakeGitHub, clock: Clock
) -> None:
    web.answers.clear()
    assert checks.run_due() == RETRY_AFTER
    web.requests.clear()
    clock.now = NOW + 60
    checks.run_due()
    # Nothing finished, so both are still due.
    assert web.api_requests() == [APP_API, ENGINE_API]


def test_an_unexpected_error_in_a_daily_check_is_logged_and_tried_later(
    checks: UpdateChecks, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: object, **kwargs: object) -> EngineUpdate:
        raise RuntimeError("a bug")

    monkeypatch.setattr(checks.engine, "update", broken)
    assert checks.run_due() == RETRY_AFTER
    assert "The daily engine update check failed unexpectedly" in caplog.text


def test_the_daily_engine_check_installs_on_the_channel_in_settings(
    checks: UpdateChecks, web: FakeGitHub, store: SettingsStore
) -> None:
    store.update({"engine_channel": "nightly"})
    web.engine_release("yt-dlp/yt-dlp-nightly-builds", NIGHTLY_API, "2026.10.10.233141")
    checks.run_due()
    upcoming = checks.engine.next_update()
    assert upcoming is not None
    assert upcoming.version == "2026.10.10.233141"


def test_the_daily_checks_run_on_their_own_thread_until_stopped(
    tmp_path: Path, web: FakeGitHub, clock: Clock, store: SettingsStore
) -> None:
    changes: list[str] = []
    checks = make_checks(tmp_path, web, clock, store, changes)
    finished = threading.Event()
    original = checks.run_due

    def run_due() -> float:
        wait = original()
        finished.set()
        return wait

    checks.run_due = run_due  # type: ignore[method-assign]
    checks.start()
    try:
        assert finished.wait(30)
    finally:
        checks.stop()
    assert changes == ["changed", "changed"]


# Check now


def test_check_now_asks_for_both_and_says_what_it_found(
    checks: UpdateChecks, web: FakeGitHub
) -> None:
    web.app_release("v0.2.0")
    results = checks.check_now()
    assert results.app == AppUpdate("available", "0.2.0", APP_PAGE)
    assert results.engine == EngineUpdate("2026.08.19", installed=False)


def test_check_now_asks_for_the_app_even_with_its_daily_check_off(
    checks: UpdateChecks, web: FakeGitHub, store: SettingsStore
) -> None:
    store.update({"check_app_updates": False})
    checks.check_now()
    assert APP_API in web.requests


def test_check_now_leaves_the_engine_alone_with_engine_checks_off(
    checks: UpdateChecks, web: FakeGitHub, store: SettingsStore
) -> None:
    store.update({"check_engine_updates": False})
    assert checks.check_now().engine is None
    assert ENGINE_API not in web.requests


def test_check_now_gives_each_checks_error(checks: UpdateChecks, web: FakeGitHub) -> None:
    web.answers.clear()
    results = checks.check_now()
    assert isinstance(results.app, ScmpError)
    assert results.app.kind is ErrorKind.offline
    assert isinstance(results.engine, ScmpError)
    assert results.engine.kind is ErrorKind.offline


def test_every_check_tells_the_ui(
    tmp_path: Path, web: FakeGitHub, clock: Clock, store: SettingsStore
) -> None:
    changes: list[str] = []
    checks = make_checks(tmp_path, web, clock, store, changes)
    checks.check_app()
    checks.check_engine()
    checks.revert_engine()
    assert changes == ["changed"] * 3


def test_a_failed_engine_check_tells_the_ui_too(
    tmp_path: Path, web: FakeGitHub, clock: Clock, store: SettingsStore
) -> None:
    changes: list[str] = []
    checks = make_checks(tmp_path, web, clock, store, changes)
    web.answers.clear()
    with pytest.raises(ScmpError):
        checks.check_engine()
    assert changes == ["changed"]


# The app's latest release


def test_before_any_check_the_app_update_is_unknown(checks: UpdateChecks) -> None:
    assert checks.app_update() == AppUpdate("unknown")


def test_a_newer_release_is_available_with_its_page(checks: UpdateChecks, web: FakeGitHub) -> None:
    web.app_release("v0.2.0")
    checks.check_app()
    assert checks.app_update() == AppUpdate("available", "0.2.0", APP_PAGE)


@pytest.mark.parametrize("tag", ["v0.1.0", "v0.0.9"])
def test_a_release_that_isnt_newer_means_this_is_current(
    checks: UpdateChecks, web: FakeGitHub, tag: str
) -> None:
    web.app_release(tag)
    assert checks.check_app() == AppUpdate("current", tag.removeprefix("v"))


def test_a_repo_with_no_release_yet_counts_as_current_once_checked(
    checks: UpdateChecks, web: FakeGitHub
) -> None:
    web.app_release(None)
    assert checks.check_app() == AppUpdate("current")


def test_the_app_update_is_known_after_a_restart_without_asking(
    tmp_path: Path, web: FakeGitHub, clock: Clock, store: SettingsStore
) -> None:
    web.app_release("v0.2.0")
    make_checks(tmp_path, web, clock, store).check_app()
    web.requests.clear()
    later = make_checks(tmp_path, web, clock, store)
    assert later.app_update().state == "available"
    assert web.requests == []


def test_rate_limited_with_nothing_cached_is_an_error(
    checks: UpdateChecks, web: FakeGitHub
) -> None:
    web.answers[APP_API] = (429, b"{}")
    with pytest.raises(ScmpError) as raised:
        checks.check_app()
    assert raised.value.kind is ErrorKind.rate_limited


# What the checks remember


@pytest.mark.parametrize("text", ["not json", "[]", '{"app": "yesterday"}'])
def test_damaged_check_times_count_as_never_checked(
    tmp_path: Path, checks: UpdateChecks, web: FakeGitHub, text: str
) -> None:
    (tmp_path / "update-checks.json").write_text(text)
    checks.run_due()
    assert web.api_requests() == [APP_API, ENGINE_API]


def test_check_times_that_cant_be_saved_only_warn(
    tmp_path: Path,
    web: FakeGitHub,
    clock: Clock,
    store: SettingsStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    checks = make_checks(tmp_path, web, clock, store)
    # A folder where the file should be.
    (tmp_path / "update-checks.json").mkdir()
    checks.check_app()
    assert "Couldn't remember when the app update check ran" in caplog.text
