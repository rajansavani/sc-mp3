"""Tests for reading the screens on macOS and following the Cocoa window's frame."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scmp3.app import toolkit_screens
from scmp3.app.cocoa_screens import cocoa_displays, watch_cocoa_moves
from scmp3.app.win_monitors import Monitors
from scmp3.app.window_fit import Area, Display
from scmp3.app.window_memory import WindowMemory
from scmp3.app.window_place import WindowPlace

MINIMUM = (980, 640)
DEFAULT = (1280, 860)


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> "FakeEvent":
        self.handlers.append(handler)
        return self


def rect(x: float, y: float, width: float, height: float) -> Any:
    """An NSRect, with y measured up from the bottom of the menu bar's screen."""
    return SimpleNamespace(
        origin=SimpleNamespace(x=x, y=y), size=SimpleNamespace(width=width, height=height)
    )


def ns_screen(frame: Any, visible: Any) -> Any:
    return SimpleNamespace(frame=lambda: frame, visibleFrame=lambda: visible)


def test_screens_are_measured_down_from_the_top_of_the_menu_bars_screen() -> None:
    # A 1440 x 900 laptop with a 25 pt menu bar and a 70 pt Dock, and a monitor above it.
    laptop = ns_screen(rect(0, 0, 1440, 900), rect(0, 70, 1440, 805))
    above = ns_screen(rect(-200, 900, 1920, 1080), rect(-200, 900, 1920, 1055))
    assert cocoa_displays([], [laptop, above]) == [
        Display(Area(0, 0, 1440, 900), Area(0, 25, 1440, 805), primary=True),
        Display(Area(-200, -1080, 1920, 1080), Area(-200, -1055, 1920, 1055)),
    ]


def test_with_no_screens_there_are_no_displays() -> None:
    assert cocoa_displays([], []) == []


def test_the_frame_is_read_from_the_window_rather_than_the_moved_event() -> None:
    # pywebview flips y against the screen the window is on, so a window on the monitor above
    # would come out wrong. The frame is flipped against the first screen instead.
    native = SimpleNamespace(frame=lambda: rect(100, 1200, 1000, 700))
    window = SimpleNamespace(
        native=native, screen=SimpleNamespace(height=900), events=SimpleNamespace(moved=FakeEvent())
    )
    reported: list[tuple[float, float]] = []
    watch_cocoa_moves(window, lambda x, y: reported.append((x, y)))
    window.events.moved.handlers[0]()
    assert reported == [(100, -1000)]


def test_a_window_placed_without_a_screen_reports_no_moves() -> None:
    window = SimpleNamespace(native=None, screen=None, events=SimpleNamespace(moved=FakeEvent()))
    reported: list[tuple[float, float]] = []
    watch_cocoa_moves(window, lambda x, y: reported.append((x, y)))
    window.events.moved.handlers[0]()
    assert reported == []


def test_on_macos_a_window_saved_in_full_screen_opens_at_its_normal_place(tmp_path: Path) -> None:
    # pywebview's Cocoa window can't open in full screen, only cover the screen.
    path = tmp_path / "window.json"
    saved = {"width": 1000, "height": 700, "x": 100, "y": 60, "maximized": True}
    path.write_text(json.dumps(saved), "utf-8")
    laptop = Display(Area(0, 0, 1440, 900), Area(0, 25, 1440, 805), primary=True)
    backend = toolkit_screens.BACKENDS["wkwebview"][1]
    memory = WindowMemory(path, Monitors([laptop]), DEFAULT, MINIMUM, backend=backend)
    assert memory.place == WindowPlace(1000, 700, x=100, y=60)


def test_on_macos_the_size_from_before_full_screen_is_kept() -> None:
    backend = toolkit_screens.BACKENDS["wkwebview"][1]
    assert backend.frame == (0, 0)
    assert backend.opens_maximized is False
    assert backend.maximize_settle_s == toolkit_screens.FULL_SCREEN_SETTLE_S
