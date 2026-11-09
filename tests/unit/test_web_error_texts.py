"""Static checks that every error Python can send the UI has its words and its buttons there.

A missing entry would show the "no message for" fallback instead of PLAN §4.11's next step.
"""

import re
from typing import get_args

import pytest

from scmp3.app.account_problems import PROFILE_LEFT
from scmp3.app.main import WEB_DIR
from scmp3.app.signin import WindowProblem
from scmp3.core.errors import ErrorKind, ErrorPlace, ErrorReason
from scmp3.core.tokens import TokenProblem

ERRORS_JS = (WEB_DIR / "strings" / "errors.js").read_text(encoding="utf-8")
ACCOUNT_JS = (WEB_DIR / "strings" / "account.js").read_text(encoding="utf-8")
JOB_VIEW_JS = (WEB_DIR / "components" / "job-view.js").read_text(encoding="utf-8")


def block(source: str, opening: str) -> str:
    """The object literal that starts at `opening`, such as "errors: {", up to its closing brace."""
    start = source.index(opening) + len(opening)
    depth = 1
    for index in range(start, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[start:index]
    raise ValueError(f"{opening!r} isn't closed")


def has_key(text: str, key: str) -> bool:
    """Whether a line in `text` starts the entry `key`, with its value on that line or the next."""
    return re.search(rf"^\s+{re.escape(key)}:\s", text, re.MULTILINE) is not None


def test_the_block_reader_stops_at_the_matching_brace() -> None:
    assert block("a: { b: { c: 1 }, d: `${x}` } e: {}", "a: {") == " b: { c: 1 }, d: `${x}` "


@pytest.mark.parametrize("kind", list(ErrorKind))
def test_every_error_kind_has_words_in_the_ui(kind: ErrorKind) -> None:
    assert has_key(block(ERRORS_JS, "errors: {"), kind.value)


@pytest.mark.parametrize("kind", list(ErrorKind))
def test_every_error_kind_has_a_tone_and_buttons_in_the_editor(kind: ErrorKind) -> None:
    assert has_key(block(JOB_VIEW_JS, "ERROR_KINDS = {"), kind.value)


@pytest.mark.parametrize("reason", get_args(ErrorReason))
def test_every_error_reason_has_words_in_the_ui(reason: str) -> None:
    assert has_key(block(ERRORS_JS, "errors: {"), reason)


@pytest.mark.parametrize("place", [place for place in get_args(ErrorPlace) if place != "library"])
def test_a_disk_error_outside_the_library_has_its_own_words_and_buttons(place: str) -> None:
    # Without these, the error would offer to choose another library folder.
    assert has_key(block(block(ERRORS_JS, "disk: {"), "variants: {"), place)
    assert has_key(block(block(JOB_VIEW_JS, "disk: {"), "variants: {"), place)


@pytest.mark.parametrize("problem", list(WindowProblem))
def test_every_sign_in_window_problem_has_words(problem: WindowProblem) -> None:
    assert has_key(block(ACCOUNT_JS, "windowProblems: {"), problem.value)


@pytest.mark.parametrize("problem", list(TokenProblem))
def test_every_refused_paste_has_words(problem: TokenProblem) -> None:
    assert has_key(block(ACCOUNT_JS, "refused: {"), problem.value)


def test_a_browser_profile_left_after_signing_out_has_words() -> None:
    assert has_key(block(ACCOUNT_JS, "signOutProblems: {"), PROFILE_LEFT)


@pytest.mark.parametrize("opening", ["problems: {", "windowProblems: {", "signOutProblems: {"])
def test_every_sign_in_task_has_words_for_an_unexpected_problem(opening: str) -> None:
    assert has_key(block(ACCOUNT_JS, opening), "other")
