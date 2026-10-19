"""Tests for `render_tags`: the source's details, the comment setting and the number checks."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.core import pipeline
from scmp3.core.history import History
from scmp3.core.models import Settings
from scmp3.core.suggest import InvalidOverride


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
TYPICAL_URL = fakes.TYPICAL_URL
TYPICAL_ID = fakes.TYPICAL_ID
FakeSoundCloud = fakes.FakeSoundCloud
prepare = fakes.prepare
# pytest finds fixtures by the names they're bound to in the test file.
settings = fakes.settings
history = fakes.history
world = fakes.world


@pytest.mark.parametrize(
    ("mode", "comment"),
    [("source_url", TYPICAL_URL), ("description", "Liner notes"), ("none", None)],
)
def test_render_tags_adds_the_source_details_and_the_comment_setting_picks(
    mode: Any, comment: str | None, world: FakeSoundCloud, settings: Settings, history: History
) -> None:
    prepared = prepare(world, settings, history)
    source = replace(prepared.source, description="Liner notes", label="Lucky Label")
    tags = pipeline.render_tags(source, prepared.suggestion, replace(settings, comment_mode=mode))
    assert tags.comment == comment
    assert (tags.source_url, tags.source_id) == (TYPICAL_URL, TYPICAL_ID)
    assert (tags.isrc, tags.label) == ("QZH5E1912919", "Lucky Label")


@pytest.mark.parametrize(
    ("overrides", "bad_field"),
    [
        ({"year": "0"}, "year"),
        ({"year": "10000"}, "year"),
        ({"track_no": "1000"}, "track_no"),
        ({"track_no": "5/3"}, "track_total"),
        ({"year": "soon"}, "year"),
    ],
)
def test_render_tags_refuses_numbers_no_tag_can_hold(
    overrides: dict[str, str],
    bad_field: str,
    world: FakeSoundCloud,
    settings: Settings,
    history: History,
) -> None:
    prepared = prepare(world, settings, history)
    with pytest.raises(InvalidOverride) as raised:
        pipeline.render_tags(prepared.source, prepared.suggestion, settings, overrides=overrides)
    assert raised.value.field == bad_field
