"""How the values yt-dlp and api-v2 give for SoundCloud tracks and sets become our fields: cover
links, codec names, dates and lengths."""

import logging
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

# Cover sizes in order of preference. Larger ones such as t3000x3000 are upscaled, so never used.
ARTWORK_IDS = ("original", "t500x500")
# The size suffix of a SoundCloud image URL, as in artworks-000243916348-ktoo7d-large.jpg.
_IMAGE_SIZE = re.compile(r"-[0-9a-z]+\.(?:jpg|png)$")

# Codec names for the start of yt-dlp's acodec, such as mp4a.40.2.
CODECS = {"mp4a": "aac", "mp3": "mp3", "opus": "opus", "alac": "alac", "flac": "flac"}

log = logging.getLogger(__name__)


def codec(acodec: str | None, format_id: str) -> str | None:
    """A plain codec name, from yt-dlp's acodec or else from the format_id, as in hls_mp3_0_0."""
    if acodec and acodec != "none":
        prefix = acodec.split(".", 1)[0].lower()
        return CODECS.get(prefix, prefix)
    parts = format_id.split("_")
    if len(parts) > 1 and parts[1] in CODECS.values():
        return parts[1]
    return None


def pick_artwork(thumbnails: Sequence[Mapping[str, Any]]) -> str | None:
    """The first of ARTWORK_IDS among yt-dlp's thumbnails, for a track or a set."""
    by_id = {thumb.get("id"): thumb.get("url") for thumb in thumbnails}
    return next((by_id[size] for size in ARTWORK_IDS if by_id.get(size)), None)


def t500x500(image: str | None) -> str | None:
    """The t500x500 size of an api-v2 image URL. The original's type would take a request to find.

    An image without a size in its name, like SoundCloud's default avatar, gives None, as it gives
    yt-dlp no sized thumbnails.
    """
    if not image or not _IMAGE_SIZE.search(image):
        return None
    return _IMAGE_SIZE.sub("-t500x500.jpg", image)


def is_avatar(artwork_url: str | None) -> bool:
    """Whether the cover is the uploader's profile picture, which yt-dlp falls back to."""
    if artwork_url is None:
        return False
    name = urlsplit(artwork_url).path.rsplit("/", 1)[-1]
    return name.startswith("avatars-")


def first(values: Sequence[str] | None) -> str | None:
    return values[0] if values else None


def yyyymmdd(value: str | None) -> date | None:
    """A date from yt-dlp's YYYYMMDD form."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        log.warning("Ignoring a date yt-dlp gave in an unexpected form: %r", value)
        return None


def iso_date(value: str | None) -> date | None:
    """The date part of an api-v2 timestamp such as 2017-09-26T00:00:00Z."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        log.warning("Ignoring a date api-v2 gave in an unexpected form: %r", value)
        return None


def seconds(milliseconds: float | None) -> float | None:
    return milliseconds / 1000 if isinstance(milliseconds, int | float) else None
