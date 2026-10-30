"""Tests for reading the monitors on Windows in the units pywebview's window uses."""

import sys

import pytest

from scmp3.app import win_monitors
from scmp3.app.window_fit import Area

SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)


def test_a_monitor_at_100_percent_is_in_pixels() -> None:
    assert win_monitors.in_window_units(0, 0, 1920, 1040, 1.0) == Area(0, 0, 1920, 1040)


def test_a_monitor_at_125_percent_is_in_pixels_over_the_scale() -> None:
    assert win_monitors.in_window_units(0, 0, 1920, 1040, 1.25) == Area(0, 0, 1536, 832)


def test_a_monitor_left_of_the_primary_keeps_its_negative_position() -> None:
    area = win_monitors.in_window_units(-2880, 0, 0, 1620, 1.5)
    assert area == Area(-1920, 0, 1920, 1080)


@pytest.mark.parametrize("scale", SCALES)
def test_a_steady_value_comes_back_the_same_at_every_start(scale: float) -> None:
    for value in range(-3000, 3000):
        kept = win_monitors.steady(value, scale)
        assert win_monitors.round_trip(kept, scale) == kept
        assert abs(kept - value) <= 3


def test_a_negative_position_at_125_percent_would_creep_without_steadying() -> None:
    # pywebview's int() rounds toward zero, so -2999 moves right at each start.
    assert win_monitors.round_trip(-2999, 1.25) == -2998
    assert win_monitors.round_trip(-2998, 1.25) == -2997
    assert win_monitors.steady(-2999, 1.25) == -3000


@pytest.mark.skipif(sys.platform != "win32", reason="reads this PC's monitors through Win32")
def test_this_pcs_monitors_are_read_with_the_primary_first() -> None:
    found = win_monitors.read_monitors()
    monitors = found.displays
    assert monitors
    assert found.scale >= 1.0
    assert monitors[0].primary
    assert sum(monitor.primary for monitor in monitors) == 1
    for monitor in monitors:
        assert monitor.work.width <= monitor.bounds.width
        assert monitor.work.height <= monitor.bounds.height


@pytest.mark.skipif(sys.platform == "win32", reason="checks the other platforms")
def test_off_windows_no_monitors_are_read() -> None:
    assert win_monitors.read_monitors() == win_monitors.Monitors()
