"""Keeping the main window's size and place between runs, on Windows only.

Every case was checked live on Windows. On Qt, GTK and macOS, pywebview places a window relative
to a screen and reports it in other ways, so there the window opens as it always did and
window.json is neither read nor written.
"""

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from scmp3.app import window_fit, window_place
from scmp3.app.win_monitors import Monitors, read_monitors, steady
from scmp3.app.window_place import PlaceTracker, WindowPlace


def remembers_place(platform: str = sys.platform, environ: Mapping[str, str] = os.environ) -> bool:
    """Whether the window's place is kept: on Windows, in pywebview's WinForms window."""
    # PYWEBVIEW_GUI=qt makes pywebview use Qt on Windows too.
    return platform == "win32" and environ.get("PYWEBVIEW_GUI", "").lower() != "qt"


class WindowMemory:
    """Where the window opens, from window.json fitted to the monitors, and what it keeps."""

    def __init__(
        self,
        path: Path,
        monitors: Monitors,
        default: tuple[int, int],
        minimum: tuple[int, int],
    ) -> None:
        self._path = path
        # Read once, so the place kept at close is fitted in the same units it opened in.
        self._displays = monitors.displays
        self._scale = monitors.scale
        self._default = default
        self._minimum = minimum
        saved = window_place.load(path)
        self.place: WindowPlace = window_fit.fit(saved, self._displays, default, minimum)
        self._tracker = PlaceTracker(self.place, minimum)

    def attach(self, window: Any) -> None:
        """Follow the window's size, position and state."""
        self._tracker.attach(window)

    def keep(self) -> None:
        """Save where the window was, fitted to the monitors so it's never off every one."""
        place = window_fit.to_keep(
            self._tracker.place(), self._displays, self._default, self._minimum
        )
        window_place.save(self._path, self._steadied(place))

    def _steadied(self, place: WindowPlace) -> WindowPlace:
        """The place moved by a pixel or two, if need be, so it comes back the same next time."""
        x = steady(place.x, self._scale) if place.x is not None else None
        y = steady(place.y, self._scale) if place.y is not None else None
        width, height = (steady(side, self._scale) for side in (place.width, place.height))
        return replace(place, x=x, y=y, width=width, height=height)


def for_this_platform(
    default: tuple[int, int],
    minimum: tuple[int, int],
    monitors: Callable[[], Monitors] = read_monitors,
) -> WindowMemory | None:
    """The window's memory on Windows, or None elsewhere, where the window opens as it always did.

    Neither reading window.json nor the monitors raises, so the app always starts.
    """
    if not remembers_place():
        return None
    return WindowMemory(window_place.default_path(), monitors(), default, minimum)
