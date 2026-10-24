"""Turning the cover someone chose into the finished cover that goes in the tags.

The choice is the source's artwork, a link, a pasted image or a file. `cover_sources` fetches the
images and `artwork` processes them.
"""

import logging
from pathlib import Path

from scmp3.core import artwork, cover_sources
from scmp3.core.artwork import Cover
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import safe_url
from scmp3.core.models import ArtworkChoice, PlaylistInfo, SourceInfo

log = logging.getLogger(__name__)


def load_cover(
    choice: ArtworkChoice,
    source: SourceInfo | PlaylistInfo,
    max_size: int,
    opener: cover_sources.Opener,
) -> Cover | None:
    """The finished cover for a choice, or None for none. The source's artwork tries each size.

    The source is a track, or a set whose own cover an album shares.

    Raises the last failure when no image works, so a save never quietly loses its cover.
    """
    if choice.kind == "none":
        return None
    if choice.kind == "file":
        return artwork.process(read_cover_file(choice.value), crop=choice.crop, max_size=max_size)
    if choice.kind == "source":
        urls = cover_sources.cover_candidates(source)
    elif choice.value:
        urls = [choice.value]
    else:
        raise ScmpError(ErrorKind.internal, "a cover from a link needs the link")
    if not urls:
        return None
    return _first_that_works(urls, choice, max_size, opener)


def _first_that_works(
    urls: list[str], choice: ArtworkChoice, max_size: int, opener: cover_sources.Opener
) -> Cover:
    """The cover from the first URL that downloads and processes."""
    failures: list[ScmpError] = []
    for url in urls:
        try:
            data = image_bytes(url, opener)
            return artwork.process(data, crop=choice.crop, max_size=max_size)
        except ScmpError as error:
            log.info("The cover at %s didn't work: %s", safe_url(url), error)
            failures.append(error)
    raise failures[-1]


def image_bytes(url: str, opener: cover_sources.Opener) -> bytes:
    """The image at a web link, or in a data URL such as a pasted image."""
    if url[:5].lower() == "data:":
        return cover_sources.from_data_url(url)
    return cover_sources.fetch_image(url, opener)


def read_cover_file(value: str | None) -> bytes:
    """A cover image file's bytes, refusing one too big to be a cover."""
    if not value:
        raise ScmpError(ErrorKind.internal, "a cover from a file needs the file's path")
    limit = cover_sources.MAX_IMAGE_BYTES
    try:
        with Path(value).open("rb") as file:
            # One byte more than the limit is enough to tell the file is too big.
            data = file.read(limit + 1)
    except OSError as error:
        reason = error.strerror or error
        raise ScmpError(ErrorKind.disk, f"can't read the cover file: {reason}") from error
    if len(data) > limit:
        raise ScmpError(ErrorKind.convert_failed, f"the cover file is over {limit} bytes")
    return data
