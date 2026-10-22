"""Draw the app's logo with Pillow into packaging/icons/: app.ico for Windows and one PNG per size.

Usage: uv run python packaging/make_icons.py
The logo is the one in the UI's top bar: a tangerine rounded square holding a dark arrow that
points down into a tray. Run this again after changing the drawing, and commit what it writes.
"""

import argparse
import math
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PIL import Image, ImageDraw

PACKAGING_DIR = Path(__file__).resolve().parent
ICONS_DIR = PACKAGING_DIR / "icons"
ICO_NAME = "app.ico"

TANGERINE = (0xFF, 0x7A, 0x30, 255)
INK = (0x1B, 0x0E, 0x04, 255)

# The drawing on the UI logo's 28-unit grid, from web/components/TopBar.js. At small sizes each
# stroke moves onto whole pixels: the arrow up and the tray down, so the gap between them stays.
GRID = 28
CORNER_RADIUS = 8
STROKE_WIDTH = 2.2
STROKES: tuple[tuple[tuple[tuple[float, float], ...], Callable[[float], int]], ...] = (
    (((14, 7), (14, 17)), math.floor),
    (((9.5, 12.5), (14, 17), (18.5, 12.5)), math.floor),
    (((8, 20.5), (20, 20.5)), math.ceil),
)
# Transparent space around the square, as a share of the icon, so it sits like other app icons.
MARGIN = 1 / 16
# At 32 px and below a 2.2-unit line blurs, so small sizes get a heavier one.
SMALL_SIZE = 32
SMALL_STROKE_WIDTH = 2.8
# At 24 px and below, strokes are 2 px wide and sit on whole pixels, so they stay sharp.
SNAP_SIZE = 24
SNAPPED_WIDTH = 2

# Windows picks from these for the taskbar, Explorer and title bars at each display scale.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
# The PNGs cover the sizes a macOS iconset needs (T-070) and a large one for the README.
PNG_SIZES = (16, 32, 64, 128, 256, 512, 1024)
# Each size is drawn up to this many times larger, then scaled down, for smooth edges.
SUPERSAMPLE = 8
# The largest canvas drawn on, which keeps the 1024 px icon from needing 268 MB.
MAX_CANVAS = 4096


def on_pixels(x: float, y: float, move_y: Callable[[float], int]) -> tuple[float, float]:
    """A point moved so a 2 px stroke through it covers whole pixels, whose edges are integers."""
    return round(x), move_y(y)


def draw_logo(size: int) -> Image.Image:
    """Draw the logo as a square RGBA image of `size` pixels."""
    scale = max(1, min(SUPERSAMPLE, MAX_CANVAS // size))
    snap = size <= SNAP_SIZE
    # Positions are in the icon's own pixels until they're scaled onto the canvas.
    inset = round(size * MARGIN) if snap else size * MARGIN
    unit = (size - 2 * inset) / GRID
    image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    far = (size - inset) * scale - 1
    square = (inset * scale, inset * scale, far, far)
    draw.rounded_rectangle(square, radius=CORNER_RADIUS * unit * scale, fill=TANGERINE)

    stroke = SMALL_STROKE_WIDTH if size <= SMALL_SIZE else STROKE_WIDTH
    width = (SNAPPED_WIDTH if snap else stroke * unit) * scale
    for grid_points, move_y in STROKES:
        points = [(inset + x * unit, inset + y * unit) for x, y in grid_points]
        if snap:
            points = [on_pixels(x, y, move_y) for x, y in points]
        # Pillow puts whole numbers at pixel centers, so positions move back half a pixel.
        points = [(x * scale - 0.5, y * scale - 0.5) for x, y in points]
        draw.line(points, fill=INK, width=round(width), joint="curve")
        # Pillow's lines have square ends, so round caps are drawn as dots on each end. Its
        # ellipse box takes in its last pixel, hence the half pixel off the radius.
        radius = width / 2 - 0.5
        for x, y in (points[0], points[-1]):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=INK)
    # A box filter keeps strokes on whole pixels exact, and Lanczos smooths the rest.
    resample = Image.Resampling.BOX if snap else Image.Resampling.LANCZOS
    return image.resize((size, size), resample)


def png_name(size: int) -> str:
    return f"icon-{size}.png"


def write_icons(folder: Path) -> list[Path]:
    """Write app.ico and the PNGs into `folder`, and return their paths."""
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for size in PNG_SIZES:
        path = folder / png_name(size)
        draw_logo(size).save(path, optimize=True)
        written.append(path)

    # Pillow takes each size from append_images, so every size is drawn for itself.
    frames = [draw_logo(size) for size in ICO_SIZES]
    ico = folder / ICO_NAME
    largest = frames[-1]
    largest.save(ico, sizes=[(size, size) for size in ICO_SIZES], append_images=frames[:-1])
    written.append(ico)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """Draw the icons and list what was written."""
    parser = argparse.ArgumentParser(description="Draw the app's icons into packaging/icons/.")
    parser.add_argument("--out", type=Path, default=ICONS_DIR, help="default: packaging/icons")
    args = parser.parse_args(argv)
    for path in write_icons(args.out):
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
