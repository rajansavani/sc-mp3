"""window.json: the main window's size, position and maximized state, kept between runs.

window_fit.py checks a place against the screens before the window opens. The window reports
its size and position through pywebview's events, which arrive on their own threads in no fixed
order. A maximize sends the full-screen size too, so the size kept is the last one reported well
before the window was maximized.
"""

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from scmp3.core import paths
from scmp3.core.errors import ScmpError

FILE_NAME = "window.json"
# Sizes and positions reported this close before a maximize may already be the maximized ones.
MAXIMIZE_SETTLE_S = 0.5
# How many recent reports are kept to look back past a maximize.
KEPT_REPORTS = 16
# Windows parks a minimized window at -32000, so positions this far out aren't real ones.
PARKED_LIMIT = -10000
# The sizes and positions a saved file may hold. Anything else is damage, and pythonnet can't
# even build a window over 2**31 pixels wide.
SIZE_RANGE = (1, 100_000)
POSITION_RANGE = (-100_000, 100_000)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WindowPlace:
    """Where the window was: its size and position when not maximized, and whether it was."""

    width: int
    height: int
    x: int | None = None
    y: int | None = None
    maximized: bool = False


def default_path() -> Path:
    """Where window.json lives."""
    return paths.data_dir() / FILE_NAME


def load(path: Path) -> WindowPlace | None:
    """The saved place, or None when there's none or it can't be used. It never raises, since
    nothing in this file is worth not starting over."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _place_from(data)
    except FileNotFoundError:
        return None
    # Damage can raise nearly anything, such as RecursionError for deeply nested lists.
    except Exception as error:
        log.warning("Ignoring %s, which can't be used: %r", path.name, error)
        return None


def _place_from(data: Any) -> WindowPlace:
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return WindowPlace(
        width=_whole_number(data["width"], SIZE_RANGE),
        height=_whole_number(data["height"], SIZE_RANGE),
        x=_whole_number(data["x"], POSITION_RANGE) if data.get("x") is not None else None,
        y=_whole_number(data["y"], POSITION_RANGE) if data.get("y") is not None else None,
        maximized=data.get("maximized") is True,
    )


def _whole_number(value: Any, allowed: tuple[int, int]) -> int:
    # bool is an int in Python, but true isn't a size. A range check also turns away Infinity
    # and NaN, without the OverflowError a 400-digit number gives math.isfinite.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{value!r} isn't a number")
    if not allowed[0] <= value <= allowed[1]:
        raise ValueError(f"{value!r} is out of range")
    return round(value)


def save(path: Path, place: WindowPlace) -> None:
    """Write the place. A failure is logged, since it only costs the window's size next time."""
    try:
        paths.write_text_atomically(path, json.dumps(asdict(place)) + "\n")
    except ScmpError as error:
        log.warning("Couldn't keep the window's size and position: %s", error)


def window_options(place: WindowPlace | None, default: tuple[int, int]) -> dict[str, Any]:
    """The create_window options that open the window at a place window_fit has checked."""
    if place is None:
        return {"width": default[0], "height": default[1]}
    options: dict[str, Any] = {
        "width": place.width,
        "height": place.height,
        "maximized": place.maximized,
    }
    if place.x is not None and place.y is not None:
        options.update(x=place.x, y=place.y)
    return options


@dataclass(frozen=True)
class _Report:
    at: float
    size: tuple[int, int] | None = None
    position: tuple[int, int] | None = None


class PlaceTracker:
    """Follows the window's events and knows the place to save when it closes."""

    def __init__(
        self,
        start: WindowPlace,
        minimum: tuple[int, int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._start = start
        self._minimum = minimum
        self._clock = clock
        self._lock = threading.Lock()
        self._reports: deque[_Report] = deque(maxlen=KEPT_REPORTS)
        self._maximized_at: float | None = clock() if start.maximized else None
        self._minimized = False

    def attach(self, window: Any) -> None:
        """Listen to the window's size, position and state events."""
        window.events.resized += self.resized
        window.events.moved += self.moved
        window.events.maximized += self.maximized
        window.events.minimized += self.minimized
        window.events.restored += self.restored

    def resized(self, width: float, height: float) -> None:
        # A minimized window reports a tiny size, under the minimum the window allows.
        if width < self._minimum[0] or height < self._minimum[1]:
            return
        self._report(_Report(self._clock(), size=(round(width), round(height))))

    def moved(self, x: float, y: float) -> None:
        if x <= PARKED_LIMIT or y <= PARKED_LIMIT:
            return
        # macOS reports floats.
        self._report(_Report(self._clock(), position=(round(x), round(y))))

    def maximized(self) -> None:
        # A maximized window that's restored from the taskbar says it's maximized again.
        with self._lock:
            if self._maximized_at is None:
                self._maximized_at = self._clock()
            self._minimized = False

    def minimized(self) -> None:
        with self._lock:
            self._minimized = True

    def restored(self) -> None:
        # Restoring from minimized leaves a maximized window maximized, and pywebview sends
        # the same event for both, so the state is read again from the next maximize.
        with self._lock:
            if self._minimized:
                self._minimized = False
                return
            self._maximized_at = None

    def _report(self, report: _Report) -> None:
        with self._lock:
            if self._minimized:
                return
            self._reports.append(report)

    def place(self) -> WindowPlace:
        """The place to save: the last normal size and position, and whether it's maximized."""
        with self._lock:
            cutoff = None
            if self._maximized_at is not None:
                cutoff = self._maximized_at - MAXIMIZE_SETTLE_S
            normal = [report for report in self._reports if cutoff is None or report.at < cutoff]
            place = replace(self._start, maximized=self._maximized_at is not None)
        size = next((report.size for report in reversed(normal) if report.size), None)
        position = next((report.position for report in reversed(normal) if report.position), None)
        if size:
            place = replace(place, width=size[0], height=size[1])
        if position:
            place = replace(place, x=position[0], y=position[1])
        return place
