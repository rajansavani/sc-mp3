"""Tests for packaging/make_icons.py, which draws the logo into app.ico and PNGs."""

import importlib.util
import itertools
import sys
from pathlib import Path
from types import ModuleType

import pytest
from PIL import IcoImagePlugin, Image, ImageChops

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "make_icons.py"
# Pillow updates can move an edge pixel by a step or two without changing the drawing.
TOLERANCE = 2


def load_script() -> ModuleType:
    """Import the script by its path, since packaging/ isn't a package."""
    spec = importlib.util.spec_from_file_location("scmp3_packaging_make_icons", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make_icons = load_script()


def differs(first: Image.Image, second: Image.Image) -> int:
    """The largest difference in any channel of any pixel."""
    assert first.size == second.size
    bands = ImageChops.difference(first.convert("RGBA"), second.convert("RGBA")).getextrema()
    return max(high for _, high in bands)


@pytest.fixture(scope="module")
def drawn(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("icons")
    make_icons.write_icons(folder)
    return folder


def test_it_writes_the_ico_and_a_png_per_size(drawn: Path) -> None:
    expected = {make_icons.ICO_NAME, *(make_icons.png_name(size) for size in make_icons.PNG_SIZES)}
    assert {path.name for path in drawn.iterdir()} == expected
    for size in make_icons.PNG_SIZES:
        with Image.open(drawn / make_icons.png_name(size)) as image:
            assert image.size == (size, size)
            assert image.mode == "RGBA"


def test_the_ico_holds_every_size_windows_picks_from(drawn: Path) -> None:
    with Image.open(drawn / make_icons.ICO_NAME) as ico:
        assert sorted(ico.info["sizes"]) == [(size, size) for size in make_icons.ICO_SIZES]


@pytest.mark.parametrize("size", [16, 32, 256])
def test_each_ico_size_is_drawn_for_itself_rather_than_scaled(drawn: Path, size: int) -> None:
    with (drawn / make_icons.ICO_NAME).open("rb") as file:
        frame = IcoImagePlugin.IcoFile(file).getimage((size, size))
        assert differs(frame, make_icons.draw_logo(size)) == 0


def test_the_logo_is_a_tangerine_square_holding_a_dark_arrow() -> None:
    logo = make_icons.draw_logo(256)
    inset = 256 * make_icons.MARGIN
    unit = (256 - 2 * inset) / make_icons.GRID

    def at(x: float, y: float) -> tuple[int, ...]:
        return logo.getpixel((round(inset + x * unit), round(inset + y * unit)))

    assert logo.getpixel((0, 0))[3] == 0
    assert at(4, 14) == make_icons.TANGERINE
    # The arrow's shaft, its point and the tray under it.
    assert at(14, 10) == make_icons.INK
    assert at(14, 16.5) == make_icons.INK
    assert at(14, 20.5) == make_icons.INK
    # The gap between the arrow and the tray.
    assert at(14, 18.75) == make_icons.TANGERINE


@pytest.mark.parametrize("size", [16, 20, 24])
def test_small_icons_keep_the_shaft_and_tray_on_whole_pixels(size: int) -> None:
    logo = make_icons.draw_logo(size)
    middle = size // 2
    rows = [[logo.getpixel((x, y)) for x in range(middle - 2, middle + 2)] for y in range(size)]
    # A sharp 2 px shaft: two columns of ink with no half-tone beside them.
    sharp = [make_icons.TANGERINE, make_icons.INK, make_icons.INK, make_icons.TANGERINE]
    assert sharp in rows
    # Down the middle, counting only exact colors: the arrow, a gap, then the tray.
    column = [row[1] for row in rows if row[1] in (make_icons.INK, make_icons.TANGERINE)]
    runs = [color for color, _ in itertools.groupby(column)]
    assert runs == [make_icons.TANGERINE, make_icons.INK] * 2 + [make_icons.TANGERINE]


def test_the_committed_icons_match_the_drawing() -> None:
    icons = make_icons.ICONS_DIR
    for size in make_icons.PNG_SIZES:
        with Image.open(icons / make_icons.png_name(size)) as committed:
            assert differs(committed, make_icons.draw_logo(size)) <= TOLERANCE, size
    with Image.open(icons / make_icons.ICO_NAME) as ico:
        assert sorted(ico.info["sizes"]) == [(size, size) for size in make_icons.ICO_SIZES]
