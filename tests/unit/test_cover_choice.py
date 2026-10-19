"""Tests for turning a cover choice into the finished cover, with a fake opener."""

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from scmp3.core import cover_choice
from scmp3.core.cover_choice import load_cover
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import ArtworkChoice, ArtworkKind, CropBox, SourceInfo

ARTWORK = "https://i1.sndcdn.com/artworks-000243916348-ktoo7d"


def image(width: int, height: int, fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "teal").save(buffer, fmt)
    return buffer.getvalue()


class Response(io.BytesIO):
    def __init__(self, data: bytes, content_type: str = "image/png") -> None:
        super().__init__(data)
        self.headers = {"Content-Type": content_type}


class FakeOpener:
    """Serves images by URL, answering 404 for the rest, and keeps what it was asked for."""

    def __init__(self, images: dict[str, bytes]) -> None:
        self.images = images
        self.requested: list[str] = []

    def __call__(self, url: str) -> Response:
        self.requested.append(url)
        if url not in self.images:
            raise OSError("HTTP Error 404: Not Found")
        return Response(self.images[url])


def source(artwork_url: str | None = f"{ARTWORK}-original.jpg") -> SourceInfo:
    return SourceInfo(
        "soundcloud",
        "soundcloud:tracks:1",
        "https://soundcloud.com/a/b",
        "B",
        "A",
        artwork_url=artwork_url,
    )


def test_no_cover_needs_no_request() -> None:
    opener = FakeOpener({})
    assert load_cover(ArtworkChoice("none"), source(), 1000, opener) is None
    assert opener.requested == []


def test_a_track_without_artwork_gets_no_cover() -> None:
    opener = FakeOpener({})
    assert load_cover(ArtworkChoice(), source(artwork_url=None), 1000, opener) is None
    assert opener.requested == []


def test_the_source_cover_tries_the_original_first() -> None:
    opener = FakeOpener({f"{ARTWORK}-original.jpg": image(1400, 1400)})
    cover = load_cover(ArtworkChoice(), source(), 1000, opener)
    assert cover is not None
    assert (cover.width, cover.source_width) == (1000, 1400)
    assert opener.requested == [f"{ARTWORK}-original.jpg"]


def test_the_source_cover_moves_on_when_a_size_is_missing_or_unreadable() -> None:
    opener = FakeOpener(
        {f"{ARTWORK}-original.png": b"not an image", f"{ARTWORK}-t500x500.jpg": image(500, 500)}
    )
    cover = load_cover(ArtworkChoice(), source(), 1000, opener)
    assert cover is not None
    assert cover.width == 500
    assert opener.requested == [
        f"{ARTWORK}-original.jpg",
        f"{ARTWORK}-original.png",
        f"{ARTWORK}-t500x500.jpg",
    ]


def test_when_no_size_works_the_last_failure_is_raised() -> None:
    with pytest.raises(ScmpError) as raised:
        load_cover(ArtworkChoice(), source(), 1000, FakeOpener({}))
    # The opener's error has no status, so it reads as no answer at all.
    assert raised.value.kind == ErrorKind.offline
    assert "t500x500" in str(raised.value)


def test_a_cover_from_a_link_is_cropped_as_chosen() -> None:
    url = "https://example.com/cover.png"
    crop = CropBox(left=0.0, top=0.0, width=0.5, height=1.0)
    choice = ArtworkChoice("url", url, crop)
    cover = load_cover(choice, source(), 1000, FakeOpener({url: image(800, 400)}))
    assert cover is not None
    assert cover.width == 400


def test_a_pasted_image_is_read_from_its_data_url_without_a_request() -> None:
    data_url = "data:image/png;base64," + base64.b64encode(image(600, 600)).decode()
    opener = FakeOpener({})
    cover = load_cover(ArtworkChoice("url", data_url), source(), 300, opener)
    assert cover is not None
    assert cover.width == 300
    assert opener.requested == []


def test_a_cover_from_a_file_is_read_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "cover.jpg"
    path.write_bytes(image(700, 700, "JPEG"))
    cover = load_cover(ArtworkChoice("file", str(path)), source(), 1000, FakeOpener({}))
    assert cover is not None
    assert cover.width == 700


def test_a_missing_cover_file_is_a_disk_error(tmp_path: Path) -> None:
    choice = ArtworkChoice("file", str(tmp_path / "gone.jpg"))
    with pytest.raises(ScmpError) as raised:
        load_cover(choice, source(), 1000, FakeOpener({}))
    assert raised.value.kind == ErrorKind.disk
    assert str(tmp_path) not in str(raised.value)


def test_a_cover_file_too_big_to_be_a_cover_is_refused_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cover_choice.cover_sources, "MAX_IMAGE_BYTES", 100)
    path = tmp_path / "huge.png"
    path.write_bytes(image(300, 300))
    with pytest.raises(ScmpError) as raised:
        load_cover(ArtworkChoice("file", str(path)), source(), 1000, FakeOpener({}))
    assert raised.value.kind == ErrorKind.convert_failed


@pytest.mark.parametrize("kind", ["url", "file"])
def test_a_choice_without_its_link_or_path_is_a_bug(kind: ArtworkKind) -> None:
    with pytest.raises(ScmpError) as raised:
        load_cover(ArtworkChoice(kind), source(), 1000, FakeOpener({}))
    assert raised.value.kind == ErrorKind.internal
