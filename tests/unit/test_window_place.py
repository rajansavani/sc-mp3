"""Tests for keeping the main window's size, position and maximized state between runs."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scmp3.app import main, window_place
from scmp3.app.window_place import PlaceTracker, WindowPlace

MINIMUM = (980, 640)
DEFAULT = (1280, 860)


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> "FakeEvent":
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self) -> None:
        names = ("resized", "moved", "maximized", "minimized", "restored")
        self.events = SimpleNamespace(**{name: FakeEvent() for name in names})


@pytest.fixture
def clock() -> Clock:
    return Clock()


def tracker(clock: Clock, start: WindowPlace | None = None) -> PlaceTracker:
    return PlaceTracker(start or WindowPlace(*DEFAULT), MINIMUM, clock)


def test_a_saved_place_loads_back(tmp_path: Path) -> None:
    path = tmp_path / "window.json"
    place = WindowPlace(width=1400, height=900, x=40, y=60, maximized=True)
    window_place.save(path, place)
    assert window_place.load(path) == place


def test_no_file_means_no_saved_place(tmp_path: Path) -> None:
    assert window_place.load(tmp_path / "window.json") is None


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"width": 1400}',
        '{"width": "wide", "height": 900}',
        '{"width": true, "height": 900}',
        '{"width": -5, "height": 900}',
        '{"width": Infinity, "height": 900}',
        '{"width": 1400, "height": NaN}',
        '{"width": 3000000000, "height": 720}',
        '{"width": 1100, "height": 720, "x": -200000, "y": 0}',
        '{"width": 0, "height": 720}',
        pytest.param('{"width": 1' + "0" * 400 + ', "height": 720}', id="400 digits"),
        pytest.param("[" * 100_000 + "]" * 100_000, id="nested 100,000 deep"),
    ],
)
def test_a_file_that_cant_be_used_is_ignored(tmp_path: Path, text: str) -> None:
    # Each of these is ignored, never raised, so the app always starts.
    path = tmp_path / "window.json"
    path.write_text(text, encoding="utf-8")
    assert window_place.load(path) is None


def test_a_place_without_a_position_loads_without_one(tmp_path: Path) -> None:
    path = tmp_path / "window.json"
    path.write_text(json.dumps({"width": 1100, "height": 700.4}), encoding="utf-8")
    assert window_place.load(path) == WindowPlace(width=1100, height=700)


def test_a_failed_save_is_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    folder_in_the_way = tmp_path / "file"
    folder_in_the_way.write_text("", encoding="utf-8")
    window_place.save(folder_in_the_way / "window.json", WindowPlace(1300, 900))
    assert "Couldn't keep the window's size and position" in caplog.text


def test_the_first_run_opens_at_the_default_size() -> None:
    assert window_place.window_options(None, DEFAULT) == {"width": 1280, "height": 860}


def test_a_checked_place_opens_where_it_says() -> None:
    place = WindowPlace(width=1400, height=900, x=40, y=60, maximized=True)
    options = window_place.window_options(place, DEFAULT)
    assert options == {"width": 1400, "height": 900, "x": 40, "y": 60, "maximized": True}


def test_the_tracker_keeps_the_last_size_and_position(clock: Clock) -> None:
    places = tracker(clock)
    places.resized(1300, 880)
    places.moved(100, 50)
    places.resized(1350, 900)
    assert places.place() == WindowPlace(width=1350, height=900, x=100, y=50)


def test_with_no_reports_the_tracker_keeps_the_place_it_started_with(clock: Clock) -> None:
    start = WindowPlace(width=1400, height=900, x=40, y=60)
    assert tracker(clock, start).place() == start


def test_a_maximize_keeps_the_size_from_before_it(clock: Clock) -> None:
    places = tracker(clock)
    places.resized(1300, 880)
    places.moved(100, 50)
    clock.advance(5)
    # The full-screen size can arrive just before the maximized event, from another thread.
    places.resized(1920, 1040)
    places.moved(-8, -8)
    clock.advance(0.05)
    places.maximized()
    places.resized(1920, 1040)
    assert places.place() == WindowPlace(width=1300, height=880, x=100, y=50, maximized=True)


def test_restoring_from_maximized_keeps_the_restored_size(clock: Clock) -> None:
    places = tracker(clock)
    places.resized(1300, 880)
    clock.advance(5)
    places.maximized()
    places.resized(1920, 1040)
    clock.advance(5)
    places.restored()
    places.resized(1300, 880)
    assert places.place() == WindowPlace(width=1300, height=880)


def test_minimizing_a_maximized_window_keeps_it_maximized(clock: Clock) -> None:
    places = tracker(clock)
    places.resized(1300, 880)
    clock.advance(5)
    places.maximized()
    places.resized(1920, 1040)
    clock.advance(5)
    places.minimized()
    places.moved(-32000, -32000)
    places.resized(160, 28)
    clock.advance(5)
    places.restored()
    places.maximized()
    places.resized(1920, 1040)
    assert places.place() == WindowPlace(width=1300, height=880, maximized=True)


def test_a_minimized_window_keeps_its_place(clock: Clock) -> None:
    places = tracker(clock)
    places.resized(1300, 880)
    places.moved(100, 50)
    places.minimized()
    places.moved(-32000, -32000)
    places.resized(160, 28)
    assert places.place() == WindowPlace(width=1300, height=880, x=100, y=50)


def test_a_window_that_opens_maximized_keeps_its_saved_size(clock: Clock) -> None:
    start = WindowPlace(width=1300, height=880, x=100, y=50, maximized=True)
    places = tracker(clock, start)
    places.maximized()
    places.resized(1920, 1040)
    places.moved(-8, -8)
    assert places.place() == start


def test_the_tracker_listens_to_the_windows_events(clock: Clock) -> None:
    window = FakeWindow()
    tracker(clock).attach(window)
    for name in ("resized", "moved", "maximized", "minimized", "restored"):
        assert len(getattr(window.events, name).handlers) == 1


def test_the_main_window_opens_where_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, Any] = {}

    class Toolkit:
        def __init__(self) -> None:
            self.settings: dict[str, Any] = {}

        def create_window(self, title: str, url: str | None = None, **options: Any) -> Any:
            created.update(options)
            raise RuntimeError("stop here")

    place = WindowPlace(width=1400, height=900, x=40, y=60)
    with pytest.raises(RuntimeError, match="stop here"):
        main.open_window(Toolkit(), None, None, page="index.html", place=place)
    assert (created["width"], created["height"], created["x"], created["y"]) == (1400, 900, 40, 60)
    assert created["min_size"] == main.MIN_WINDOW_SIZE
