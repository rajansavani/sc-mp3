"""Static checks of the Engine and updates section: every code Python sends has its words."""

import re
from typing import get_args

import pytest

from scmp3.app.components_view import EngineSource
from scmp3.app.main import WEB_DIR
from scmp3.core.errors import ErrorKind
from scmp3.core.models import EngineChannel, Settings

STRINGS = WEB_DIR / "strings" / "settings.js"
FIELDS = WEB_DIR / "components" / "settings" / "fields.js"

# The kinds an update check, an engine update or a YouTube support install can end with.
DOWNLOAD_ERRORS = {
    ErrorKind.offline,
    ErrorKind.rate_limited,
    ErrorKind.service_down,
    ErrorKind.unavailable,
    ErrorKind.bad_download,
    ErrorKind.disk,
}


def block(text: str, name: str) -> str:
    """The text of `name: {` up to its closing brace, found by its indentation."""
    start = re.search(rf"^( *){re.escape(name)}: \{{", text, re.MULTILINE)
    assert start, f"no {name} block"
    end = text.index(f"\n{start.group(1)}}}", start.end())
    return text[start.end() : end]


def keys(text: str, name: str) -> set[str]:
    """The keys of a block, whether it's on one line or one key per line."""
    found = block(text, name) if f"{name}: {{\n" in text else _inline(text, name)
    return set(re.findall(r"(\w+):", found))


def _inline(text: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}: \{{([^{{}}]*)\}}", text)
    assert match, f"no {name} block"
    return match.group(1)


def engine_strings() -> str:
    return block(STRINGS.read_text(encoding="utf-8"), "engine")


@pytest.mark.parametrize(
    ("name", "codes"),
    [("sources", get_args(EngineSource)), ("channels", get_args(EngineChannel))],
)
def test_every_engine_source_and_channel_has_its_words(name: str, codes: tuple[str, ...]) -> None:
    assert keys(engine_strings(), name) == set(codes)


def test_every_error_a_download_can_end_with_has_its_words() -> None:
    assert keys(engine_strings(), "downloadErrors") == {kind.value for kind in DOWNLOAD_ERRORS}


def test_the_channel_choice_offers_every_channel_with_a_label() -> None:
    strings = STRINGS.read_text(encoding="utf-8")
    assert keys(block(strings, "choices"), "engine_channel") == set(get_args(EngineChannel))
    fields = FIELDS.read_text(encoding="utf-8")
    choices = re.search(r"engine_channel: \[([^\]]*)\]", fields)
    assert choices, "fields.js has no engine_channel choices"
    assert re.findall(r'"(\w+)"', choices.group(1)) == list(get_args(EngineChannel))


@pytest.mark.parametrize("setting", ["check_app_updates", "check_engine_updates"])
def test_each_update_switch_has_a_label_and_a_hint(setting: str) -> None:
    switches = block(STRINGS.read_text(encoding="utf-8"), "switches")
    assert setting in Settings().to_dict()
    assert re.search(rf"{setting}: \{{\s*label: \"[^\"]+\",\s*hint: \"[^\"]+\"", switches)
