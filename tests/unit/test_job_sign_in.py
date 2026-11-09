"""Tests for the sign-in jobs use: the current token for each step, and one renewal after a 401."""

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from yt_dlp.extractor.soundcloud import SoundcloudBaseIE

from scmp3.app.job_sign_in import JobSignIn
from scmp3.app.job_steps import PipelineSteps
from scmp3.core import pipeline
from scmp3.core.engine import YTDLP_LOGGER
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.inputs import ParsedInput
from scmp3.core.models import Settings, SourceInfo, TagSuggestion
from scmp3.core.token_store import TokenStore


def load_signin_fakes() -> ModuleType:
    """tests/fixtures/signin_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_signin_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "signin_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


fakes = load_signin_fakes()
NOW = fakes.NOW
HOUR = fakes.HOUR
GUEST_WARNING = "[soundcloud] Provided authorization token is invalid. Continuing as guest"


@pytest.fixture(autouse=True)
def ytdlp_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A fresh dict for the headers yt-dlp's SoundCloud extractors share, put back afterwards."""
    headers: dict[str, str] = {}
    monkeypatch.setattr(SoundcloudBaseIE, "_HEADERS", headers)
    return headers


@pytest.fixture
def store() -> TokenStore:
    # The default keyring, which conftest swaps for one in memory.
    return TokenStore(clock=fakes.clock)


class Ended:
    """Counts the times JobSignIn says the sign-in ended."""

    def __init__(self) -> None:
        self.times = 0

    def __call__(self) -> None:
        self.times += 1


@pytest.fixture
def ended() -> Ended:
    return Ended()


def sign_in_with(ydl: Any, store: TokenStore, ended: Ended) -> JobSignIn:
    return JobSignIn(make_ydl=ydl, make_store=lambda: store, on_ended=ended, clock=fakes.clock)


def refused() -> ScmpError:
    """A step's error for an api-v2 401, which yt-dlp's errors read as an outdated engine."""
    error = ScmpError(ErrorKind.engine_outdated, "HTTP Error 401: Unauthorized")
    error.__cause__ = fakes.status_error(401, "https://api-v2.soundcloud.com/resolve")
    return error


class Step:
    """A step that records the tokens it ran with, and raises or warns as told, in turn."""

    def __init__(self, *outcomes: Exception | str | None) -> None:
        self.tokens: list[str | None] = []
        self._outcomes = list(outcomes)

    def __call__(self, token: str | None) -> str:
        self.tokens.append(token)
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "guest":
            logging.getLogger(YTDLP_LOGGER).warning(GUEST_WARNING)
        return "done"


def test_a_step_runs_with_no_token_when_nobody_is_signed_in(
    store: TokenStore, ended: Ended, ytdlp_headers: dict[str, str]
) -> None:
    ytdlp_headers["Authorization"] = "OAuth left over"
    step = Step()
    assert sign_in_with(fakes.FakeYdl(), store, ended).run(step) == "done"
    assert step.tokens == [None]
    # A sign-out in another process reaches this one through the header.
    assert "Authorization" not in ytdlp_headers


def test_a_step_runs_with_the_stored_token_and_sets_yt_dlps_header(
    store: TokenStore, ended: Ended, ytdlp_headers: dict[str, str]
) -> None:
    tokens = fakes.signed_in(store)
    step = Step()
    sign_in_with(fakes.FakeYdl(), store, ended).run(step)
    assert step.tokens == [tokens.access]
    assert ytdlp_headers["Authorization"] == f"OAuth {tokens.access}"


def test_a_token_about_to_expire_is_renewed_before_the_step(
    store: TokenStore, ended: Ended
) -> None:
    fakes.signed_in(store, exp=NOW + 60)
    new_access = fakes.make_access()
    ydl = fakes.FakeYdl(fakes.token_answer(new_access, fakes.make_refresh()))
    step = Step()
    sign_in_with(ydl, store, ended).run(step)
    assert step.tokens == [new_access]


def test_a_refused_token_is_renewed_and_the_step_runs_again(
    store: TokenStore, ended: Ended, ytdlp_headers: dict[str, str]
) -> None:
    tokens = fakes.signed_in(store)
    new_access = fakes.make_access()
    ydl = fakes.FakeYdl(fakes.token_answer(new_access, fakes.make_refresh()))
    step = Step(refused())
    assert sign_in_with(ydl, store, ended).run(step) == "done"
    assert step.tokens == [tokens.access, new_access]
    assert [request.url for request in ydl.requests] == [fakes.TOKEN_URL]
    assert ytdlp_headers["Authorization"] == f"OAuth {new_access}"
    assert ended.times == 0


def test_a_step_refused_again_after_renewing_fails_as_an_ended_sign_in(
    store: TokenStore, ended: Ended
) -> None:
    fakes.signed_in(store)
    ydl = fakes.FakeYdl(fakes.token_answer(fakes.make_access(), fakes.make_refresh()))
    with pytest.raises(ScmpError) as error_info:
        sign_in_with(ydl, store, ended).run(Step(refused(), refused()))
    # Not engine_outdated, since the engine had no part in it.
    assert error_info.value.kind is ErrorKind.auth_expired
    assert ended.times == 1


def test_a_renewal_refused_with_invalid_grant_ends_the_sign_in(
    store: TokenStore, ended: Ended
) -> None:
    fakes.signed_in(store)
    ydl = fakes.FakeYdl(fakes.oauth_error("invalid_grant"))
    with pytest.raises(ScmpError) as error_info:
        sign_in_with(ydl, store, ended).run(Step(refused()))
    assert error_info.value.kind is ErrorKind.auth_expired
    assert store.load() is None
    assert ended.times == 1


def test_an_ended_sign_in_found_before_the_step_says_so_too(
    store: TokenStore, ended: Ended
) -> None:
    fakes.signed_in(store, exp=NOW - HOUR)
    step = Step()
    with pytest.raises(ScmpError) as error_info:
        sign_in_with(fakes.FakeYdl(fakes.oauth_error("invalid_grant")), store, ended).run(step)
    assert error_info.value.kind is ErrorKind.auth_expired
    assert step.tokens == []
    assert ended.times == 1


def test_a_failure_that_isnt_a_401_is_left_alone(store: TokenStore, ended: Ended) -> None:
    fakes.signed_in(store)
    ydl = fakes.FakeYdl()
    step = Step(ScmpError(ErrorKind.engine_outdated, "yt-dlp has no SoundCloud client_id"))
    with pytest.raises(ScmpError) as error_info:
        sign_in_with(ydl, store, ended).run(step)
    assert error_info.value.kind is ErrorKind.engine_outdated
    assert len(step.tokens) == 1
    assert ydl.requests == []


def test_a_renewal_that_cant_reach_soundcloud_fails_the_step_as_offline(
    store: TokenStore, ended: Ended
) -> None:
    from yt_dlp.networking.exceptions import TransportError

    fakes.signed_in(store)
    ydl = fakes.FakeYdl(TransportError(msg="timed out"))
    with pytest.raises(ScmpError) as error_info:
        sign_in_with(ydl, store, ended).run(Step(refused()))
    # offline retries by itself, and the sign-in is kept for it.
    assert error_info.value.kind is ErrorKind.offline
    assert store.load() is not None
    assert ended.times == 0


def test_a_guest_warning_renews_and_runs_the_step_again(store: TokenStore, ended: Ended) -> None:
    tokens = fakes.signed_in(store)
    new_access = fakes.make_access()
    ydl = fakes.FakeYdl(fakes.token_answer(new_access, fakes.make_refresh()))
    step = Step("guest")
    sign_in_with(ydl, store, ended).run(step)
    assert step.tokens == [tokens.access, new_access]


def test_a_second_guest_warning_fails_as_an_ended_sign_in(store: TokenStore, ended: Ended) -> None:
    fakes.signed_in(store)
    ydl = fakes.FakeYdl(fakes.token_answer(fakes.make_access(), fakes.make_refresh()))
    with pytest.raises(ScmpError) as error_info:
        sign_in_with(ydl, store, ended).run(Step("guest", "guest"))
    assert error_info.value.kind is ErrorKind.auth_expired
    assert ended.times == 1


def test_a_token_another_job_renewed_meanwhile_is_used_without_renewing_again(
    store: TokenStore, ended: Ended
) -> None:
    tokens = fakes.signed_in(store)
    newer = fakes.make_tokens()

    def renewed_elsewhere(token: str | None) -> str:
        if token == tokens.access:
            store.save(newer)
            raise refused()
        return "done"

    ydl = fakes.FakeYdl()
    assert sign_in_with(ydl, store, ended).run(renewed_elsewhere) == "done"
    assert ydl.requests == []


def test_a_sign_out_while_the_step_ran_runs_it_again_as_a_guest(
    store: TokenStore, ended: Ended
) -> None:
    fakes.signed_in(store)

    def signed_out_meanwhile(token: str | None) -> str:
        if token is not None:
            store.delete()
            raise refused()
        return "done as a guest"

    ydl = fakes.FakeYdl()
    assert sign_in_with(ydl, store, ended).run(signed_out_meanwhile) == "done as a guest"
    assert ended.times == 0


def test_the_stored_token_is_read_without_renewing(store: TokenStore, ended: Ended) -> None:
    tokens = fakes.signed_in(store, exp=NOW - HOUR)
    ydl = fakes.FakeYdl()
    assert sign_in_with(ydl, store, ended).stored_token() == tokens.access
    assert ydl.requests == []


# The pipeline steps


class RecordingPipeline:
    """Stands in for core.pipeline's prepare, prepare_set and fetch, and records their tokens."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tokens: list[str | None] = []
        monkeypatch.setattr(pipeline, "prepare", self.prepare)
        monkeypatch.setattr(pipeline, "fetch", self.fetch)
        monkeypatch.setattr(pipeline, "prepare_set", self.prepare)

    def prepare(self, parsed: ParsedInput, *, token: str | None, **options: Any) -> str:
        self.tokens.append(token)
        return "prepared"

    def fetch(self, prepared: pipeline.Prepared, **options: Any) -> str:
        self.tokens.append(prepared.token)
        return "staged"


def source(kind: str) -> SourceInfo:
    link = "https://example.com/1"
    return SourceInfo(kind=kind, id=f"{kind}:1", url=link, title="Track", uploader="Kaia")  # type: ignore[arg-type]


def steps_with(store: TokenStore, ended: Ended, ydl: Any) -> PipelineSteps:
    tools = pipeline.Tools(make_ydl=ydl)
    return PipelineSteps(tools, sign_in_with(ydl, store, ended))


def test_resolving_a_soundcloud_link_passes_the_current_token(
    store: TokenStore, ended: Ended, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = fakes.signed_in(store)
    recorded = RecordingPipeline(monkeypatch)
    parsed = ParsedInput(raw="x", kind="soundcloud_track", url="https://soundcloud.com/a/b")
    steps_with(store, ended, fakes.FakeYdl()).prepare(parsed, settings=Settings(), job_id="j")
    assert recorded.tokens == [tokens.access]


def test_listing_a_set_passes_the_current_token(
    store: TokenStore, ended: Ended, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = fakes.signed_in(store)
    recorded = RecordingPipeline(monkeypatch)
    parsed = ParsedInput(raw="x", kind="soundcloud_set", url="https://soundcloud.com/a/sets/b")
    steps_with(store, ended, fakes.FakeYdl()).prepare_set(parsed, settings=Settings())
    assert recorded.tokens == [tokens.access]


def test_resolving_a_youtube_link_never_reads_the_sign_in(
    ended: Ended, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = RecordingPipeline(monkeypatch)
    parsed = ParsedInput(raw="x", kind="youtube_video", url="https://www.youtube.com/watch?v=1")

    def no_store() -> TokenStore:
        raise AssertionError("a YouTube link read the sign-in")

    steps = PipelineSteps(
        pipeline.Tools(make_ydl=fakes.FakeYdl()), JobSignIn(make_store=no_store, on_ended=ended)
    )
    steps.prepare(parsed, settings=Settings(), job_id="j")
    assert recorded.tokens == [None]


def test_downloading_uses_a_fresh_token_and_keeps_it_on_the_job(
    store: TokenStore, ended: Ended, monkeypatch: pytest.MonkeyPatch
) -> None:
    fakes.signed_in(store, exp=NOW + 60)
    new_access = fakes.make_access()
    ydl = fakes.FakeYdl(fakes.token_answer(new_access, fakes.make_refresh()))
    recorded = RecordingPipeline(monkeypatch)
    prepared = pipeline.Prepared(
        job_id="j",
        source=source("soundcloud"),
        suggestion=TagSuggestion(base_title="Track", main_artists=[]),
        duplicate=None,
        settings=Settings(),
        info={},
        token="an old token",
    )
    steps = steps_with(store, ended, ydl)
    steps.fetch(prepared, on_progress=lambda *args: None, cancel=None)  # type: ignore[arg-type]
    assert recorded.tokens == [new_access]
    assert prepared.token == new_access


def test_a_restored_soundcloud_job_gets_the_stored_token(store: TokenStore, ended: Ended) -> None:
    tokens = fakes.signed_in(store)
    steps = steps_with(store, ended, fakes.FakeYdl())
    suggestion = TagSuggestion(base_title="Track", main_artists=[])
    restored = steps.restore("j", source("soundcloud"), suggestion, None, settings=Settings())
    assert restored.token == tokens.access
    youtube = steps.restore("k", source("youtube"), suggestion, None, settings=Settings())
    assert youtube.token is None
