"""Tests for cover previews: checking images, their sizes, and the small previews the UI gets."""

import base64
import io
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

from scmp3.app.job_covers import (
    COVER_PROBLEMS,
    NO_COVER,
    PREVIEW_PX,
    CoverReader,
    CoverRequest,
    CoverView,
    KeptImage,
    checked_cover,
    cover_problem,
    inspect,
    keep_source_cover,
    preview_url,
)
from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import CropBox, SourceInfo

ARTWORK = "https://i1.sndcdn.com/artworks-000243916348-ktoo7d-t500x500.jpg"
ORIGINAL = "https://i1.sndcdn.com/artworks-000243916348-ktoo7d-original.jpg"
ORIGINAL_PNG = "https://i1.sndcdn.com/artworks-000243916348-ktoo7d-original.png"


def image(width: int, height: int, mode: str = "RGB", fmt: str = "PNG", **save: object) -> bytes:
    buffer = io.BytesIO()
    color = (0, 128, 128, 0) if mode == "RGBA" else "teal"
    Image.new(mode, (width, height), color).save(buffer, fmt, **save)
    return buffer.getvalue()


def decoded(data_url: str) -> Image.Image:
    prefix = "data:image/jpeg;base64,"
    assert data_url.startswith(prefix)
    return Image.open(io.BytesIO(base64.b64decode(data_url.removeprefix(prefix))))


class Response(io.BytesIO):
    def __init__(self, data: bytes, content_type: str) -> None:
        super().__init__(data)
        self.headers = {"Content-Type": content_type}


class FakeWeb:
    """Answers image requests from a table, and counts the sessions opened."""

    def __init__(self, images: dict[str, bytes]) -> None:
        self.images = images
        self.requests: list[str] = []
        self.sessions = 0

    def urlopen(self, url: str) -> Response:
        self.requests.append(url)
        if url not in self.images:
            raise OSError("no answer")
        return Response(self.images[url], "image/png")

    @contextmanager
    def session(self) -> Iterator["FakeWeb"]:
        self.sessions += 1
        yield self


def source(artwork_url: str | None = ARTWORK) -> SourceInfo:
    return SourceInfo(
        kind="soundcloud",
        id="soundcloud:tracks:1",
        url="https://soundcloud.com/kaia/midnight-drive",
        title="Midnight Drive",
        uploader="Kaia",
        artwork_url=artwork_url,
    )


def test_an_image_is_measured_as_the_user_sees_it() -> None:
    rotated = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # rotated 90° clockwise
    Image.new("RGB", (400, 300), "teal").save(rotated, "JPEG", exif=exif)
    facts = inspect(rotated.getvalue())
    assert (facts.width, facts.height, facts.ext) == (300, 400, "jpeg")


def test_something_that_isnt_an_image_is_refused() -> None:
    with pytest.raises(ScmpError) as raised:
        inspect(b"<html>not an image</html>")
    assert raised.value.kind == ErrorKind.convert_failed


def test_the_preview_is_small_and_keeps_the_whole_image() -> None:
    preview = decoded(preview_url(image(1600, 900)))
    assert preview.size == (PREVIEW_PX, 144)


def test_a_small_image_isnt_scaled_up_for_its_preview() -> None:
    assert decoded(preview_url(image(120, 80))).size == (120, 80)


def test_a_transparent_image_previews_on_white() -> None:
    preview = decoded(preview_url(image(64, 64, mode="RGBA")))
    assert preview.getpixel((10, 10)) >= (250, 250, 250)


def test_the_sources_cover_is_the_first_size_that_works() -> None:
    original = image(1400, 1400)
    web = FakeWeb({ORIGINAL_PNG: original, ARTWORK: image(500, 500)})
    view, kept = CoverReader(web.session).source(source())
    assert view == CoverView(url=ORIGINAL_PNG, width=1400, height=1400)
    assert web.requests == [ORIGINAL, ORIGINAL_PNG]
    assert web.sessions == 1
    # It comes back for the save, so it isn't downloaded twice.
    assert kept == KeptImage(original, "png")


def test_a_sources_cover_that_wont_load_is_still_shown_by_its_link() -> None:
    view, kept = CoverReader(FakeWeb({}).session).source(source())
    assert view == CoverView(url=ARTWORK)
    assert view.size() is None
    assert kept is None


def test_a_source_without_artwork_has_no_cover() -> None:
    web = FakeWeb({})
    assert CoverReader(web.session).source(source(None)) == (NO_COVER, None)
    assert web.sessions == 0


def test_the_sources_cover_is_kept_under_a_name_of_its_own(tmp_path: Path) -> None:
    first = keep_source_cover(KeptImage(b"one", "jpeg"), tmp_path)
    second = keep_source_cover(KeptImage(b"two", "jpeg"), tmp_path)
    assert first is not None and second is not None
    assert first != second
    assert first.name.startswith("cover-source-") and first.suffix == ".jpeg"
    assert first.read_bytes() == b"one"


def test_a_cover_that_cant_be_kept_only_logs(tmp_path: Path) -> None:
    assert keep_source_cover(KeptImage(b"one", "jpeg"), tmp_path / "missing folder") is None


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ScmpError(ErrorKind.convert_failed, "not an image"), "unreadable"),
        (ScmpError(ErrorKind.offline), "offline"),
        (ScmpError(ErrorKind.rate_limited), "rate_limited"),
        (ScmpError(ErrorKind.service_down), "service_down"),
        (ScmpError(ErrorKind.unavailable), "unavailable"),
        (ScmpError(ErrorKind.disk), "disk"),
        (ScmpError(ErrorKind.engine_outdated), "internal"),
        (None, "internal"),
    ],
)
def test_a_cover_problem_says_what_went_wrong(error: ScmpError | None, code: str) -> None:
    assert cover_problem(error) == code
    assert code in COVER_PROBLEMS


def test_a_cover_link_is_measured() -> None:
    link = "https://example.com/cover.png"
    view = CoverReader(FakeWeb({link: image(800, 600)}).session).link(link)
    assert view.size() == {"width": 800, "height": 600}
    assert view.url == link


def test_a_cover_link_that_fails_raises() -> None:
    with pytest.raises(ScmpError) as raised:
        CoverReader(FakeWeb({}).session).link("https://example.com/missing.png")
    assert raised.value.kind == ErrorKind.offline


def test_a_cover_file_is_sent_as_a_preview(tmp_path: Path) -> None:
    path = tmp_path / "cover.png"
    path.write_bytes(image(1000, 500))
    view = CoverReader(FakeWeb({}).session).file(str(path))
    assert view.size() == {"width": 1000, "height": 500}
    assert decoded(view.url or "").size == (PREVIEW_PX, 128)


def test_a_missing_cover_file_raises_a_disk_error(tmp_path: Path) -> None:
    with pytest.raises(ScmpError) as raised:
        CoverReader(FakeWeb({}).session).file(str(tmp_path / "gone.png"))
    assert raised.value.kind == ErrorKind.disk


def test_a_pasted_image_is_saved_in_the_folder_it_is_given(tmp_path: Path) -> None:
    data = image(300, 300)
    data_url = "data:image/png;base64," + base64.b64encode(data).decode()
    path, view = CoverReader(FakeWeb({}).session).pasted(data_url, tmp_path)
    assert path.parent == tmp_path
    assert path.suffix == ".png"
    assert path.read_bytes() == data
    assert view.size() == {"width": 300, "height": 300}


def test_a_pasted_text_isnt_taken_as_an_image(tmp_path: Path) -> None:
    with pytest.raises(ScmpError):
        CoverReader(FakeWeb({}).session).pasted("data:text/plain,hello", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_checked_link_becomes_a_url_choice_with_its_crop() -> None:
    link = "https://example.com/cover.png"
    crop = CropBox(0.1, 0.1, 0.5, 0.5)
    covers = CoverReader(FakeWeb({link: image(100, 100)}).session)
    choice, view = checked_cover(covers, "job1", CoverRequest("url", link, crop))
    assert (choice.kind, choice.value, choice.crop) == ("url", link, crop)
    assert view.url == link


def test_a_checked_paste_becomes_a_file_in_the_jobs_staging_folder() -> None:
    data_url = "data:image/png;base64," + base64.b64encode(image(50, 50)).decode()
    covers = CoverReader(FakeWeb({}).session)
    choice, _ = checked_cover(covers, "job1", CoverRequest("data_url", data_url, None))
    assert choice.kind == "file"
    assert Path(choice.value or "").parent == paths.staging_dir() / "job1"


def test_a_letterboxed_cover_says_where_python_cuts_it() -> None:
    frame = Image.new("RGB", (480, 360), (0, 0, 0))
    frame.paste(Image.new("RGB", (480, 270), "navy"), (0, 45))
    buffer = io.BytesIO()
    frame.save(buffer, "PNG")
    web = FakeWeb({ARTWORK: buffer.getvalue()})
    view, _kept = CoverReader(web.session).source(source())
    assert view.default_crop == CropBox(105 / 480, 45 / 360, 270 / 480, 270 / 360)
    assert view.size() == {"width": 480, "height": 360}


def test_a_cover_that_fills_its_frame_has_no_default_crop() -> None:
    assert inspect(image(1280, 720)).default_crop is None
