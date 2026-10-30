"""Tests for reading the screens through Qt and GTK, and following GTK's frame as it moves."""

from types import SimpleNamespace
from typing import Any

import pytest

from scmp3.app import toolkit_screens
from scmp3.app.toolkit_screens import gtk_displays, qt_displays, watch_gtk_moves
from scmp3.app.window_fit import Area, Display


class QtRect:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self._values = (x, y, width, height)

    def x(self) -> int:
        return self._values[0]

    def y(self) -> int:
        return self._values[1]

    def width(self) -> int:
        return self._values[2]

    def height(self) -> int:
        return self._values[3]


class QtScreen:
    def __init__(self, name: str, whole: QtRect, work: QtRect) -> None:
        self._name, self._whole, self._work = name, whole, work

    def name(self) -> str:
        return self._name

    def geometry(self) -> QtRect:
        return self._whole

    def availableGeometry(self) -> QtRect:
        return self._work


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> "FakeEvent":
        self.handlers.append(handler)
        return self


class GtkNative:
    """A Gtk.Window that keeps its signal handlers and says where its frame is."""

    def __init__(self, position: tuple[int, int]) -> None:
        self.position = position
        self.signals: dict[str, Any] = {}

    def connect(self, signal: str, handler: Any) -> None:
        self.signals[signal] = handler

    def get_position(self) -> tuple[int, int]:
        return self.position


def screen(x: int, y: int, width: int, height: int, frame: Any = None) -> Any:
    """One of pywebview's screens."""
    return SimpleNamespace(x=x, y=y, width=width, height=height, frame=frame)


def gdk_monitor(whole: tuple[int, ...], work: tuple[int, ...], primary: bool) -> Any:
    def rect(values: tuple[int, ...]) -> Any:
        return SimpleNamespace(x=values[0], y=values[1], width=values[2], height=values[3])

    return SimpleNamespace(
        get_geometry=lambda: rect(whole),
        get_workarea=lambda: rect(work),
        is_primary=lambda: primary,
    )


def test_qt_screens_give_their_work_areas_and_the_primary_one() -> None:
    left = QtScreen("LEFT", QtRect(0, 0, 1000, 800), QtRect(0, 0, 1000, 770))
    right = QtScreen("RIGHT", QtRect(1000, 0, 1400, 900), QtRect(1000, 30, 1400, 870))
    screens = [screen(1000, 0, 1400, 900, right), screen(0, 0, 1000, 800, left)]
    assert qt_displays(screens, lambda: "RIGHT") == [
        Display(Area(1000, 0, 1400, 900), Area(1000, 30, 1400, 870), primary=True),
        Display(Area(0, 0, 1000, 800), Area(0, 0, 1000, 770)),
    ]


def test_gtk_monitors_give_their_work_areas_and_the_primary_one() -> None:
    monitors = [
        gdk_monitor((0, 0, 1000, 800), (0, 0, 1000, 800), primary=False),
        gdk_monitor((1000, 0, 1400, 900), (1000, 0, 1400, 868), primary=True),
    ]
    assert gtk_displays([], lambda: monitors) == [
        Display(Area(0, 0, 1000, 800), Area(0, 0, 1000, 800)),
        Display(Area(1000, 0, 1400, 900), Area(1000, 0, 1400, 868), primary=True),
    ]


def test_the_window_is_placed_against_pywebviews_first_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = [Display(Area(1000, 0, 1400, 900), Area(1000, 0, 1400, 900), primary=True)]
    reader = (lambda _screens: displays, toolkit_screens.BACKENDS["qtwebengine"][1])
    monkeypatch.setitem(toolkit_screens.BACKENDS, "qtwebengine", reader)
    first = screen(1000, 0, 1400, 900)
    found = toolkit_screens.toolkit_screens([first, screen(0, 0, 1000, 800)], "qtwebengine")
    assert found is not None
    assert (found.origin.screen, found.origin.x, found.origin.y) == (first, 1000, 0)
    assert found.displays == displays


def test_a_toolkit_without_a_backend_keeps_no_place() -> None:
    assert toolkit_screens.toolkit_screens([screen(0, 0, 1440, 900)], "cef") is None
    assert toolkit_screens.toolkit_screens([screen(0, 0, 1440, 900)], None) is None


def test_with_no_screens_the_toolkit_keeps_no_place() -> None:
    assert toolkit_screens.toolkit_screens([], "gtkwebkit2") is None


def test_qt_and_gtk_leave_room_for_the_frame_and_forget_a_maximize_on_restore() -> None:
    for renderer in ("qtwebengine", "gtkwebkit2"):
        backend = toolkit_screens.BACKENDS[renderer][1]
        assert backend.frame == toolkit_screens.FRAME_ROOM
        assert backend.reports_after_restore is False


def test_gtk_reports_the_frames_position_rather_than_the_client_areas() -> None:
    native = GtkNative((100, 120))
    window = SimpleNamespace(native=native, events=SimpleNamespace(before_show=FakeEvent()))
    reported: list[tuple[float, float]] = []
    watch_gtk_moves(window, lambda x, y: reported.append((x, y)))
    # The handler is connected only once pywebview has made the GTK window.
    assert native.signals == {}
    window.events.before_show.handlers[0]()
    # The event says where the client area is, a 1 px border and 20 px title bar further in.
    keep_going = native.signals["configure-event"](native, SimpleNamespace(x=101, y=140))
    assert reported == [(100, 120)]
    assert keep_going is False


def test_a_screen_reading_failure_opens_the_window_without_a_place(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> tuple[list[Any], str]:
        raise RuntimeError("cannot open display")

    assert toolkit_screens.read_toolkit_screens(broken) is None
    assert "Couldn't read the screens" in caplog.text
