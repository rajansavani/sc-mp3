"""The monitors on Windows, read through Win32 in the units pywebview's window uses.

pywebview 6.2.1's WinForms window makes the process system DPI aware as it opens, then reports
and takes its size and place in pixels divided by the window's scale, which for a system-aware
process is the system's. Making the process aware first, as pywebview does, and dividing by the
same scale puts the monitors in those units at any scaling. Read any earlier, a monitor scaled
differently from the main one would come out in other units.
"""

import ctypes
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

from scmp3.app.window_fit import Area, Display

DEFAULT_DPI = 96
MONITORINFOF_PRIMARY = 1
LOGPIXELSX = 88
# How far steady() looks for a value pywebview leaves as it is. At scales in steps of 25%, one
# comes every 4 pixels at most.
STEADY_REACH = 8

log = logging.getLogger(__name__)


class _Rect(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class _MonitorInfo(ctypes.Structure):
    _fields_ = (
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", ctypes.c_ulong),
    )


@dataclass(frozen=True)
class Monitors:
    """The monitors, primary first, and the scale pywebview's window divides pixels by."""

    displays: list[Display] = field(default_factory=list)
    scale: float = 1.0


def in_window_units(left: int, top: int, right: int, bottom: int, scale: float) -> Area:
    """A rectangle in pixels, in the units pywebview's window takes: pixels over the scale."""
    return Area(
        round(left / scale),
        round(top / scale),
        round((right - left) / scale),
        round((bottom - top) / scale),
    )


def round_trip(value: int, scale: float) -> int:
    """A size or position through pywebview 6.2.1's WinForms window: set as int(value * scale)
    pixels, then reported as int(pixels / scale)."""
    return int(int(value * scale) / scale)


def steady(value: int, scale: float) -> int:
    """The value nearest `value` that pywebview's round trip leaves as it is.

    int() rounds toward zero, so at 125% a negative position, on a monitor left of the primary
    one, would move a pixel right at each start until it reached such a value.
    """
    for offset in sorted(range(-STEADY_REACH, STEADY_REACH + 1), key=abs):
        if round_trip(value + offset, scale) == value + offset:
            return value + offset
    return value


def read_monitors() -> Monitors:
    """Every monitor, primary first, or none when they can't be read or this isn't Windows."""
    if sys.platform != "win32":
        return Monitors()
    try:
        return _read(ctypes.windll.user32)
    except (OSError, AttributeError, ValueError):
        log.warning("Couldn't read the monitors, so the window opens at its default place")
        log.debug("Reading the monitors failed", exc_info=True)
        return Monitors()


def _read(user32: Any) -> Monitors:
    # The same call pywebview makes before its window opens, so it changes nothing later.
    user32.SetProcessDPIAware()
    scale = _system_dpi(user32) / DEFAULT_DPI
    found: list[Display] = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_Rect), ctypes.c_void_p
    )

    def collect(monitor: int, _dc: int, _rect: Any, _data: int) -> int:
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
            found.append(_display(info, scale))
        return 1

    user32.EnumDisplayMonitors(None, None, callback_type(collect), None)
    return Monitors(sorted(found, key=lambda display: not display.primary), scale)


def _display(info: _MonitorInfo, scale: float) -> Display:
    whole, work = info.rcMonitor, info.rcWork
    return Display(
        bounds=in_window_units(whole.left, whole.top, whole.right, whole.bottom, scale),
        work=in_window_units(work.left, work.top, work.right, work.bottom, scale),
        primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
    )


def _system_dpi(user32: Any) -> int:
    """The system's DPI, which a system-aware process's windows all use."""
    if hasattr(user32, "GetDpiForSystem"):
        return int(user32.GetDpiForSystem())
    # Windows before 10 version 1607 reads it from the screen's device context.
    screen = user32.GetDC(None)
    try:
        return int(ctypes.windll.gdi32.GetDeviceCaps(screen, LOGPIXELSX))
    finally:
        user32.ReleaseDC(None, screen)
