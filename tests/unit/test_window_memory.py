"""Tests for keeping the window's place on Windows, Linux and macOS, and leaving it alone with Qt
on Windows."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scmp3.app import win_monitors, window_memory, window_place
from scmp3.app.toolkit_screens import ToolkitScreens
from scmp3.app.win_monitors import Monitors
from scmp3.app.window_fit import Area, Display
from scmp3.app.window_memory import WindowMemory, remembers_place, remembers_toolkit_place
from scmp3.app.window_place import Backend, ScreenOrigin, WindowPlace

MINIMUM = (980, 640)
DEFAULT = (1280, 860)
LAPTOP = Display(Area(0, 0, 1920, 1080), Area(0, 0, 1920, 1040), primary=True)
ONE_SCREEN = Monitors([LAPTOP], 1.0)
FRAME = (8, 40)
# A laptop on the right as the primary screen, with a monitor on its left.
LEFT = Display(Area(0, 0, 1000, 800), Area(0, 0, 1000, 800))
RIGHT = Display(Area(1000, 0, 1400, 900), Area(1000, 0, 1400, 900), primary=True)


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> "FakeEvent":
        self.handlers.append(handler)
        return self


def fake_window() -> Any:
    names = ("resized", "moved", "maximized", "minimized", "restored", "before_show", "closing")
    return SimpleNamespace(events=SimpleNamespace(**{name: FakeEvent() for name in names}))


@pytest.mark.parametrize(
    ("platform", "environ", "remembers"),
    [
        ("win32", {}, True),
        ("win32", {"PYWEBVIEW_GUI": "Qt"}, False),
        ("linux", {}, False),
        ("darwin", {}, False),
    ],
)
def test_only_windows_with_its_own_window_remembers_the_place(
    platform: str, environ: dict[str, str], remembers: bool
) -> None:
    assert remembers_place(platform, environ) is remembers


@pytest.mark.parametrize(
    ("platform", "remembers"), [("linux", True), ("darwin", True), ("win32", False)]
)
def test_linux_and_macos_remember_the_place_through_pywebviews_toolkit(
    platform: str, remembers: bool
) -> None:
    assert remembers_toolkit_place(platform) is remembers


def test_elsewhere_window_json_is_neither_read_nor_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: Any) -> None:
        pytest.fail("window.json was touched")

    monkeypatch.setattr(window_memory, "remembers_place", lambda: False)
    monkeypatch.setattr(window_memory, "remembers_toolkit_place", lambda: False)
    monkeypatch.setattr(window_place, "load", refuse)
    found = window_memory.for_this_platform(
        DEFAULT, MINIMUM, monitors=refuse, toolkit_screens=refuse
    )
    assert found is None


def test_on_linux_a_toolkit_whose_screens_cant_be_read_keeps_no_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(window_memory, "remembers_place", lambda: False)
    monkeypatch.setattr(window_memory, "remembers_toolkit_place", lambda: True)
    assert window_memory.for_this_platform(DEFAULT, MINIMUM, toolkit_screens=lambda: None) is None


def test_on_linux_the_place_comes_from_the_toolkits_screens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "window.json"
    path.write_text(json.dumps({"width": 980, "height": 700, "x": 10, "y": 50}), "utf-8")
    monkeypatch.setattr(window_memory, "remembers_place", lambda: False)
    monkeypatch.setattr(window_memory, "remembers_toolkit_place", lambda: True)
    monkeypatch.setattr(window_place, "default_path", lambda: path)
    origin = ScreenOrigin(object(), 1000, 0)
    found = ToolkitScreens([LEFT, RIGHT], origin, Backend(frame=FRAME))
    memory = window_memory.for_this_platform(DEFAULT, MINIMUM, toolkit_screens=lambda: found)
    assert memory is not None
    assert memory.place == WindowPlace(980, 700, x=10, y=50)
    assert memory.origin is origin


def test_the_window_opens_at_the_saved_place_fitted_to_the_monitors(tmp_path: Path) -> None:
    path = tmp_path / "window.json"
    path.write_text(json.dumps({"width": 2400, "height": 1300, "x": 300, "y": 100}), "utf-8")
    memory = WindowMemory(path, ONE_SCREEN, DEFAULT, MINIMUM)
    assert memory.place == WindowPlace(1920, 1040, x=0, y=0)


def test_the_place_kept_is_where_the_window_was(tmp_path: Path) -> None:
    path = tmp_path / "window.json"
    memory = WindowMemory(path, ONE_SCREEN, DEFAULT, MINIMUM)
    window = fake_window()
    memory.attach(window)
    window.events.resized.handlers[0](1100, 720)
    window.events.moved.handlers[0](150, 120)
    memory.keep()
    assert window_place.load(path) == WindowPlace(1100, 720, x=150, y=120)


def test_the_place_kept_is_fitted_with_the_monitors_read_at_the_start(tmp_path: Path) -> None:
    path = tmp_path / "window.json"
    memory = WindowMemory(path, ONE_SCREEN, DEFAULT, MINIMUM)
    window = fake_window()
    memory.attach(window)
    window.events.moved.handlers[0](5000, 120)
    memory.keep()
    assert window_place.load(path) == WindowPlace(1280, 860, x=320, y=90)


def test_at_125_percent_a_place_left_of_the_primary_is_kept_where_it_comes_back(
    tmp_path: Path,
) -> None:
    left = Display(Area(-1536, 0, 1536, 864), Area(-1536, 0, 1536, 832))
    path = tmp_path / "window.json"
    memory = WindowMemory(path, Monitors([LAPTOP, left], 1.25), DEFAULT, MINIMUM)
    window = fake_window()
    memory.attach(window)
    window.events.resized.handlers[0](1101, 721)
    window.events.moved.handlers[0](-1399, 61)
    memory.keep()
    kept = window_place.load(path)
    assert kept is not None and kept.x is not None
    assert win_monitors.round_trip(kept.x, 1.25) == kept.x
    assert abs(kept.x - -1399) <= 3


def test_with_its_own_watcher_the_frames_moves_are_kept_instead_of_pywebviews(
    tmp_path: Path,
) -> None:
    watched: list[Any] = []

    def watch(window: Any, report: Any) -> None:
        watched.append(report)

    path = tmp_path / "window.json"
    backend = Backend(frame=FRAME, watch_moves=watch)
    memory = WindowMemory(path, Monitors([LEFT, RIGHT]), DEFAULT, MINIMUM, backend=backend)
    window = fake_window()
    memory.attach(window)
    assert window.events.moved.handlers == []
    window.events.resized.handlers[0](980, 700)
    watched[0](10, 50)
    memory.keep()
    assert window_place.load(path) == WindowPlace(980, 700, x=10, y=50)


def test_with_the_primary_on_the_right_a_place_on_the_left_stays_there(tmp_path: Path) -> None:
    path = tmp_path / "window.json"
    path.write_text(json.dumps({"width": 980, "height": 700, "x": 10, "y": 50}), "utf-8")
    backend = Backend(frame=FRAME)
    for _start in range(5):
        memory = WindowMemory(path, Monitors([LEFT, RIGHT]), DEFAULT, MINIMUM, backend=backend)
        window = fake_window()
        memory.attach(window)
        # Qt reports the frame where it was placed, in desktop coordinates.
        window.events.moved.handlers[0](memory.place.x, memory.place.y)
        memory.keep()
    assert window_place.load(path) == WindowPlace(980, 700, x=10, y=50)


def test_where_the_toolkit_says_the_window_is_at_close_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "window.json"
    backend = Backend(frame=FRAME, read_at_close=lambda window: window.normal)
    memory = WindowMemory(path, Monitors([LEFT, RIGHT]), DEFAULT, MINIMUM, backend=backend)
    window = fake_window()
    memory.attach(window)
    window.events.resized.handlers[0](1150, 760)
    window.normal = WindowPlace(1000, 700, x=1200, y=100)
    window.events.closing.handlers[0](window)
    memory.keep()
    assert window_place.load(path) == WindowPlace(1000, 700, x=1200, y=100)


def test_a_place_that_cant_be_read_at_close_leaves_the_reported_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(window: Any) -> WindowPlace:
        raise RuntimeError("wrapped C/C++ object has been deleted")

    path = tmp_path / "window.json"
    backend = Backend(frame=FRAME, read_at_close=broken)
    memory = WindowMemory(path, Monitors([LEFT, RIGHT]), DEFAULT, MINIMUM, backend=backend)
    window = fake_window()
    memory.attach(window)
    window.events.resized.handlers[0](1000, 700)
    window.events.moved.handlers[0](1200, 100)
    window.events.closing.handlers[0](window)
    memory.keep()
    assert window_place.load(path) == WindowPlace(1000, 700, x=1200, y=100)
    assert "Couldn't read the window's place as it closed" in caplog.text


def test_the_read_at_close_gets_the_window_and_never_cancels_the_close(tmp_path: Path) -> None:
    from webview.event import Event

    read: list[Any] = []

    def reader(window: Any) -> WindowPlace:
        read.append(window)
        return WindowPlace(1000, 700, x=10, y=50)

    backend = Backend(frame=FRAME, read_at_close=reader)
    memory = WindowMemory(
        tmp_path / "window.json", Monitors([LEFT, RIGHT]), DEFAULT, MINIMUM, backend=backend
    )
    window = fake_window()
    # pywebview's own closing event, which runs its handlers on the toolkit's thread.
    window.events.closing = Event(window, should_lock=True)
    memory.attach(window)
    cancelled = window.events.closing.set()
    assert read == [window]
    assert cancelled is False


def test_on_windows_nothing_is_read_as_the_window_closes(tmp_path: Path) -> None:
    memory = WindowMemory(tmp_path / "window.json", ONE_SCREEN, DEFAULT, MINIMUM)
    window = fake_window()
    memory.attach(window)
    assert window.events.closing.handlers == []
