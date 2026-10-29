"""Tests for asking before the window closes while unsaved tracks can't be kept for next time."""

import inspect
import sys
from types import SimpleNamespace

import pytest

from scmp3.app import closing, messages
from scmp3.app.closing import Asking, CloseGuard, ask_on_windows, close_guard
from scmp3.app.message_box import IDNO, IDYES, MB_DEFBUTTON2, MB_ICONWARNING, MB_YESNO


class UnsavedJobs:
    def __init__(self, count: int, *, kept: bool = False) -> None:
        self.count = count
        self.kept = kept
        self.keeps = 0

    def unsaved_count(self) -> int:
        return self.count

    def keep_queue(self) -> bool:
        self.keeps += 1
        return self.kept


class Asker:
    """Answers the question with a fixed answer, and remembers it."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.questions: list[str] = []
        self.parents: list[object] = []

    def __call__(self, question: str, parent: object = None) -> bool:
        self.questions.append(question)
        self.parents.append(parent)
        return self.answer


def asking(ask: Asker, *, session_ending: bool = False) -> Asking:
    return Asking(ask, lambda: session_ending)


def guard(jobs: UnsavedJobs, found: Asking | None) -> CloseGuard:
    return CloseGuard(jobs, lambda: found)


def test_nothing_is_asked_when_every_track_is_saved_or_skipped() -> None:
    ask = Asker(False)
    assert guard(UnsavedJobs(0), asking(ask)).closing() is True
    assert ask.questions == []


def test_nothing_is_asked_once_the_queue_is_kept_for_next_time() -> None:
    ask = Asker(False)
    jobs = UnsavedJobs(3, kept=True)
    assert guard(jobs, asking(ask)).closing() is True
    assert jobs.keeps == 1
    assert ask.questions == []


@pytest.mark.parametrize("answer", [True, False])
def test_the_answer_decides_when_the_queue_cant_be_kept(answer: bool) -> None:
    ask = Asker(answer)
    assert guard(UnsavedJobs(3), asking(ask)).closing() is answer
    assert ask.questions == [messages.unsaved(3)]


def test_nothing_is_asked_while_windows_ends_the_session() -> None:
    # Nobody is there to answer, and the question would hold up logging off.
    ask = Asker(False)
    assert guard(UnsavedJobs(2), asking(ask, session_ending=True)).closing() is True
    assert ask.questions == []


def test_without_a_way_to_ask_the_window_closes_and_keeps_nothing() -> None:
    jobs = UnsavedJobs(3)
    assert guard(jobs, None).closing() is True
    assert jobs.keeps == 0


def test_the_question_counts_the_tracks_and_says_why_it_asks() -> None:
    assert messages.unsaved(3).startswith("3 tracks haven't been saved, and sc-mp3 couldn't keep")
    assert messages.unsaved(1).startswith("1 track hasn't been saved, and sc-mp3 couldn't keep")
    assert "Close anyway?" in messages.unsaved(2)
    for text in (messages.unsaved(1), messages.unsaved(5)):
        assert "!" not in text
        assert "—" not in text


@pytest.mark.parametrize(("button", "closes"), [(IDYES, True), (IDNO, False)])
def test_windows_asks_with_a_yes_or_no_box_that_defaults_to_no(button: int, closes: bool) -> None:
    shown: list[tuple[str, str, int]] = []

    def message_box(text: str, title: str, flags: int) -> int:
        shown.append((text, title, flags))
        return button

    question = "3 tracks haven't been saved. Close anyway?"
    assert ask_on_windows(question, message_box=message_box) is closes
    [(text, title, flags)] = shown
    assert text == question
    assert title == "sc-mp3"
    assert flags & MB_YESNO and flags & MB_ICONWARNING and flags & MB_DEFBUTTON2


@pytest.mark.skipif(sys.platform != "win32", reason="asks Windows itself")
def test_windows_says_this_session_isnt_ending() -> None:
    assert closing.windows_session_ending() is False


@pytest.mark.parametrize(
    ("renderer", "ask"),
    [
        ("edgechromium", closing.ask_on_windows),
        ("gtkwebkit2", closing.ask_with_gtk),
        ("qtwebengine", closing.ask_with_qt),
        ("wkwebview", closing.ask_on_macos),
    ],
)
def test_each_renderer_asks_with_its_own_toolkit(renderer: str, ask: closing.Ask) -> None:
    found = closing.asking_for(renderer)
    assert found is not None
    assert found.ask is ask


def test_only_windows_tells_that_the_session_is_ending() -> None:
    for renderer in ("gtkwebkit2", "qtwebengine", "wkwebview"):
        assert closing.ASKERS[renderer].session_ending is closing.session_goes_on
    assert closing.ASKERS["edgechromium"].session_ending is closing.windows_session_ending
    assert closing.session_goes_on() is False


@pytest.mark.parametrize("renderer", [None, "mshtml", "cef"])
def test_a_renderer_with_no_way_to_ask_closes_the_window(renderer: str | None) -> None:
    jobs = UnsavedJobs(2)
    assert close_guard(jobs, renderer=lambda: renderer).closing() is True
    assert jobs.keeps == 0


def test_the_renderer_is_looked_up_when_the_window_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    ask = Asker(False)
    monkeypatch.setitem(closing.ASKERS, "qtwebengine", asking(ask))
    chosen: list[str | None] = [None]
    handler = close_guard(UnsavedJobs(2), renderer=lambda: chosen[0])
    # pywebview picks its renderer in start(), after the handler is made.
    chosen[0] = "qtwebengine"
    assert handler.closing() is False
    assert ask.questions == [messages.unsaved(2)]


def test_the_question_is_asked_over_the_window_that_is_closing() -> None:
    ask = Asker(False)
    native = object()
    handler = guard(UnsavedJobs(2), asking(ask))
    assert handler.closing(SimpleNamespace(native=native)) is False
    # Called with no window, as by a test's fake event, the toolkit picks the parent itself.
    assert handler.closing() is False
    assert ask.parents == [native, None]


def test_pywebview_hands_the_closing_handler_its_window() -> None:
    # pywebview passes the window only to a handler with a parameter named window.
    assert "window" in inspect.signature(guard(UnsavedJobs(0), None).closing).parameters


def test_the_question_splits_into_its_first_paragraph_and_the_rest() -> None:
    text, detail = closing.split_question(messages.unsaved(2))
    assert text.endswith("Close anyway?")
    assert detail == "Closing removes them from the queue."
    assert closing.split_question("Close anyway?") == ("Close anyway?", "")
