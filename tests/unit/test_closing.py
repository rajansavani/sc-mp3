"""Tests for asking before the window closes while tracks in the queue haven't been saved."""

import pytest

from scmp3.app import closing, messages
from scmp3.app.closing import Asking, CloseGuard, ask_on_windows, close_guard
from scmp3.app.message_box import IDNO, IDYES, MB_DEFBUTTON2, MB_ICONWARNING, MB_YESNO


class UnsavedJobs:
    def __init__(self, count: int) -> None:
        self.count = count

    def unsaved_count(self) -> int:
        return self.count


class Asker:
    """Answers the question with a fixed answer, and remembers it."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.questions: list[str] = []

    def __call__(self, question: str) -> bool:
        self.questions.append(question)
        return self.answer


def asking(ask: Asker, *, session_ending: bool = False) -> Asking:
    return Asking(ask, lambda: session_ending)


def test_nothing_is_asked_when_every_track_is_saved_or_skipped() -> None:
    ask = Asker(False)
    assert CloseGuard(UnsavedJobs(0), asking(ask)).closing() is True
    assert ask.questions == []


@pytest.mark.parametrize("answer", [True, False])
def test_the_answer_decides_while_tracks_are_unsaved(answer: bool) -> None:
    ask = Asker(answer)
    assert CloseGuard(UnsavedJobs(3), asking(ask)).closing() is answer
    assert ask.questions == [messages.unsaved(3)]


def test_nothing_is_asked_while_windows_ends_the_session() -> None:
    # Nobody is there to answer, and the question would hold up logging off.
    ask = Asker(False)
    assert CloseGuard(UnsavedJobs(2), asking(ask, session_ending=True)).closing() is True
    assert ask.questions == []


def test_without_a_way_to_ask_the_window_closes() -> None:
    assert CloseGuard(UnsavedJobs(3), None).closing() is True


def test_the_question_counts_the_tracks() -> None:
    assert messages.unsaved(3).startswith("3 tracks haven't been saved. Close anyway?")
    assert messages.unsaved(1).startswith("1 track hasn't been saved. Close anyway?")
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
    assert ask_on_windows(question, message_box) is closes
    [(text, title, flags)] = shown
    assert text == question
    assert title == "sc-mp3"
    assert flags & MB_YESNO and flags & MB_ICONWARNING and flags & MB_DEFBUTTON2


def test_each_os_gets_its_own_way_to_ask() -> None:
    windows = close_guard(UnsavedJobs(1), platform="win32")
    assert windows._asking is closing.ASKERS["win32"]
    assert closing.ASKERS["win32"].ask is closing.ask_on_windows
    assert close_guard(UnsavedJobs(1), platform="darwin").closing() is True


@pytest.mark.skipif(closing.sys.platform != "win32", reason="asks Windows itself")
def test_windows_says_this_session_isnt_ending() -> None:
    assert closing.windows_session_ending() is False
