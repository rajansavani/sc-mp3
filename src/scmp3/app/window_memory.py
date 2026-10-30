"""Keeping the main window's size and place between runs, on Windows and Linux.

Windows reads its monitors through Win32 (win_monitors.py), and Linux through the Qt or GTK
toolkit pywebview picked (toolkit_screens.py). On macOS, and on Windows with PYWEBVIEW_GUI=qt,
the window opens as it always did and window.json is neither read nor written.
"""

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from scmp3.app import window_fit, window_place
from scmp3.app.toolkit_screens import ToolkitScreens, read_toolkit_screens
from scmp3.app.win_monitors import Monitors, read_monitors, steady
from scmp3.app.window_place import WINFORMS, Backend, PlaceTracker, ScreenOrigin, WindowPlace


def remembers_place(platform: str = sys.platform, environ: Mapping[str, str] = os.environ) -> bool:
    """Whether the window's place is kept: on Windows, in pywebview's WinForms window."""
    # PYWEBVIEW_GUI=qt makes pywebview use Qt on Windows too.
    return platform == "win32" and environ.get("PYWEBVIEW_GUI", "").lower() != "qt"


def remembers_toolkit_place(platform: str = sys.platform) -> bool:
    """Whether the window's place is kept through pywebview's toolkit: on Linux, with Qt or GTK."""
    return platform.startswith("linux")


class WindowMemory:
    """Where the window opens, from window.json fitted to the monitors, and what it keeps."""

    def __init__(
        self,
        path: Path,
        monitors: Monitors,
        default: tuple[int, int],
        minimum: tuple[int, int],
        *,
        backend: Backend = WINFORMS,
        origin: ScreenOrigin | None = None,
    ) -> None:
        self._path = path
        # Read once, so the place kept at close is fitted in the same units it opened in.
        self._displays = monitors.displays
        self._scale = monitors.scale
        self._default = default
        self._minimum = minimum
        self._backend = backend
        # The screen Qt and GTK place the window against, or None on Windows.
        self.origin = origin
        saved = window_place.load(path)
        self.place: WindowPlace = window_fit.fit(
            saved, self._displays, default, minimum, backend.frame
        )
        self._tracker = PlaceTracker(
            self.place, minimum, sizes_after_restore=backend.sizes_after_restore
        )

    def attach(self, window: Any) -> None:
        """Follow the window's size, position and state."""
        watch_moves = self._backend.watch_moves
        self._tracker.attach(window, moves=watch_moves is None)
        if watch_moves is not None:
            watch_moves(window, self._tracker.moved)

    def keep(self) -> None:
        """Save where the window was, fitted to the monitors so it's never off every one."""
        place = window_fit.to_keep(
            self._tracker.place(), self._displays, self._default, self._minimum, self._backend.frame
        )
        window_place.save(self._path, self._steadied(place))

    def _steadied(self, place: WindowPlace) -> WindowPlace:
        """The place moved by a pixel or two, if need be, so it comes back the same next time."""
        x = steady(place.x, self._scale) if place.x is not None else None
        y = steady(place.y, self._scale) if place.y is not None else None
        width, height = (steady(side, self._scale) for side in (place.width, place.height))
        return replace(place, x=x, y=y, width=width, height=height)


def for_toolkit(
    path: Path, found: ToolkitScreens, default: tuple[int, int], minimum: tuple[int, int]
) -> WindowMemory:
    """The window's memory for Qt or GTK, whose units need no scale."""
    return WindowMemory(
        path, Monitors(found.displays), default, minimum, backend=found.backend, origin=found.origin
    )


def for_this_platform(
    default: tuple[int, int],
    minimum: tuple[int, int],
    monitors: Callable[[], Monitors] = read_monitors,
    toolkit_screens: Callable[[], ToolkitScreens | None] = read_toolkit_screens,
) -> WindowMemory | None:
    """The window's memory, or None where the window opens as it always did.

    Neither reading window.json nor the monitors raises, so the app always starts.
    """
    if remembers_place():
        return WindowMemory(window_place.default_path(), monitors(), default, minimum)
    if not remembers_toolkit_place():
        return None
    found = toolkit_screens()
    if found is None:
        return None
    return for_toolkit(window_place.default_path(), found, default, minimum)
