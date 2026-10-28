"""Static checks of the main window's UI: every error kind, cleanup and choice has its text.

The behavior itself is checked in a browser against the mock. These catch a kind or a choice
added in Python without its words or its banner in the UI.
"""

import re
from pathlib import Path
from typing import get_args

import pytest

from scmp3.app.main import WEB_DIR
from scmp3.core.errors import ErrorKind, ErrorPlace, ErrorReason
from scmp3.core.models import AudioFormat, ChangeKind, SourceKind, TitleStyle

# The colors T-020 added for banners, notes and dialogs.
T020_TOKENS = [
    "--warn-surface",
    "--warn-line",
    "--danger",
    "--danger-surface",
    "--danger-line",
    "--field-error-line",
    "--scrim",
    "--dialog-bg",
]

STRINGS = WEB_DIR / "strings" / "main.js"
# The job error texts, which main.js takes in.
ERROR_STRINGS = WEB_DIR / "strings" / "errors.js"
JOB_VIEW = WEB_DIR / "components" / "job-view.js"
BANNERS = WEB_DIR / "components" / "EditorBanners.js"
MOCK_FIELD_CHECKS = WEB_DIR / "mock" / "field-checks.js"
# The places a disk error can be about besides the library, each with its own words.
OTHER_PLACES = set(get_args(ErrorPlace)) - {"library"}

# Typographic apostrophes and single quotes. UI text uses the straight one.
LEFT_QUOTE, RIGHT_QUOTE = chr(0x2018), chr(0x2019)
CURLY_APOSTROPHES = re.compile(f"[{LEFT_QUOTE}{RIGHT_QUOTE}]")


def keys_in_block(text: str, name: str) -> set[str]:
    """The keys one level inside `name: {` or `const name = {`, on one line or indented below."""
    header = rf"^( *)(?:export )?(?:const )?{re.escape(name)}(?:: | = )\{{(.*)$"
    start = re.search(header, text, re.MULTILINE)
    assert start, f"no {name} block"
    indent, rest = start.groups()
    if "}" in rest:
        return set(re.findall(r"(\w+):", rest))
    end = text.index(f"\n{indent}}}", start.end())
    block = text[start.end() : end]
    return set(re.findall(rf"^{indent}  (\w+):", block, re.MULTILINE))


def keys_in_every_block(text: str, name: str) -> set[str]:
    """The keys of every `name: {` block in the text, put together."""
    keys: set[str] = set()
    for start in re.finditer(rf"^ *{re.escape(name)}: \{{", text, re.MULTILINE):
        keys |= keys_in_block(text[start.start() :], name)
    return keys


def strings_text() -> str:
    return STRINGS.read_text(encoding="utf-8")


def error_strings_text() -> str:
    return ERROR_STRINGS.read_text(encoding="utf-8")


def error_kinds() -> set[str]:
    return {kind.value for kind in ErrorKind}


def test_every_error_kind_has_its_status_and_headline() -> None:
    text = error_strings_text()
    assert keys_in_block(text, "errors") >= error_kinds()
    for kind in error_kinds():
        assert {"status", "headline"} <= keys_in_block(text, kind), kind


def test_every_error_reason_and_the_data_folder_have_their_own_words() -> None:
    # The variants under the error kinds, by the names job-view.js's errorVariant() gives: the
    # disk error's, the YouTube reasons a video is unavailable, and a re-tag's file reasons.
    variants = keys_in_every_block(error_strings_text(), "variants")
    assert variants == {*get_args(ErrorReason), *OTHER_PLACES}
    for variant in variants:
        assert "headline" in keys_in_block(error_strings_text(), variant), variant


def test_every_error_kind_has_a_banner_with_a_tone() -> None:
    table = JOB_VIEW.read_text(encoding="utf-8")
    assert keys_in_block(table, "ERROR_KINDS") >= error_kinds()
    for kind in error_kinds():
        entry = re.search(rf"^  {kind}: \{{[^}}]*\btone: \"(danger|warn|neutral)\"", table, re.M)
        assert entry, kind


def test_every_banner_action_has_a_label_and_a_handler() -> None:
    lists = re.findall(r"actions: \[([^\]]*)\]", JOB_VIEW.read_text(encoding="utf-8"))
    actions = set(re.findall(r'"(\w+)"', " ".join(lists)))
    assert actions
    assert actions <= keys_in_block(error_strings_text(), "errorActions")
    assert actions <= keys_in_block(BANNERS.read_text(encoding="utf-8"), "ACTIONS")


def test_every_error_offers_remove_from_the_queue_last() -> None:
    lists = re.findall(r"actions: \[([^\]]*)\]", JOB_VIEW.read_text(encoding="utf-8"))
    assert len(lists) > len(error_kinds())
    for actions in lists:
        assert re.findall(r'"(\w+)"', actions)[-1:] == ["remove"], actions


def test_every_field_refusal_the_mock_sends_has_text() -> None:
    reasons = set(re.findall(r'"([A-Z][A-Z_]+)"', MOCK_FIELD_CHECKS.read_text(encoding="utf-8")))
    assert reasons
    assert reasons <= keys_in_block(strings_text(), "fieldErrors")


@pytest.mark.parametrize(
    "strings_file", sorted((WEB_DIR / "strings").glob("*.js")), ids=lambda path: path.name
)
def test_ui_text_uses_straight_apostrophes(strings_file: Path) -> None:
    lines = strings_file.read_text(encoding="utf-8").splitlines()
    curly = [number for number, line in enumerate(lines, start=1) if CURLY_APOSTROPHES.search(line)]
    assert curly == [], f"curly apostrophes on lines {curly}"


def test_the_apostrophe_check_sees_both_curly_kinds() -> None:
    sample = f"it{RIGHT_QUOTE}s {LEFT_QUOTE}this{RIGHT_QUOTE} it's"
    assert CURLY_APOSTROPHES.findall(sample) == [RIGHT_QUOTE, LEFT_QUOTE, RIGHT_QUOTE]


def test_every_cleanup_kind_has_a_label_for_when_it_is_off() -> None:
    assert keys_in_block(strings_text(), "keptLabels") == set(get_args(ChangeKind))


@pytest.mark.parametrize(
    ("block", "choices"),
    [
        ("styles", get_args(TitleStyle)),
        ("audioTargets", get_args(AudioFormat)),
        ("sources", get_args(SourceKind)),
        ("cleanupsTitle", get_args(SourceKind)),
    ],
)
def test_every_choice_has_its_words(block: str, choices: tuple[str, ...]) -> None:
    assert keys_in_block(strings_text(), block) == set(choices)


def test_the_tokens_define_the_banner_and_dialog_colors() -> None:
    tokens = (WEB_DIR / "styles" / "tokens.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"(--[\w-]+)\s*:", tokens))
    assert set(T020_TOKENS) <= defined


def test_the_key_reader_finds_inline_and_nested_keys() -> None:
    text = "const x = {\n  outer: {\n    one: 1,\n    two: {\n      deep: 3,\n    },\n  },\n};\n"
    assert keys_in_block(text, "x") == {"outer"}
    assert keys_in_block(text, "outer") == {"one", "two"}
    assert keys_in_block(text, "two") == {"deep"}
    assert keys_in_block("  flat: { a: 1, b: 2 },\n", "flat") == {"a", "b"}
