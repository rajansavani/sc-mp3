"""Tests for making square JPEG covers, from images generated with Pillow here."""

import io
import logging
import math
import os
from typing import Any

import pytest
from PIL import EpsImagePlugin, Image, ImageCms, ImageFile

from scmp3.core import artwork
from scmp3.core.artwork import Cover
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import CropBox

RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
WHITE = (255, 255, 255)

# JPEG markers: a baseline frame, a progressive frame, EXIF data, an ICC profile and a comment.
SOF0, SOF2, APP1, APP2, COM, SOS = 0xC0, 0xC2, 0xE1, 0xE2, 0xFE, 0xDA


def encode(image: Image.Image, image_format: str, **options: Any) -> bytes:
    """The image saved in memory in `image_format`."""
    buffer = io.BytesIO()
    image.save(buffer, image_format, **options)
    return buffer.getvalue()


def decoded(cover: Cover) -> Image.Image:
    """The cover's JPEG, decoded."""
    return Image.open(io.BytesIO(cover.jpeg))


def assert_near(pixel: tuple[int, ...], expected: tuple[int, ...], tolerance: int = 12) -> None:
    """The pixel matches the color within `tolerance`, allowing for JPEG's small errors."""
    assert all(abs(a - b) <= tolerance for a, b in zip(pixel, expected, strict=True)), pixel


def header_markers(jpeg: bytes) -> list[int]:
    """The marker codes of a JPEG's header segments, up to the start of the image data."""
    markers = []
    position = 2  # after the start-of-image marker
    while position < len(jpeg):
        marker = jpeg[position + 1]
        markers.append(marker)
        if marker == SOS:
            break
        length = int.from_bytes(jpeg[position + 2 : position + 4], "big")
        position += 2 + length
    return markers


def noise(side: int) -> bytes:
    """A square of random pixels as BMP, the worst case for JPEG size."""
    image = Image.frombytes("RGB", (side, side), os.urandom(side * side * 3))
    return encode(image, "BMP")


def red_blue_swapped_profile() -> bytes:
    """Pillow's sRGB profile with its red and blue primaries swapped, so red pixels turn blue."""
    profile = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    tag_count = int.from_bytes(profile[128:132], "big")
    swaps = {b"rXYZ": b"bXYZ", b"bXYZ": b"rXYZ"}
    for index in range(tag_count):
        at = 132 + 12 * index
        signature = bytes(profile[at : at + 4])
        profile[at : at + 4] = swaps.get(signature, signature)
    return bytes(profile)


# process: sizes, cropping and warnings


def test_a_wide_image_is_cropped_to_its_center_square_without_upscaling() -> None:
    image = Image.new("RGB", (1280, 720), BLUE)
    image.paste(GREEN, (280, 0, 1000, 720))
    cover = artwork.process(encode(image, "PNG"))
    assert (cover.width, cover.height) == (720, 720)
    assert (cover.source_width, cover.source_height) == (1280, 720)
    out = decoded(cover)
    assert out.size == (720, 720)
    for x in (4, 360, 715):
        assert_near(out.getpixel((x, 360)), GREEN)
    assert cover.warnings == []


def test_a_small_image_keeps_its_size_and_gets_the_small_source_warning() -> None:
    cover = artwork.process(encode(Image.new("RGB", (300, 300), RED), "PNG"))
    assert (cover.width, cover.height) == (300, 300)
    assert decoded(cover).size == (300, 300)
    assert cover.warnings == ["small_source"]


def test_a_500_px_image_gets_no_warning() -> None:
    cover = artwork.process(encode(Image.new("RGB", (500, 500), RED), "PNG"))
    assert cover.warnings == []


def test_a_huge_image_is_downscaled_to_the_max_size() -> None:
    data = encode(Image.new("RGB", (3000, 3000), RED), "PNG")
    cover = artwork.process(data)
    assert (cover.width, cover.height) == (1000, 1000)
    assert decoded(cover).size == (1000, 1000)
    assert (cover.source_width, cover.source_height) == (3000, 3000)
    assert_near(decoded(cover).getpixel((500, 500)), RED)
    assert artwork.process(data, max_size=640).width == 640


def test_a_crop_box_picks_the_region_to_keep() -> None:
    image = Image.new("RGB", (800, 800), RED)
    image.paste(GREEN, (400, 0, 800, 400))
    crop = CropBox(left=0.5, top=0.0, width=0.5, height=0.5)
    cover = artwork.process(encode(image, "PNG"), crop=crop)
    assert (cover.width, cover.height) == (400, 400)
    out = decoded(cover)
    for point in ((3, 3), (200, 200), (396, 396)):
        assert_near(out.getpixel(point), GREEN)
    # The cover is built from 400 px of the source, so it's flagged though the source is 800.
    assert cover.warnings == ["small_source"]


def test_a_crop_box_that_isnt_square_gets_the_square_centered_in_it() -> None:
    image = Image.new("RGB", (1000, 1000), RED)
    image.paste(BLUE, (500, 0, 1000, 1000))
    crop = CropBox(left=0.0, top=0.0, width=1.0, height=0.5)
    cover = artwork.process(encode(image, "PNG"), crop=crop)
    assert (cover.width, cover.height) == (500, 500)
    out = decoded(cover)
    assert_near(out.getpixel((60, 250)), RED)
    assert_near(out.getpixel((440, 250)), BLUE)


def test_a_crop_box_past_the_edge_is_kept_inside_the_image() -> None:
    image = Image.new("RGB", (1000, 1000), RED)
    crop = CropBox(left=0.8, top=0.8, width=0.5, height=0.5)
    cover = artwork.process(encode(image, "PNG"), crop=crop)
    assert (cover.width, cover.height) == (200, 200)


@pytest.mark.parametrize(
    "crop",
    [
        CropBox(left=0.1, top=0.1, width=0.0, height=0.5),
        CropBox(left=math.nan, top=0.0, width=0.5, height=0.5),
        CropBox(left=0.0, top=0.0, width=math.inf, height=0.5),
        # JSON sends a NaN from JS as null.
        CropBox.from_dict({"left": None, "top": 0.0, "width": 0.5, "height": 0.5}),
        CropBox(left=-5.0, top=0.0, width=0.5, height=0.5),
        CropBox(left=0.0, top=1.0, width=0.5, height=0.5),
    ],
    ids=["empty", "nan", "infinite", "null", "left of the image", "below the image"],
)
def test_a_crop_box_the_ui_shouldnt_send_is_refused(crop: CropBox) -> None:
    data = encode(Image.new("RGB", (100, 100), RED), "PNG")
    with pytest.raises(ScmpError) as caught:
        artwork.process(data, crop=crop)
    assert caught.value.kind == ErrorKind.internal


def test_a_crop_box_partly_outside_the_image_keeps_the_part_inside() -> None:
    image = Image.new("RGB", (1000, 1000), RED)
    crop = CropBox(left=-0.2, top=-0.2, width=0.5, height=0.5)
    cover = artwork.process(encode(image, "PNG"), crop=crop)
    assert (cover.width, cover.height) == (300, 300)


def test_a_max_size_below_one_is_refused() -> None:
    data = encode(Image.new("RGB", (100, 100), RED), "PNG")
    with pytest.raises(ScmpError) as caught:
        artwork.process(data, max_size=0)
    assert caught.value.kind == ErrorKind.internal


# process: color, transparency and orientation


def test_transparency_is_flattened_onto_white() -> None:
    image = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (200, 200, 400, 400))
    image.paste((0, 0, 255, 128), (0, 500, 600, 600))
    cover = artwork.process(encode(image, "PNG"))
    out = decoded(cover)
    assert out.mode == "RGB"
    assert_near(out.getpixel((20, 20)), WHITE)
    assert_near(out.getpixel((300, 300)), RED)
    # Half-transparent blue over white is a light blue.
    assert_near(out.getpixel((300, 560)), (127, 127, 255))


TRANSPARENCY_KINDS = {
    "palette GIF": lambda image: encode(image, "GIF"),
    "palette PNG": lambda image: encode(image.quantize(), "PNG"),
    "gray and alpha PNG": lambda image: encode(image.convert("LA"), "PNG"),
}


@pytest.mark.parametrize("kind", TRANSPARENCY_KINDS)
def test_other_kinds_of_transparency_are_flattened_onto_white(kind: str) -> None:
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (40, 40, 60, 60))
    data = TRANSPARENCY_KINDS[kind](image)
    assert Image.open(io.BytesIO(data)).has_transparency_data
    cover = artwork.process(data)
    assert_near(decoded(cover).getpixel((5, 5)), WHITE)


def test_a_cmyk_jpeg_becomes_rgb() -> None:
    image = Image.new("CMYK", (600, 600), (0, 255, 255, 0))
    cover = artwork.process(encode(image, "JPEG", quality=95))
    out = decoded(cover)
    assert out.mode == "RGB"
    assert_near(out.getpixel((300, 300)), RED, tolerance=20)


def test_a_grayscale_image_becomes_rgb() -> None:
    cover = artwork.process(encode(Image.new("L", (100, 100), 128), "PNG"))
    out = decoded(cover)
    assert out.mode == "RGB"
    assert_near(out.getpixel((50, 50)), (128, 128, 128))


@pytest.mark.parametrize(
    ("mode", "image_format"), [("I;16", "PNG"), ("I", "TIFF")], ids=["16-bit PNG", "I TIFF"]
)
def test_wide_grayscale_is_scaled_rather_than_clipped(mode: str, image_format: str) -> None:
    data = encode(Image.new(mode, (100, 100), 32768), image_format)
    assert Image.open(io.BytesIO(data)).mode == mode
    cover = artwork.process(data)
    assert_near(decoded(cover).getpixel((50, 50)), (128, 128, 128))


def test_exif_rotation_is_applied_before_cropping() -> None:
    # Stored landscape, with red on the left. EXIF orientation 6 means "turn it 90° clockwise",
    # so it's shown as a portrait with red on top.
    image = Image.new("RGB", (600, 300), RED)
    image.paste(BLUE, (300, 0, 600, 300))
    exif = Image.Exif()
    exif[0x0112] = 6
    cover = artwork.process(encode(image, "JPEG", exif=exif, quality=95))
    assert (cover.source_width, cover.source_height) == (300, 600)
    out = decoded(cover)
    assert_near(out.getpixel((150, 40)), RED)
    assert_near(out.getpixel((150, 260)), BLUE)
    assert len(out.getexif()) == 0


def test_an_embedded_color_profile_is_converted_to_srgb() -> None:
    image = Image.new("RGB", (100, 100), RED)
    cover = artwork.process(encode(image, "PNG", icc_profile=red_blue_swapped_profile()))
    assert_near(decoded(cover).getpixel((50, 50)), BLUE)


def test_a_rotated_photo_keeps_its_color_profile() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6
    profile = red_blue_swapped_profile()
    data = encode(Image.new("RGB", (600, 300), RED), "JPEG", exif=exif, icc_profile=profile)
    cover = artwork.process(data)
    assert (cover.source_width, cover.source_height) == (300, 600)
    assert_near(decoded(cover).getpixel((150, 150)), BLUE)


def test_an_srgb_profile_keeps_the_colors() -> None:
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image = Image.new("RGB", (100, 100), (200, 100, 50))
    cover = artwork.process(encode(image, "JPEG", icc_profile=profile, quality=95))
    assert_near(decoded(cover).getpixel((50, 50)), (200, 100, 50), tolerance=6)


def test_a_broken_color_profile_is_ignored_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    image = Image.new("RGB", (100, 100), (200, 100, 50))
    with caplog.at_level(logging.WARNING, logger="scmp3.core.artwork"):
        cover = artwork.process(encode(image, "PNG", icc_profile=b"not a profile"))
    assert_near(decoded(cover).getpixel((50, 50)), (200, 100, 50))
    assert "color profile" in caplog.text


# process: the JPEG it writes


def test_the_cover_is_a_baseline_jpeg_without_exif_or_a_profile() -> None:
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    exif = Image.Exif()
    exif[0x0112] = 1
    source = encode(
        Image.new("RGB", (800, 800), RED), "JPEG", progressive=True, exif=exif, icc_profile=profile
    )
    assert SOF2 in header_markers(source)
    cover = artwork.process(source)
    assert cover.jpeg.startswith(b"\xff\xd8")
    markers = header_markers(cover.jpeg)
    assert SOF0 in markers
    assert SOF2 not in markers
    assert APP1 not in markers
    assert APP2 not in markers
    assert COM not in markers
    assert decoded(cover).format == "JPEG"


def test_a_jpeg_comment_isnt_copied_into_the_cover() -> None:
    source = encode(Image.new("RGB", (100, 100), RED), "JPEG", comment=b"Shot by Jane at home")
    cover = artwork.process(source)
    assert COM not in header_markers(cover.jpeg)
    assert b"Jane" not in cover.jpeg


def test_a_gif_comment_too_long_for_a_jpeg_isnt_copied_either() -> None:
    source = encode(Image.new("RGB", (100, 100), RED), "GIF", comment=b"x" * 70_000)
    assert len(Image.open(io.BytesIO(source)).info["comment"]) == 70_000
    cover = artwork.process(source)
    assert COM not in header_markers(cover.jpeg)


# process: which files it reads


@pytest.mark.parametrize("image_format", artwork.COVER_FORMATS)
def test_every_allowed_format_makes_a_cover(image_format: str) -> None:
    cover = artwork.process(encode(Image.new("RGB", (64, 48), RED), image_format))
    assert (cover.width, cover.height) == (48, 48)
    assert_near(decoded(cover).getpixel((24, 24)), RED, tolerance=30)


@pytest.mark.parametrize("image_format", ["PCX", "TGA", "PPM", "ICO", "DIB", "SGI"])
def test_formats_outside_the_allowlist_are_refused(image_format: str) -> None:
    data = encode(Image.new("RGB", (32, 32), RED), image_format)
    assert Image.open(io.BytesIO(data)).format is not None
    with pytest.raises(ScmpError) as caught:
        artwork.process(data)
    assert caught.value.kind == ErrorKind.convert_failed


def test_an_eps_file_is_refused_without_running_ghostscript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def ghostscript(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("Ghostscript was called")

    monkeypatch.setattr(EpsImagePlugin, "Ghostscript", ghostscript)
    data = encode(Image.new("RGB", (32, 32), RED), "EPS")
    with pytest.raises(ScmpError) as caught:
        artwork.process(data)
    assert caught.value.kind == ErrorKind.convert_failed
    assert calls == []
    assert "Ghostscript" not in str(caught.value)


def test_an_image_over_50_megapixels_is_refused_before_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A blank 1-bit PNG this size is a few kilobytes, though decoded it would take gigabytes.
    data = encode(Image.new("1", (7100, 7100)), "PNG")
    assert len(data) < 100_000

    def no_decoding(self: ImageFile.ImageFile) -> None:
        raise AssertionError("the pixels were decoded")

    monkeypatch.setattr(ImageFile.ImageFile, "load", no_decoding)
    with pytest.raises(ScmpError) as caught:
        artwork.process(data)
    assert caught.value.kind == ErrorKind.convert_failed
    assert "megapixel" in str(caught.value)


@pytest.mark.parametrize("error", [RuntimeError, NotImplementedError, MemoryError])
def test_decoder_errors_of_other_kinds_fail_to_convert(
    monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    # Pillow's AVIF decoder raises RuntimeError, and some plugins NotImplementedError.
    def failing_load(self: ImageFile.ImageFile) -> None:
        raise error("decoder failed")

    monkeypatch.setattr(ImageFile.ImageFile, "load", failing_load)
    with pytest.raises(ScmpError) as caught:
        artwork.process(encode(Image.new("RGB", (32, 32), RED), "PNG"))
    assert caught.value.kind == ErrorKind.convert_failed


def test_a_noisy_cover_at_the_largest_size_setting_is_under_4_mb() -> None:
    cover = artwork.process(noise(1400), max_size=1400)
    assert cover.width == 1400
    assert len(cover.jpeg) < 4_000_000


def test_a_cover_that_would_be_4_mb_or_more_is_refused() -> None:
    with pytest.raises(ScmpError) as caught:
        artwork.process(noise(2400), max_size=2400)
    assert caught.value.kind == ErrorKind.convert_failed


# process: files it can't read


def test_bytes_that_arent_an_image_fail_to_convert() -> None:
    with pytest.raises(ScmpError) as caught:
        artwork.process(b"<html>Not found</html>")
    assert caught.value.kind == ErrorKind.convert_failed


def test_a_truncated_image_fails_to_convert() -> None:
    png = encode(Image.frombytes("RGB", (200, 200), os.urandom(200 * 200 * 3)), "PNG")
    with pytest.raises(ScmpError) as caught:
        artwork.process(png[: len(png) // 2])
    assert caught.value.kind == ErrorKind.convert_failed
