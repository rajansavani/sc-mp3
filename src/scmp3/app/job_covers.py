"""Covers as the UI shows them: the image's size, and a small preview of images added by hand.

The full image never crosses the bridge, since every edit sends the whole job again.
"""

import base64
import io
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from scmp3.core import artwork, cover_sources, download
from scmp3.core.cover_choice import read_cover_file
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import safe_url
from scmp3.core.models import ArtworkChoice, CropBox, SourceInfo
from scmp3.core.sources import localfile

# The longest side of the preview sent for an image added by hand, as in the UI's mock.
PREVIEW_PX = 256
# The longest side of the preview sent for a cover from YouTube, which fills the editor's cover
# sharply on a screen scaled to 200%.
YOUTUBE_PREVIEW_PX = 512
PREVIEW_QUALITY = 85
# What a pasted image, and the source's own cover, are saved as in the job's staging folder.
PASTED_PREFIX = "cover-"
SOURCE_COVER_STEM = "cover-source"
# Why a new cover can't be used, as the job's cover_problem. web/strings/main.js has advice for
# each. An image that can't be used is "unreadable", and the others are the error's kind.
UNREADABLE = "unreadable"
INTERNAL_PROBLEM = ErrorKind.internal.value
COVER_PROBLEMS = (
    UNREADABLE,
    ErrorKind.offline.value,
    ErrorKind.rate_limited.value,
    ErrorKind.service_down.value,
    ErrorKind.unavailable.value,
    ErrorKind.disk.value,
    INTERNAL_PROBLEM,
)

# Opens a session whose `urlopen` fetches images, like `with make_ydl() as ydl`.
OpenerSession = Callable[[], AbstractContextManager[Any]]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoverView:
    """What the UI shows for a cover: where the page loads it from, and the size Python crops."""

    url: str | None = None  # an https link, or a small data: preview
    width: int | None = None
    height: int | None = None
    # The square a cover without a crop box is cut to, when it isn't the centered square.
    default_crop: CropBox | None = None
    # A file's own cover that can't be previewed. A re-tag's save keeps it as it is.
    unseen: bool = False

    def size(self) -> dict[str, int] | None:
        """The size as the job dict sends it, or None when it isn't known."""
        if self.width is None or self.height is None:
            return None
        return {"width": self.width, "height": self.height}


NO_COVER = CoverView()


def without_preview(view: CoverView) -> CoverView:
    """The view as queue.json keeps it. A data: preview, up to about 55 KB for YouTube, is left
    out, and made again from the image kept in staging when the queue comes back."""
    if view.url is not None and view.url.startswith("data:"):
        return replace(view, url=None)
    return view


def lacks_preview(view: CoverView) -> bool:
    """Whether queue.json left this view's preview out: it has a size but nothing to show."""
    return view.url is None and view.size() is not None


def remade_preview(view: CoverView, kept: Path, source: SourceInfo | None) -> CoverView:
    """The view with its preview made again from the image kept in staging.

    Raises OSError if the file can't be read, or an error from Pillow if it isn't an image.
    """
    side = YOUTUBE_PREVIEW_PX if source is not None and source.kind == "youtube" else PREVIEW_PX
    return replace(view, url=preview_url(kept.read_bytes(), side))


@dataclass(frozen=True)
class KeptImage:
    """An image's bytes and its extension, kept for the save."""

    data: bytes = field(repr=False)
    ext: str


@dataclass(frozen=True)
class ImageFacts:
    """A checked image's size after EXIF rotation, and the extension of its format."""

    width: int
    height: int
    ext: str
    default_crop: CropBox | None = None  # as artwork.Cover has it


def _view(url: str, facts: ImageFacts) -> CoverView:
    """What the UI shows for a checked image at `url`."""
    return CoverView(
        url=url, width=facts.width, height=facts.height, default_crop=facts.default_crop
    )


def _linked_view(url: str, data: bytes, facts: ImageFacts) -> CoverView:
    """What the UI shows for a checked image downloaded from `url`.

    WebView2's tracking prevention logs a warning for each image the page loads from YouTube's
    image host, so those go to the page as a preview, as files do.
    """
    if cover_sources.is_youtube_image(url):
        return _view(preview_url(data, YOUTUBE_PREVIEW_PX), facts)
    return _view(url, facts)


def inspect(data: bytes) -> ImageFacts:
    """Check that the bytes are an image a cover can be made from. Raises ScmpError if not."""
    # The same processing a save does, so an image that passes here passes then too.
    cover = artwork.process(data, max_size=1)
    with Image.open(io.BytesIO(data), formats=artwork.COVER_FORMATS) as image:
        ext = (image.format or "img").lower()
    return ImageFacts(cover.source_width, cover.source_height, ext, cover.default_crop)


def preview_url(data: bytes, side: int = PREVIEW_PX) -> str:
    """A small JPEG of the whole image as a data: URL, for the page to show and crop over."""
    with Image.open(io.BytesIO(data), formats=artwork.COVER_FORMATS) as opened:
        image = ImageOps.exif_transpose(opened)
        image.thumbnail((side, side))
        rgb = _on_white(image)
    out = io.BytesIO()
    rgb.save(out, "JPEG", quality=PREVIEW_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def _on_white(image: Image.Image) -> Image.Image:
    """The image as RGB, with any transparency shown on white, as the saved cover has it."""
    if not image.has_transparency_data:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    background.alpha_composite(rgba)
    return background.convert("RGB")


class CoverReader:
    """Fetches and checks covers for the UI. Every call is slow, so it runs on a worker."""

    def __init__(self, open_session: OpenerSession) -> None:
        self._open_session = open_session

    def source(self, source: SourceInfo) -> tuple[CoverView, KeptImage | None]:
        """The source's own cover: the first size that works, tried in the order finalize uses.

        The image comes back too, so the save can use it without downloading it again. It's
        None when no size worked. A file's own cover is the one embedded in it.
        """
        if source.kind == "file":
            return _embedded(source)
        urls = cover_sources.cover_candidates(source)
        if not urls:
            return NO_COVER, None
        with self._open_session() as session:
            for url in urls:
                try:
                    data, facts = _checked_image(url, session)
                except ScmpError as error:
                    log.info("The cover at %s didn't work: %s", safe_url(url), error)
                    continue
                return _linked_view(url, data, facts), KeptImage(data, facts.ext)
        # The page can still try the plain link, but there's no size to crop from.
        return CoverView(url=source.artwork_url), None

    def link(self, url: str) -> CoverView:
        """A cover from an https link, with its size. Raises ScmpError when it can't be used."""
        with self._open_session() as session:
            return _fetched(url, session)

    def file(self, path: str) -> CoverView:
        """A cover from an image file, as a small preview. Raises ScmpError if it's unusable."""
        data = read_cover_file(path)
        facts = inspect(data)
        return _view(preview_url(data), facts)

    def pasted(self, data_url: str, folder: Path) -> tuple[Path, CoverView]:
        """Save a pasted image in `folder`, and return its path and preview."""
        data = cover_sources.from_data_url(data_url)
        facts = inspect(data)
        path = folder / f"{PASTED_PREFIX}{uuid.uuid4().hex}.{facts.ext}"
        try:
            path.write_bytes(data)
        except OSError as error:
            reason = error.strerror or error
            raise ScmpError(ErrorKind.disk, f"can't keep the pasted image: {reason}") from error
        return path, _view(preview_url(data), facts)


def _embedded(source: SourceInfo) -> tuple[CoverView, KeptImage | None]:
    """The cover in a file being re-tagged, as a small preview. Raises ScmpError if unusable."""
    data = localfile.embedded_cover(Path(source.id))
    if data is None:
        return NO_COVER, None
    try:
        facts = inspect(data)
    except ScmpError as error:
        log.info("The file's own cover can't be previewed, and the save keeps it: %s", error)
        return CoverView(unseen=True), None
    return _view(preview_url(data), facts), KeptImage(data, facts.ext)


def _fetched(url: str, session: Any) -> CoverView:
    """The image at a link, checked, with its size."""
    data, facts = _checked_image(url, session)
    return _linked_view(url, data, facts)


def _checked_image(url: str, session: Any) -> tuple[bytes, ImageFacts]:
    data = cover_sources.fetch_image(url, session.urlopen)
    return data, inspect(data)


def keep_source_cover(image: KeptImage, folder: Path) -> Path | None:
    """Write the source's cover into the job's staging folder for the save.

    On failure the save downloads it again, so it only logs.
    """
    # A name of its own, so a task whose result came too late can delete its file safely.
    path = folder / f"{SOURCE_COVER_STEM}-{uuid.uuid4().hex}.{image.ext}"
    try:
        path.write_bytes(image.data)
    except OSError as error:
        log.warning("Couldn't keep the source's cover for the save: %s", error.strerror or error)
        return None
    return path


def cover_problem(error: ScmpError | None) -> str:
    """The code for why a new cover can't be used. None stands for a bug in sc-mp3."""
    if error is None:
        return INTERNAL_PROBLEM
    if error.kind == ErrorKind.convert_failed:
        return UNREADABLE
    return error.kind.value if error.kind.value in COVER_PROBLEMS else INTERNAL_PROBLEM


@dataclass(frozen=True)
class CoverRequest:
    """A new cover the user chose, to check before the job switches to it."""

    kind: str  # "url", "file" or "data_url"
    value: str  # the link, the file's path, or the pasted image's data: URL
    crop: CropBox | None


def checked_cover(
    covers: CoverReader, job_id: str, request: CoverRequest
) -> tuple[ArtworkChoice, CoverView]:
    """The artwork choice for a new cover and its view, once the image is known to work.

    A pasted image is saved in the job's staging folder, which goes when the job does, and the
    choice points at that file. Raises ScmpError when the image can't be used.
    """
    if request.kind == "url":
        return ArtworkChoice("url", request.value, request.crop), covers.link(request.value)
    if request.kind == "file":
        return ArtworkChoice("file", request.value, request.crop), covers.file(request.value)
    path, view = covers.pasted(request.value, download.new_staging(job_id))
    return ArtworkChoice("file", str(path), request.crop), view
