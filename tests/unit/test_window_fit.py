"""Tests for fitting the window's saved place to the screens there are now."""

from scmp3.app.window_fit import Area, Display, fit, primary, to_keep
from scmp3.app.window_place import WindowPlace

MINIMUM = (980, 640)
DEFAULT = (1280, 860)
# A 1920 x 1080 primary screen with a 40 px taskbar at the bottom, and a second one on its right.
LAPTOP = Display(Area(0, 0, 1920, 1080), Area(0, 0, 1920, 1040))
MONITOR = Display(Area(1920, 0, 2560, 1440), Area(1920, 0, 2560, 1400))
SMALL_LAPTOP = Display(Area(0, 0, 1366, 768), Area(0, 0, 1366, 728))


def fitted(place: WindowPlace | None, *displays: Display) -> WindowPlace:
    return fit(place, displays, DEFAULT, MINIMUM)


def test_a_place_on_a_screen_opens_where_it_was() -> None:
    place = WindowPlace(width=1100, height=720, x=150, y=120)
    assert fitted(place, LAPTOP, MONITOR) == place


def test_a_place_on_the_second_screen_opens_there() -> None:
    place = WindowPlace(width=1400, height=900, x=2200, y=100)
    assert fitted(place, LAPTOP, MONITOR) == place


def test_a_maximized_window_from_a_screen_thats_gone_opens_maximized_on_the_primary() -> None:
    # Maximized on a second monitor that has since been unplugged (review F1).
    place = WindowPlace(width=1100, height=720, x=9000, y=120, maximized=True)
    assert fitted(place, LAPTOP) == WindowPlace(1280, 860, x=320, y=90, maximized=True)


def test_a_place_off_every_screen_opens_at_the_default_place() -> None:
    place = WindowPlace(width=1100, height=720, x=9000, y=120)
    assert fitted(place, LAPTOP) == WindowPlace(1280, 860, x=320, y=90)


def test_a_place_larger_than_its_screen_is_shrunk_and_moved_onto_it() -> None:
    # Saved on a big monitor, opened on a smaller screen (review F2).
    place = WindowPlace(width=2400, height=1300, x=300, y=100)
    assert fitted(place, LAPTOP) == WindowPlace(1920, 1040, x=0, y=0)


def test_on_a_small_laptop_the_whole_window_fits_above_the_taskbar() -> None:
    place = WindowPlace(width=2000, height=1300, x=300, y=100)
    assert fitted(place, SMALL_LAPTOP) == WindowPlace(1366, 728, x=0, y=0)


def test_a_window_mostly_on_a_screen_is_moved_fully_onto_it() -> None:
    place = WindowPlace(width=1100, height=720, x=1100, y=400)
    assert fitted(place, LAPTOP) == WindowPlace(1100, 720, x=820, y=320)


def test_a_window_mostly_off_its_screen_opens_at_the_default_place() -> None:
    place = WindowPlace(width=1100, height=720, x=1500, y=500)
    assert fitted(place, LAPTOP) == WindowPlace(1280, 860, x=320, y=90)


def test_a_window_across_two_screens_goes_onto_the_one_with_more_of_it() -> None:
    place = WindowPlace(width=1100, height=720, x=1700, y=100)
    assert fitted(place, LAPTOP, MONITOR) == WindowPlace(1100, 720, x=1920, y=100)


def test_a_work_area_under_the_minimum_gets_the_minimum_at_its_corner() -> None:
    tiny = Display(Area(0, 0, 800, 600), Area(0, 0, 800, 560))
    place = WindowPlace(width=1100, height=720, x=0, y=0)
    assert fitted(place, tiny) == WindowPlace(980, 640, x=0, y=0)


def test_a_taskbar_at_the_top_keeps_the_window_below_it() -> None:
    top_bar = Display(Area(0, 0, 1920, 1080), Area(0, 40, 1920, 1040))
    place = WindowPlace(width=1100, height=720, x=100, y=10)
    assert fitted(place, top_bar).y == 40


def test_the_first_run_opens_at_the_default_size_in_the_middle_of_the_primary_screen() -> None:
    assert fitted(None, MONITOR, LAPTOP) == WindowPlace(1280, 860, x=320, y=90)


def test_the_default_size_shrinks_to_a_small_screen() -> None:
    assert fitted(None, SMALL_LAPTOP) == WindowPlace(1280, 728, x=43, y=0)


def test_with_no_screens_known_the_window_opens_at_the_default_size_unplaced() -> None:
    place = WindowPlace(width=1100, height=720, x=9000, y=120, maximized=True)
    assert fitted(place) == WindowPlace(1280, 860, maximized=True)


def test_the_primary_screen_is_the_one_windows_marks() -> None:
    # A primary monitor on the right of a second one, as a laptop with a monitor on its left.
    left = Display(Area(-1000, 0, 1000, 800), Area(-1000, 0, 1000, 760))
    marked = Display(Area(0, 0, 1400, 900), Area(0, 0, 1400, 860), primary=True)
    assert primary([left, marked]) == marked


def test_without_a_mark_the_primary_screen_is_the_one_at_the_origin() -> None:
    assert primary([MONITOR, LAPTOP]) == LAPTOP
    assert primary([MONITOR]) == MONITOR


def test_the_place_kept_at_close_is_never_off_every_screen() -> None:
    place = WindowPlace(width=1100, height=720, x=9000, y=120, maximized=True)
    kept = to_keep(place, [LAPTOP], DEFAULT, MINIMUM)
    assert kept == WindowPlace(1280, 860, x=320, y=90, maximized=True)


def test_with_no_screens_known_the_place_is_kept_as_it_is() -> None:
    place = WindowPlace(width=1100, height=720, x=9000, y=120)
    assert to_keep(place, [], DEFAULT, MINIMUM) == place
