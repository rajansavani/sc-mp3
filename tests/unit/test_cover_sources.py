"""Tests for fetching cover images and choosing SoundCloud cover sizes, with fake openers only."""

import base64
import io
import urllib.error
from collections.abc import Callable
from email.message import Message
from typing import Any

import pytest
from PIL import Image
from yt_dlp.networking.common import Response
from yt_dlp.networking.exceptions import HTTPError, TransportError

from scmp3.core import cover_sources
from scmp3.core.errors import Cancelled, ErrorKind, ScmpError
from scmp3.core.models import PlaylistInfo, SourceInfo

RED = (255, 0, 0)
IMAGE_URL = "https://img.example.com/covers/a.jpg"
SC = "https://i1.sndcdn.com/artworks-000209893581-orfv6t"


def encode(image: Image.Image, image_format: str, **options: Any) -> bytes:
    """The image saved in memory in `image_format`."""
    buffer = io.BytesIO()
    image.save(buffer, image_format, **options)
    return buffer.getvalue()


class FakeResponse(io.BytesIO):
    """A response shaped like urllib's: a readable body with email-style headers."""

    def __init__(self, body: bytes, content_type: str | None) -> None:
        super().__init__(body)
        self.headers = Message()
        if content_type is not None:
            self.headers["Content-Type"] = content_type
        self.bytes_read = 0

    def read(self, size: int | None = -1, /) -> bytes:
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


def serving(response: Any) -> Callable[[str], Any]:
    """An opener that returns `response` for any URL."""
    return lambda url: response


def failing(error: Exception) -> Callable[[str], Any]:
    """An opener that raises `error` for any URL."""

    def opener(url: str) -> Any:
        raise error

    return opener


def http_error(status: int) -> HTTPError:
    """yt-dlp's error for an HTTP status."""
    return HTTPError(Response(io.BytesIO(b""), IMAGE_URL, {}, status=status))


def soundcloud_track(artwork_url: str | None) -> SourceInfo:
    return SourceInfo(
        kind="soundcloud",
        id="soundcloud:tracks:1",
        url="https://soundcloud.com/kaia/midnight-drive",
        title="Midnight Drive",
        uploader="Kaia",
        artwork_url=artwork_url,
    )


# fetch_image


def test_fetch_image_returns_the_body_of_an_image_response() -> None:
    body = encode(Image.new("RGB", (8, 8), RED), "JPEG")
    urls: list[str] = []

    def opener(url: str) -> FakeResponse:
        urls.append(url)
        return FakeResponse(body, "image/jpeg")

    assert cover_sources.fetch_image(IMAGE_URL, opener) == body
    assert urls == [IMAGE_URL]


def test_fetch_image_reads_a_yt_dlp_response() -> None:
    response = Response(io.BytesIO(b"png bytes"), IMAGE_URL, {"content-type": "image/png"})
    assert cover_sources.fetch_image(IMAGE_URL, serving(response)) == b"png bytes"
    assert response.closed


@pytest.mark.parametrize("content_type", ["image/png; charset=binary", "Image/WebP"])
def test_fetch_image_accepts_image_types_with_parameters_or_capitals(content_type: str) -> None:
    opener = serving(FakeResponse(b"data", content_type))
    assert cover_sources.fetch_image(IMAGE_URL, opener) == b"data"


@pytest.mark.parametrize("content_type", ["text/html", "application/json", None])
def test_fetch_image_refuses_a_response_that_isnt_an_image(content_type: str | None) -> None:
    response = FakeResponse(b"<html>", content_type)
    with pytest.raises(ScmpError) as caught:
        cover_sources.fetch_image(IMAGE_URL, serving(response))
    assert caught.value.kind == ErrorKind.unavailable
    assert response.closed


def test_fetch_image_stops_reading_once_the_image_is_over_the_cap() -> None:
    response = FakeResponse(b"x" * 5_000_000, "image/jpeg")
    with pytest.raises(ScmpError) as caught:
        cover_sources.fetch_image(IMAGE_URL, serving(response), max_bytes=100_000)
    assert caught.value.kind == ErrorKind.unavailable
    assert response.bytes_read < 300_000


def test_fetch_image_accepts_an_image_exactly_at_the_cap() -> None:
    opener = serving(FakeResponse(b"x" * 1000, "image/jpeg"))
    assert len(cover_sources.fetch_image(IMAGE_URL, opener, max_bytes=1000)) == 1000


def test_fetch_image_caps_images_at_20_mb_by_default() -> None:
    assert cover_sources.MAX_IMAGE_BYTES == 20_000_000


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (http_error(404), ErrorKind.unavailable),
        (http_error(403), ErrorKind.unavailable),
        (http_error(429), ErrorKind.rate_limited),
        (http_error(503), ErrorKind.service_down),
        (TransportError("connection reset"), ErrorKind.offline),
        (
            urllib.error.HTTPError(IMAGE_URL, 404, "Not Found", Message(), io.BytesIO(b"")),
            ErrorKind.unavailable,
        ),
        (urllib.error.URLError("no route to host"), ErrorKind.offline),
        (TimeoutError("timed out"), ErrorKind.offline),
    ],
)
def test_fetch_image_maps_opener_errors_to_error_kinds(error: Exception, kind: ErrorKind) -> None:
    with pytest.raises(ScmpError) as caught:
        cover_sources.fetch_image(IMAGE_URL, failing(error))
    assert caught.value.kind == kind
    assert caught.value.__cause__ is error


def test_fetch_image_closes_the_response_an_http_error_holds() -> None:
    error = http_error(404)
    with pytest.raises(ScmpError):
        cover_sources.fetch_image(IMAGE_URL, failing(error))
    assert error.response.closed


def test_fetch_image_leaves_the_query_out_of_error_details() -> None:
    with pytest.raises(ScmpError) as caught:
        cover_sources.fetch_image(f"{IMAGE_URL}?token=s3cret", failing(TimeoutError("timed out")))
    assert "s3cret" not in str(caught.value)
    assert "img.example.com/covers/a.jpg" in str(caught.value)


def test_fetch_image_leaves_a_user_name_and_password_out_of_error_details() -> None:
    url = "http://me:hunter22@img.example.com:8080/a.jpg"
    with pytest.raises(ScmpError) as caught:
        cover_sources.fetch_image(url, failing(TimeoutError("timed out")))
    assert "hunter22" not in str(caught.value)
    assert "me:" not in str(caught.value)
    assert "img.example.com:8080/a.jpg" in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Users/someone/Pictures/cover.jpg",
        "ftp://img.example.com/a.jpg",
        "C:/Users/someone/cover.jpg",
        "not a link",
        "",
    ],
)
def test_fetch_image_refuses_links_that_arent_http_without_opening_them(url: str) -> None:
    def opener(url: str) -> Any:
        raise AssertionError("the opener was called")

    with pytest.raises(ScmpError) as caught:
        cover_sources.fetch_image(url, opener)
    assert caught.value.kind == ErrorKind.unavailable
    # A file link holds a local path, which can include the user's name.
    assert "someone" not in str(caught.value)


def test_fetch_image_lets_a_cancel_through() -> None:
    with pytest.raises(Cancelled):
        cover_sources.fetch_image(IMAGE_URL, failing(Cancelled()))


def test_fetch_image_reports_a_broken_opener_as_our_own_bug() -> None:
    with pytest.raises(ScmpError) as caught:
        cover_sources.fetch_image(IMAGE_URL, serving(None))
    assert caught.value.kind == ErrorKind.internal


# from_data_url


def test_from_data_url_decodes_a_base64_image() -> None:
    png = encode(Image.new("RGB", (4, 4), RED), "PNG")
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    assert cover_sources.from_data_url(data_url) == png


def test_from_data_url_decodes_a_percent_encoded_image() -> None:
    assert cover_sources.from_data_url("data:image/svg+xml,%3Csvg%2F%3E") == b"<svg/>"


@pytest.mark.parametrize(
    "value",
    [
        "data:text/plain;base64,aGVsbG8=",
        "data:;base64,aGVsbG8=",
        "https://img.example.com/a.png",
        "data:image/png;base64,not*base64!",
        "image/png;base64,aGVsbG8=",
    ],
)
def test_from_data_url_refuses_anything_but_an_image_data_url(value: str) -> None:
    with pytest.raises(ScmpError) as caught:
        cover_sources.from_data_url(value)
    assert caught.value.kind == ErrorKind.unavailable


# pick_largest

# The list yt-dlp builds for a SoundCloud track. The original has no size, only a preference.
SOUNDCLOUD_THUMBNAILS = [
    {"id": "mini", "url": f"{SC}-mini.jpg", "width": 16, "height": 16},
    {"id": "large", "url": f"{SC}-large.jpg", "width": 100, "height": 100},
    {"id": "t300x300", "url": f"{SC}-t300x300.jpg", "width": 300, "height": 300},
    {"id": "crop", "url": f"{SC}-crop.jpg", "width": 400, "height": 400},
    {"id": "t500x500", "url": f"{SC}-t500x500.jpg", "width": 500, "height": 500},
    {"id": "original", "url": f"{SC}-original.png", "preference": 10},
]


def test_pick_largest_prefers_soundclouds_original() -> None:
    assert cover_sources.pick_largest(SOUNDCLOUD_THUMBNAILS) == f"{SC}-original.png"


def test_pick_largest_skips_excluded_ids() -> None:
    upscaled = {"id": "t3000x3000", "url": f"{SC}-t3000x3000.jpg", "width": 3000, "preference": 20}
    thumbnails = [*SOUNDCLOUD_THUMBNAILS, upscaled]
    picked = cover_sources.pick_largest(thumbnails, exclude=cover_sources.SOUNDCLOUD_UPSCALED)
    assert picked == f"{SC}-original.png"
    assert cover_sources.pick_largest(thumbnails, exclude={"original", "t3000x3000"}) == (
        f"{SC}-t500x500.jpg"
    )


def test_pick_largest_ranks_by_width_then_height_without_preferences() -> None:
    thumbnails = [
        {"url": "https://i.ytimg.com/vi/x/default.jpg", "width": 120, "height": 90},
        {"url": "https://i.ytimg.com/vi/x/maxresdefault.jpg", "width": 1280, "height": 720},
        {"url": "https://i.ytimg.com/vi/x/hq720.jpg", "width": 1280, "height": 700},
        {"url": "https://i.ytimg.com/vi/x/sddefault.jpg", "width": 640, "height": 480},
    ]
    assert cover_sources.pick_largest(thumbnails) == "https://i.ytimg.com/vi/x/maxresdefault.jpg"


def test_pick_largest_ranks_preference_above_size_as_yt_dlp_does() -> None:
    thumbnails = [
        {"url": "https://example.com/big.jpg", "width": 1920, "preference": -5},
        {"url": "https://example.com/small.jpg", "width": 480, "preference": 0},
    ]
    assert cover_sources.pick_largest(thumbnails) == "https://example.com/small.jpg"


def test_pick_largest_skips_thumbnails_without_a_url_and_can_find_none() -> None:
    assert cover_sources.pick_largest([{"id": "original", "preference": 10}]) is None
    assert cover_sources.pick_largest([]) is None


# soundcloud_candidates


JPG_FIRST = [f"{SC}-original.jpg", f"{SC}-original.png", f"{SC}-t500x500.jpg"]
PNG_FIRST = [f"{SC}-original.png", f"{SC}-original.jpg", f"{SC}-t500x500.jpg"]


@pytest.mark.parametrize(
    ("artwork_url", "expected"),
    [
        (f"{SC}-original.jpg", JPG_FIRST),
        # The original's own extension comes first; the 500 px size is always JPEG.
        (f"{SC}-original.png", PNG_FIRST),
        (f"{SC}-large.jpg", JPG_FIRST),
        (f"{SC}-t500x500.jpg", JPG_FIRST),
        (f"{SC}-t3000x3000.jpg", JPG_FIRST),
        (f"{SC}-t1080x1080.png", PNG_FIRST),
    ],
)
def test_soundcloud_candidates_are_the_original_as_jpeg_or_png_then_500_px(
    artwork_url: str, expected: list[str]
) -> None:
    assert cover_sources.soundcloud_candidates(soundcloud_track(artwork_url)) == expected


def test_soundcloud_candidates_never_include_the_upscaled_sizes() -> None:
    for url in (f"{SC}-t3000x3000.jpg", f"{SC}-t1080x1080.png", f"{SC}-original.jpg"):
        for candidate in cover_sources.soundcloud_candidates(soundcloud_track(url)):
            assert not any(size in candidate for size in cover_sources.SOUNDCLOUD_UPSCALED)


def test_soundcloud_candidates_work_for_an_avatar() -> None:
    avatar = "https://a1.sndcdn.com/avatars-000123-abcdef-large.jpg"
    assert cover_sources.soundcloud_candidates(soundcloud_track(avatar)) == [
        "https://a1.sndcdn.com/avatars-000123-abcdef-original.jpg",
        "https://a1.sndcdn.com/avatars-000123-abcdef-original.png",
        "https://a1.sndcdn.com/avatars-000123-abcdef-t500x500.jpg",
    ]


def test_soundcloud_candidates_work_for_a_set() -> None:
    playlist = PlaylistInfo(title="EP", owner="Kaia", artwork_url=f"{SC}-large.jpg")
    assert cover_sources.soundcloud_candidates(playlist) == JPG_FIRST


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/cover.webp",
        # Sized like SoundCloud's, but on another host.
        "https://example.com/my-cover.jpg",
        "https://sndcdn.com.example.net/artworks-1-large.jpg",
    ],
)
def test_soundcloud_candidates_keep_any_other_url_alone(url: str) -> None:
    assert cover_sources.soundcloud_candidates(soundcloud_track(url)) == [url]


def test_soundcloud_candidates_are_empty_without_artwork() -> None:
    assert cover_sources.soundcloud_candidates(soundcloud_track(None)) == []
