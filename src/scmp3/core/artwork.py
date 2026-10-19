"""Cover art: turning any source image into the square JPEG cover the tags hold.

`process` follows TECH_SPEC §11, so any source image ends up as a cover Spotify can show.
`cover_sources` finds and fetches the source images.
"""

import io
import logging
import math
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image, ImageCms, ImageOps

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import CropBox

log = logging.getLogger(__name__)

# A cover never needs more, and a small file can declare far more than memory holds.
MAX_SOURCE_PIXELS = 50_000_000
DEFAULT_MAX_SIZE = 1000
# A cover cut from fewer pixels than this looks blurry, so the UI flags it.
SMALL_SOURCE_PX = 500
# Spotify staff say embedded covers must be JPEGs under 4 MB.
MAX_COVER_BYTES = 4_000_000
JPEG_QUALITY = 90

# The only formats Pillow may decode. Others, such as EPS, can run outside programs.
COVER_FORMATS = ("JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF", "AVIF")

# The modes an ICC profile can convert directly. Other modes become RGB first.
_PROFILE_MODES = frozenset({"RGB", "L", "CMYK"})
# Gray with more than 8 bits, as in 16-bit PNGs and some TIFFs.
_WIDE_GRAY_MODES = frozenset({"I", "I;16", "I;16B", "I;16L", "I;16N"})

# Pillow raises all of these for files it can't read or modes it can't convert.
_DECODE_ERRORS = (
    OSError,
    ValueError,
    SyntaxError,
    EOFError,
    RuntimeError,
    NotImplementedError,
    MemoryError,
    Image.DecompressionBombError,
)

CoverWarning = Literal["small_source"]


@dataclass
class Cover:
    """A finished cover: square JPEG bytes, and what the UI shows about where it came from."""

    jpeg: bytes
    width: int
    height: int
    # The source's size after EXIF rotation, as the user sees it.
    source_width: int
    source_height: int
    warnings: list[CoverWarning] = field(default_factory=list)


def process(data: bytes, *, crop: CropBox | None = None, max_size: int = DEFAULT_MAX_SIZE) -> Cover:
    """Turn any image into a square sRGB baseline JPEG, at most `max_size` pixels a side."""
    if max_size < 1:
        raise ScmpError(ErrorKind.internal, f"the cover size must be positive, not {max_size}")
    image = _decode(data)
    box = _square_box(image.width, image.height, crop)
    side = box[2] - box[0]
    # Never upscale: a small source stays its own size.
    out_side = min(side, max_size)
    square = _cut_square(image, box, out_side)
    warnings: list[CoverWarning] = ["small_source"] if side < SMALL_SOURCE_PX else []
    return Cover(
        jpeg=_encode_jpeg(square),
        width=out_side,
        height=out_side,
        source_width=image.width,
        source_height=image.height,
        warnings=warnings,
    )


def _decode(data: bytes) -> Image.Image:
    """Decode the image, apply its EXIF rotation, and convert it to sRGB."""
    try:
        image = Image.open(io.BytesIO(data), formats=COVER_FORMATS)
        _check_pixel_count(image)
        image.load()
        ImageOps.exif_transpose(image, in_place=True)
        return _to_srgb(image)
    except _DECODE_ERRORS as error:
        raise ScmpError(ErrorKind.convert_failed, f"couldn't read the image: {error}") from error


def _check_pixel_count(image: Image.Image) -> None:
    """Refuse an image too big for a cover, before its pixels are decoded."""
    if image.width * image.height <= MAX_SOURCE_PIXELS:
        return
    limit = MAX_SOURCE_PIXELS // 1_000_000
    detail = f"the image is {image.width}x{image.height}, over the {limit}-megapixel limit"
    raise ScmpError(ErrorKind.convert_failed, detail)


def _to_srgb(image: Image.Image) -> Image.Image:
    """An RGB image in sRGB, with any transparency flattened onto white."""
    profile = image.info.get("icc_profile")
    if image.mode in _WIDE_GRAY_MODES:
        # convert() clips these at 255 instead of scaling them, which turns most pixels white.
        image = image.point(lambda value: value * (1 / 256)).convert("L")
    if image.has_transparency_data:
        image = _flatten_onto_white(image)
    elif image.mode not in _PROFILE_MODES:
        image = image.convert("RGB")
    if profile:
        return _apply_profile(image, profile)
    if image.mode == "RGB":
        return image
    return image.convert("RGB")


def _flatten_onto_white(image: Image.Image) -> Image.Image:
    """The image composited onto white, as RGB."""
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    background.alpha_composite(rgba)
    return background.convert("RGB")


def _apply_profile(image: Image.Image, profile: bytes) -> Image.Image:
    """Convert the colors from the image's embedded ICC profile to sRGB."""
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        converted = ImageCms.profileToProfile(
            image, source, ImageCms.createProfile("sRGB"), outputMode="RGB"
        )
    except (ImageCms.PyCMSError, OSError, ValueError, TypeError) as error:
        log.warning("Ignored the image's color profile, which couldn't be used: %s", error)
        return image.convert("RGB")
    if converted is None:
        raise ScmpError(ErrorKind.internal, "the color conversion returned no image")
    return converted


def _square_box(width: int, height: int, crop: CropBox | None) -> tuple[int, int, int, int]:
    """The square to cut, in pixels: the largest one centered in the crop box or the image."""
    if crop is None:
        left, top, right, bottom = 0, 0, width, height
    else:
        left, top, right, bottom = _crop_pixels(width, height, crop)
    side = min(right - left, bottom - top)
    left += (right - left - side) // 2
    top += (bottom - top - side) // 2
    return left, top, left + side, top + side


def _crop_pixels(width: int, height: int, crop: CropBox) -> tuple[int, int, int, int]:
    """The crop box in pixels, kept inside the image."""
    _check_crop(crop)
    left = _clamp(round(crop.left * width), 0, width - 1)
    top = _clamp(round(crop.top * height), 0, height - 1)
    right = _clamp(round((crop.left + crop.width) * width), left + 1, width)
    bottom = _clamp(round((crop.top + crop.height) * height), top + 1, height)
    return left, top, right, bottom


def _check_crop(crop: CropBox) -> None:
    """Refuse a crop box the UI shouldn't send: one with a value that isn't a finite number, no
    size, or no overlap with the image.
    """
    values = (crop.left, crop.top, crop.width, crop.height)
    # A crop computed as 0/0 in JS is NaN, which JSON sends as null.
    if not all(isinstance(value, int | float) and math.isfinite(value) for value in values):
        raise ScmpError(ErrorKind.internal, f"the crop box isn't four numbers: {crop}")
    if crop.width <= 0 or crop.height <= 0:
        raise ScmpError(ErrorKind.internal, f"the crop box is empty: {crop}")
    misses_across = crop.left >= 1 or crop.left + crop.width <= 0
    misses_down = crop.top >= 1 or crop.top + crop.height <= 0
    if misses_across or misses_down:
        raise ScmpError(ErrorKind.internal, f"the crop box is outside the image: {crop}")


def _clamp(value: int, low: int, high: int) -> int:
    """`value`, moved into the range from `low` to `high`."""
    return max(low, min(value, high))


def _cut_square(image: Image.Image, box: tuple[int, int, int, int], out_side: int) -> Image.Image:
    """Crop to the box, downscaling with LANCZOS when it's bigger than `out_side`."""
    if box[2] - box[0] == out_side:
        return image.crop(box)
    return image.resize((out_side, out_side), Image.Resampling.LANCZOS, box=box)


def _encode_jpeg(image: Image.Image) -> bytes:
    """Save as a baseline JPEG holding only the pixels: no EXIF, ICC profile or comment."""
    # crop() and resize() copy the source's info, and Pillow writes a comment from it.
    image.info = {}
    buffer = io.BytesIO()
    try:
        image.save(buffer, "JPEG", quality=JPEG_QUALITY, progressive=False)
    except (OSError, ValueError) as error:
        raise ScmpError(ErrorKind.convert_failed, f"couldn't save the cover: {error}") from error
    jpeg = buffer.getvalue()
    # Even noise stays under 2 MB at the largest size in settings, 1400 px.
    if len(jpeg) >= MAX_COVER_BYTES:
        raise ScmpError(ErrorKind.convert_failed, f"the cover is {len(jpeg)} bytes, over 4 MB")
    return jpeg
