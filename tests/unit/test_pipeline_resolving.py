"""Tests for `prepare`, the pipeline's first stage, with yt-dlp replaced by recorded fixtures."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.core import pipeline
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.inputs import classify
from scmp3.core.models import HistoryEntry, Settings
from scmp3.core.urls import UnsupportedInput


def load_fakes() -> ModuleType:
    """tests/fixtures/pipeline_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_pipeline_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "pipeline_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered first, since its dataclasses look their module up while it runs.
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


fakes = load_fakes()
record = fakes.record
TYPICAL_URL = fakes.TYPICAL_URL
TYPICAL_ID = fakes.TYPICAL_ID
SHORT_URL = fakes.SHORT_URL
FakeSoundCloud = fakes.FakeSoundCloud
BrokenHistory = fakes.BrokenHistory
recorded = fakes.recorded
link = fakes.link
prepare = fakes.prepare
staging_files = fakes.staging_files
# pytest finds fixtures by the names they're bound to in the test file.
settings = fakes.settings
history = fakes.history
world = fakes.world


def test_prepare_resolves_the_track_and_suggests_its_tags(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    prepared = prepare(world, settings, history)
    assert prepared.source.id == TYPICAL_ID
    assert prepared.suggestion.base_title == "The Following"
    assert prepared.suggestion.main_artists == ["80M"]
    assert prepared.duplicate is None
    assert prepared.info["id"] == "343609555"
    assert prepared.settings is settings
    assert len(prepared.job_id) == 32
    # Resolving makes no staging folder; only fetch does.
    assert staging_files() == []


def test_prepare_uses_the_job_id_it_is_given(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    assert prepare(world, settings, history, job_id="job-7").job_id == "job-7"


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("drm_only", ErrorKind.drm),
        ("preview_only", ErrorKind.preview_only),
        ("not_found", ErrorKind.unavailable),
    ],
)
def test_prepare_refuses_a_track_that_cant_be_saved_before_any_review(
    name: str, kind: ErrorKind, tiny_m4a: Path, settings: Settings, history: History
) -> None:
    # The preview fixture keeps its real lengths, 30 s of a 212 s track.
    world = FakeSoundCloud(record.load(name), {"*": tiny_m4a})
    with pytest.raises(ScmpError) as raised:
        prepare(world, settings, history)
    assert raised.value.kind == kind
    assert world.downloaded == []
    assert staging_files() == []


@pytest.mark.parametrize(
    "earlier",
    [
        HistoryEntry(
            TYPICAL_ID, None, Path("old.mp3"), "The Following", "80M", "The Following", "mp3"
        ),
        HistoryEntry(
            None, TYPICAL_URL, Path("old.mp3"), "The Following", "80M", "The Following", "mp3"
        ),
    ],
    ids=["by source id", "by link"],
)
def test_prepare_reports_an_earlier_save_as_a_duplicate(
    earlier: HistoryEntry, world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    history.add(earlier)
    duplicate = prepare(world, settings, history).duplicate
    assert duplicate is not None
    assert duplicate.saved_path == Path("old.mp3")


def test_a_broken_history_only_costs_the_duplicate_warning(
    world: FakeSoundCloud, settings: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    prepared = prepare(world, settings, BrokenHistory(tmp_path / "history.db"))
    assert prepared.duplicate is None
    assert "Couldn't look the track up in the history" in caplog.text


def test_prepare_follows_a_short_link_to_its_track(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    world.redirects[SHORT_URL] = f"{TYPICAL_URL}?si=abc&utm_source=clipboard"
    prepared = prepare(world, settings, history, parsed=link(SHORT_URL))
    assert prepared.source.url == TYPICAL_URL
    assert world.extracted == [TYPICAL_URL]


def test_a_short_link_to_a_set_is_refused_with_a_reason(
    world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    world.redirects[SHORT_URL] = "https://soundcloud.com/the80m/sets/singles"
    with pytest.raises(UnsupportedInput, match="sets and albums can't be saved yet"):
        prepare(world, settings, history, parsed=link(SHORT_URL))
    assert world.extracted == []


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("https://soundcloud.com/the80m/sets/singles", pipeline.SETS_NOT_SUPPORTED),
        ("https://example.com/song", "Not a supported link."),
    ],
)
def test_inputs_it_cant_save_are_refused_without_a_request(
    text: str, reason: str, world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    parsed = classify(text)
    assert reason in (pipeline.unsupported_reason(parsed) or "")
    with pytest.raises(UnsupportedInput):
        prepare(world, settings, history, parsed=parsed)
    assert world.tokens == []


@pytest.mark.parametrize(
    ("raised", "kind"),
    [(RuntimeError("a bug"), ErrorKind.internal), (PermissionError(13, "Denied"), ErrorKind.disk)],
)
def test_an_unexpected_error_still_comes_out_as_a_scmp_error(
    raised: Exception, kind: ErrorKind, settings: Settings, history: History
) -> None:
    def broken_make_ydl(*args: Any, **kwargs: Any) -> Any:
        raise raised

    tools = pipeline.Tools(make_ydl=broken_make_ydl, history=history)
    with pytest.raises(ScmpError) as error:
        pipeline.prepare(link(), settings=settings, tools=tools)
    assert error.value.kind == kind
    assert error.value.__cause__ is raised
