"""The screens on Linux, read through the toolkit pywebview picked, and how its window is placed.

pywebview 6.2.1's Qt and GTK windows add a screen's origin to the x and y they're given, and
report moves in desktop coordinates. Both take and report the client area's size, while x and
y place the frame. GTK's moved event gives the client area's position, so there the frame's
position is read from GTK itself. Screens and work areas come in the same desktop coordinates.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from scmp3.app.window_fit import Area, Display
from scmp3.app.window_place import Backend, ReportMove, ScreenOrigin

# Room for the frame around the client area: side borders, and a title bar up to about 40 px.
FRAME_ROOM = (8, 40)

log = logging.getLogger(__name__)

# Reads the monitors from a toolkit, given pywebview's screens for it.
ReadDisplays = Callable[[Sequence[Any]], list[Display]]


@dataclass(frozen=True)
class ToolkitScreens:
    """The monitors in desktop coordinates, the screen the window is placed against, and how
    the toolkit's window takes and reports its place."""

    displays: list[Display]
    origin: ScreenOrigin
    backend: Backend


def _rect_area(x: int, y: int, width: int, height: int) -> Area:
    return Area(int(x), int(y), int(width), int(height))


def qt_displays(screens: Sequence[Any]) -> list[Display]:
    """The monitors from Qt, whose screens pywebview hands over as QScreen objects."""
    from qtpy.QtGui import QGuiApplication

    primary = QGuiApplication.primaryScreen()
    primary_name = primary.name() if primary is not None else None
    displays = []
    for screen in screens:
        whole, work = screen.frame.geometry(), screen.frame.availableGeometry()
        displays.append(
            Display(
                bounds=_rect_area(whole.x(), whole.y(), whole.width(), whole.height()),
                work=_rect_area(work.x(), work.y(), work.width(), work.height()),
                primary=screen.frame.name() == primary_name,
            )
        )
    return displays


def gtk_displays(_screens: Sequence[Any]) -> list[Display]:
    """The monitors from GDK, in the order pywebview lists them."""
    # pywebview's GTK backend has already picked GDK 3.
    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    displays = []
    for index in range(display.get_n_monitors()):
        monitor = display.get_monitor(index)
        whole, work = monitor.get_geometry(), monitor.get_workarea()
        displays.append(
            Display(
                bounds=_rect_area(whole.x, whole.y, whole.width, whole.height),
                work=_rect_area(work.x, work.y, work.width, work.height),
                primary=monitor.is_primary(),
            )
        )
    return displays


def watch_gtk_moves(window: Any, report: ReportMove) -> None:
    """Report the frame's position from GTK each time the window moves or changes size."""

    def on_configure(native: Any, _event: Any) -> bool:
        # get_position gives the frame's position, which Gtk.Window.move takes.
        x, y = native.get_position()
        report(x, y)
        # False lets pywebview's own handler run as well.
        return False

    def connect() -> None:
        window.native.connect("configure-event", on_configure)

    # pywebview sends before_show on GTK's thread, once the native window exists.
    window.events.before_show += connect


# How each of pywebview's renderers reads the monitors and places its window. A toolkit not here
# opens its window as it always did.
BACKENDS: dict[str, tuple[ReadDisplays, Backend]] = {
    "qtwebengine": (qt_displays, Backend(frame=FRAME_ROOM, sizes_after_restore=False)),
    "gtkwebkit2": (
        gtk_displays,
        Backend(frame=FRAME_ROOM, watch_moves=watch_gtk_moves, sizes_after_restore=False),
    ),
}


def toolkit_screens(screens: Sequence[Any], renderer: str | None) -> ToolkitScreens | None:
    """The monitors and placing for a renderer, or None for one this doesn't know."""
    found = BACKENDS.get(renderer) if renderer else None
    if found is None or not screens:
        return None
    read_displays, backend = found
    displays = read_displays(screens)
    if not displays:
        return None
    # pywebview's Qt and GTK windows are placed against the first screen when given no other.
    first = screens[0]
    return ToolkitScreens(displays, ScreenOrigin(first, int(first.x), int(first.y)), backend)


def read_toolkit_screens() -> ToolkitScreens | None:
    """The monitors from pywebview's toolkit, or None when they can't be read. It never raises."""
    try:
        # Imported here, since the app imports pywebview only once its logging is set up.
        import webview

        # Reading the screens picks the toolkit, which sets webview.renderer.
        screens = webview.screens
        return toolkit_screens(screens, webview.renderer)
    # A toolkit can fail in many ways, and the window still opens without its place.
    except Exception:
        log.warning("Couldn't read the screens, so the window opens at its default place")
        log.debug("Reading the screens failed", exc_info=True)
        return None
