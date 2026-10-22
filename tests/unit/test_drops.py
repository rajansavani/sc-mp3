"""Tests for the Python drop handler, with drop events shaped the way pywebview sends them."""

import logging
from typing import Any

import pytest

from scmp3.app import drops
from scmp3.app.drops import SAME_NAME_DROP, FileDrops

SONG = r"C:\Music\old songs\Sunday Mornings.mp3"
OTHER = r"D:\Downloads\Night Bus.m4a"


class FakeJobs:
    """Records what reaches add_inputs, and turns down the inputs it's told to."""

    def __init__(self, refuse: tuple[str, ...] = ()) -> None:
        self.added: list[str] = []
        self._refuse = refuse

    def add_inputs(self, text: str) -> dict[str, Any]:
        self.added.append(text)
        rejected = [
            {"input": line, "reason": "FILES_NOT_SUPPORTED"}
            for line in text.splitlines()
            if line in self._refuse
        ]
        return {"added": [], "rejected": rejected}


class FakeEvents:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.sent.append(event)


def drop(*files: dict[str, Any]) -> dict[str, Any]:
    """A drop event as pywebview serializes it for a Python handler."""
    return {"type": "drop", "dataTransfer": {"files": list(files), "dropEffect": "none"}}


def dropped_file(path: str, name: str | None = None) -> dict[str, Any]:
    return {"name": name or path.rsplit("\\", 1)[-1], "size": 3, "pywebviewFullPath": path}


def handler(jobs: Any, events: FakeEvents, leftovers: list[Any] | None = None) -> FileDrops:
    """A drop handler that never touches pywebview's own list of dropped paths."""
    return FileDrops(jobs, events, leftover_paths=lambda: list(leftovers or []))


REFUSED_FOR_SAME_NAMES = {"type": "rejected", "rejected": [{"input": "", "reason": SAME_NAME_DROP}]}


@pytest.fixture
def jobs() -> FakeJobs:
    return FakeJobs()


@pytest.fixture
def events() -> FakeEvents:
    return FakeEvents()


def test_dropped_files_are_added_by_their_full_paths_one_a_line(
    jobs: FakeJobs, events: FakeEvents
) -> None:
    handler(jobs, events).dropped(drop(dropped_file(SONG), dropped_file(OTHER)))
    assert jobs.added == [f"{SONG}\n{OTHER}"]
    assert events.sent == []


def test_files_that_werent_added_are_told_like_a_paste(events: FakeEvents) -> None:
    jobs = FakeJobs(refuse=(SONG,))
    handler(jobs, events).dropped(drop(dropped_file(SONG)))
    assert events.sent == [
        {"type": "rejected", "rejected": [{"input": SONG, "reason": "FILES_NOT_SUPPORTED"}]}
    ]


def test_a_drop_of_two_files_with_the_same_name_is_refused(
    jobs: FakeJobs, events: FakeEvents
) -> None:
    # pywebview matches paths to files by name, so these two could swap paths.
    first = dropped_file(r"C:\a\song.mp3")
    second = dropped_file(r"C:\b\song.mp3")
    handler(jobs, events).dropped(drop(first, second))
    assert jobs.added == []
    assert events.sent == [REFUSED_FOR_SAME_NAMES]


def test_a_renamed_twin_whose_path_was_left_over_is_refused_too(
    jobs: FakeJobs, events: FakeEvents
) -> None:
    # WebView2 calls the second "song (1).mp3", so pywebview matched only the first one's path.
    first = dropped_file(r"C:\a\song.mp3")
    second = {"name": "song (1).mp3", "size": 3}
    leftover = [("song.mp3", r"C:\b\song.mp3")]
    handler(jobs, events, leftover).dropped(drop(first, second))
    assert jobs.added == []
    assert events.sent == [REFUSED_FOR_SAME_NAMES]


def test_a_path_left_from_a_drop_elsewhere_doesnt_refuse_the_next_drop(
    jobs: FakeJobs, events: FakeEvents
) -> None:
    # On macOS and Linux pywebview keeps the path of an image dropped on the cover too.
    stale = [("cover.png", r"C:\Pictures\cover.png")]
    handler(jobs, events, stale).dropped(drop(dropped_file(SONG)))
    assert jobs.added == [SONG]
    assert events.sent == []


def test_a_path_left_with_the_dropped_files_name_refuses_the_drop(
    jobs: FakeJobs, events: FakeEvents
) -> None:
    # pywebview took one of two "song.mp3" paths for the file, and it may not be this drop's.
    stale = [("song.mp3", r"C:\old\song.mp3")]
    handler(jobs, events, stale).dropped(drop(dropped_file(r"C:\new\song.mp3")))
    assert jobs.added == []
    assert events.sent == [REFUSED_FOR_SAME_NAMES]


def test_a_name_pywebview_decoded_by_mistake_gets_its_path(
    jobs: FakeJobs, events: FakeEvents
) -> None:
    # pywebview compares "my song.mp3" with "my%20song.mp3", so this file came without its path.
    unmatched = {"name": "my%20song.mp3", "size": 3}
    leftover = [("my%20song.mp3", r"C:\Music\my%20song.mp3")]
    handler(jobs, events, leftover).dropped(drop(unmatched))
    assert jobs.added == [r"C:\Music\my%20song.mp3"]


def test_names_that_are_the_same_once_decoded_are_twins(jobs: FakeJobs, events: FakeEvents) -> None:
    first = dropped_file(r"C:\a\my song.mp3")
    second = dropped_file(r"C:\b\my%20song.mp3")
    handler(jobs, events).dropped(drop(first, second))
    assert jobs.added == []
    assert events.sent == [REFUSED_FOR_SAME_NAMES]


def test_pywebviews_leftover_paths_are_cleared_after_each_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Left in place, a later drop of another "song.mp3" would get this old path.
    state = {"num_listeners": 1, "paths": [("song.mp3", r"C:\b\song.mp3")]}
    monkeypatch.setattr("webview.dom._dnd_state", state)
    assert drops.take_pywebview_leftovers() == [("song.mp3", r"C:\b\song.mp3")]
    assert state["paths"] == []


def test_a_drop_without_files_does_nothing(jobs: FakeJobs, events: FakeEvents) -> None:
    # A dropped link has no files, and the page adds it itself.
    handler(jobs, events).dropped(drop())
    handler(jobs, events).dropped({"type": "drop"})
    assert jobs.added == []
    assert events.sent == []


def test_files_pywebview_found_no_path_for_are_left_out(
    jobs: FakeJobs, events: FakeEvents, caplog: pytest.LogCaptureFixture
) -> None:
    # Such as an image dragged out of a browser.
    pathless = {"name": "from-a-browser.jpg", "size": 10}
    with caplog.at_level(logging.INFO):
        handler(jobs, events).dropped(drop(pathless, dropped_file(SONG)))
        handler(jobs, events).dropped(drop(pathless))
    assert jobs.added == [SONG]
    assert "1 dropped files came without their path" in caplog.text


def test_a_failure_while_adding_is_logged_with_its_traceback(
    events: FakeEvents, caplog: pytest.LogCaptureFixture
) -> None:
    class BrokenJobs:
        def add_inputs(self, text: str) -> dict[str, Any]:
            raise RuntimeError("a bug")

    handler(BrokenJobs(), events).dropped(drop(dropped_file(SONG)))
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert record.exc_info is not None
    assert "couldn't be handled" in record.getMessage()


def test_the_handler_is_bound_to_the_pages_body(jobs: FakeJobs, events: FakeEvents) -> None:
    bound: list[tuple[str, Any]] = []

    class Body:
        def on(self, event: str, callback: Any) -> None:
            bound.append((event, callback))

    class Window:
        dom = type("Dom", (), {"body": Body()})()

    drop_handler = handler(jobs, events)
    drop_handler.bind(Window())
    [(event, callback)] = bound
    assert event == "drop"
    assert callback.callback == drop_handler.dropped


def test_a_handler_that_cant_be_bound_is_logged_and_the_window_goes_on(
    jobs: FakeJobs, events: FakeEvents, caplog: pytest.LogCaptureFixture
) -> None:
    class Window:
        @property
        def dom(self) -> Any:
            raise RuntimeError("the page went away")

    handler(jobs, events).bind(Window())
    assert "drop handler wasn't set up" in caplog.text
