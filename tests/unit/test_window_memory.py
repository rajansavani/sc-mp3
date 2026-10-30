"""Tests for keeping the window's place on Windows, and leaving it alone everywhere else."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scmp3.app import win_monitors, window_memory, window_place
from scmp3.app.win_monitors import Monitors
from scmp3.app.window_fit import Area, Display
from scmp3.app.window_memory import WindowMemory, remembers_place
from scmp3.app.window_place import WindowPlace

MINIMUM = (980, 640)
DEFAULT = (1280, 860)
LAPTOP = Display(Area(0, 0, 1920, 1080), Area(0, 0, 1920, 1040), primary=True)
ONE_SCREEN = Monitors([LAPTOP], 1.0)


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> "FakeEvent":
        self.handlers.append(handler)
        return self


def fake_window() -> Any:
    names = ("resized", "moved", "maximized", "minimized", "restored")
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


def test_elsewhere_window_json_is_neither_read_nor_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: Any) -> None:
        pytest.fail("window.json was touched")

    monkeypatch.setattr(window_memory, "remembers_place", lambda: False)
    monkeypatch.setattr(window_place, "load", refuse)
    assert window_memory.for_this_platform(DEFAULT, MINIMUM, monitors=refuse) is None


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
