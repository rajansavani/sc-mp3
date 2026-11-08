"""Fitting the window's saved place to the monitors there are now, before the window opens.

A saved place is used only when the window lies mostly inside one monitor's work area, the part
the taskbar leaves free. It's then shrunk to fit that area and moved fully into it. Otherwise
the window opens at the default size in the middle of the primary monitor. `frame` is the room
the window's frame takes outside its size, on toolkits whose size is the client area's.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from scmp3.app.window_place import WindowPlace

# How much of the window must lie inside one screen's work area for its saved place to be used.
MOSTLY_INSIDE = 0.5


@dataclass(frozen=True)
class Area:
    """A rectangle in the units the window's place uses."""

    x: int
    y: int
    width: int
    height: int

    def holds(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def overlap(self, x: int, y: int, width: int, height: int) -> int:
        """How many pixels of a rectangle lie inside this one."""
        across = min(x + width, self.x + self.width) - max(x, self.x)
        down = min(y + height, self.y + self.height) - max(y, self.y)
        return max(0, across) * max(0, down)


@dataclass(frozen=True)
class Display:
    """A monitor: all of it, its work area, and whether it's the primary one."""

    bounds: Area
    work: Area
    primary: bool = False


def primary(displays: Sequence[Display]) -> Display:
    """The monitor the toolkit marks as primary, or else the one at (0, 0), or else the first."""
    marked = [display for display in displays if display.primary]
    at_origin = [display for display in displays if display.bounds.holds(0, 0)]
    return (marked or at_origin or list(displays))[0]


def fit(
    place: WindowPlace | None,
    displays: Sequence[Display],
    default: tuple[int, int],
    minimum: tuple[int, int],
    frame: tuple[int, int] = (0, 0),
) -> WindowPlace:
    """Where the window opens: its saved place fitted to a screen, or the default place.

    A maximized window keeps that, so it opens maximized on the screen its place is on.
    """
    maximized = place.maximized if place else False
    if not displays:
        # With no screens to check against, a position could be off every one.
        return WindowPlace(*default, maximized=maximized)
    area = _area_mostly_holding(place, displays, minimum, frame)
    if place is not None and area is not None:
        return _moved_inside(place, area, minimum, frame)
    work = primary(displays).work
    return _centered(WindowPlace(*default, maximized=maximized), work, minimum, frame)


def to_keep(
    place: WindowPlace,
    displays: Sequence[Display],
    default: tuple[int, int],
    minimum: tuple[int, int],
    frame: tuple[int, int] = (0, 0),
) -> WindowPlace:
    """The place to save as the window closes, fitted the same way, so the file never holds a
    place off every screen. With no screens to check, it's kept as it is, to be checked next time.
    """
    return fit(place, displays, default, minimum, frame) if displays else place


def _area_mostly_holding(
    place: WindowPlace | None,
    displays: Sequence[Display],
    minimum: tuple[int, int],
    frame: tuple[int, int],
) -> Area | None:
    """The work area that holds the largest share of the window, once the window is shrunk to
    it, when that share is at least half."""
    if place is None or place.x is None or place.y is None:
        return None
    best, best_share = None, 0.0
    for display in displays:
        work = display.work
        width, height = _outer(_fitted_size(place, work, minimum, frame), frame)
        share = work.overlap(place.x, place.y, width, height) / (width * height)
        if share > best_share:
            best, best_share = work, share
    return best if best_share >= MOSTLY_INSIDE else None


def _fitted_size(
    place: WindowPlace, area: Area, minimum: tuple[int, int], frame: tuple[int, int]
) -> tuple[int, int]:
    """The size shrunk to the area, frame included, but never under the window's minimum."""
    return (
        max(minimum[0], min(place.width, area.width - frame[0])),
        max(minimum[1], min(place.height, area.height - frame[1])),
    )


def _outer(size: tuple[int, int], frame: tuple[int, int]) -> tuple[int, int]:
    """The size with the frame around it."""
    return size[0] + frame[0], size[1] + frame[1]


def _moved_inside(
    place: WindowPlace, area: Area, minimum: tuple[int, int], frame: tuple[int, int]
) -> WindowPlace:
    """The place shrunk to the area and moved the least it takes to lie fully inside it."""
    assert place.x is not None and place.y is not None
    width, height = _fitted_size(place, area, minimum, frame)
    outer_width, outer_height = _outer((width, height), frame)
    # A window wider than the area starts at its left edge, so its title bar shows.
    x = max(area.x, min(place.x, area.x + area.width - outer_width))
    y = max(area.y, min(place.y, area.y + area.height - outer_height))
    return replace(place, width=width, height=height, x=x, y=y)


def _centered(
    place: WindowPlace, area: Area, minimum: tuple[int, int], frame: tuple[int, int]
) -> WindowPlace:
    width, height = _fitted_size(place, area, minimum, frame)
    outer_width, outer_height = _outer((width, height), frame)
    x = area.x + max(0, (area.width - outer_width) // 2)
    y = area.y + max(0, (area.height - outer_height) // 2)
    return replace(place, width=width, height=height, x=x, y=y)
