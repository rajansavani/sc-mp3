"""Files you already have, read for re-tagging: their tags and cover as a SourceInfo of kind file.

The file's title and artist go through the same cleanup as a link's title and uploader, and the
fields the cleanup doesn't touch, such as the track number, come straight from the file.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import mutagen
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

from scmp3.core import tagger
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import Settings, SourceInfo, Tags, TagSuggestion
from scmp3.core.suggest import suggest
from scmp3.core.tag_common import file_errors
from scmp3.core.tagger import FileFormat

# The audio each extension must hold, so an M4A named .mp3 is never tagged as an MP3.
CONTAINERS: dict[FileFormat, type[mutagen.FileType]] = {"mp3": MP3, "m4a": MP4}
# The tag fields Spotify's playlists know a local song by, besides its length.
IDENTITY_FIELDS = ("title", "artist", "album")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalFile:
    """A file as it is on disk now: its source info, its tags and cover, and its audio."""

    path: Path
    fmt: FileFormat
    source: SourceInfo
    tags: Tags
    cover: bytes | None
    duration: float | None
    bitrate: int | None  # kbps


def read_file(path: Path) -> LocalFile:
    """Read a file to re-tag. Raises ScmpError when it has gone or isn't MP3 or M4A audio."""
    check_present(path)
    fmt = file_format(path)
    # Before reading, so a change made while it's read counts as a change too.
    stamp = file_stamp(path)
    audio = _open_audio(path, fmt)
    with _as_unreadable(path):
        tags, cover = tagger.read(path)
    duration = float(audio.info.length) if audio.info.length else None
    bitrate = round(audio.info.bitrate / 1000) if getattr(audio.info, "bitrate", 0) else None
    source = replace(source_from_tags(path, tags, duration), file_stamp=stamp)
    return LocalFile(path, fmt, source, tags, cover, duration, bitrate)


def check_present(path: Path) -> None:
    """Raise ScmpError(file_missing) when the file isn't there, as after a move or an unplug."""
    if not path.is_file():
        raise ScmpError(ErrorKind.disk, f"{path.name} isn't there", reason="file_missing")


def file_stamp(path: Path) -> str:
    """The file's size and modified time, which change when another program edits it."""
    with _as_unreadable(path), file_errors("look at the file"):
        stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def check_unchanged(source: SourceInfo, local: "LocalFile") -> None:
    """Raise ScmpError(file_changed) when the file isn't as it was when source was read.

    A source kept from before stamps were taken has none, and passes.
    """
    if source.file_stamp is None or source.file_stamp == local.source.file_stamp:
        return
    raise ScmpError(
        ErrorKind.disk, f"{local.path.name} changed since it was read", reason="file_changed"
    )


def file_format(path: Path) -> FileFormat:
    """The format the file's extension names. Inputs only take .mp3 and .m4a files."""
    suffix = path.suffix.lower().removeprefix(".")
    if suffix == "mp3" or suffix == "m4a":
        return suffix
    raise ScmpError(ErrorKind.internal, f"can't re-tag a {path.suffix!r} file")


def source_from_tags(path: Path, tags: Tags, duration: float | None) -> SourceInfo:
    """The file as a source: its title to clean up, and its artist as both uploader and credit."""
    year = tags.year if tags.year and 1 <= tags.year <= 9999 else None
    return SourceInfo(
        kind="file",
        id=str(path),
        url=str(path),
        title=tags.title,
        # The artist tag plays the uploader's part too, so "Kaia - Song" by Kaia loses its "Kaia -".
        uploader=tags.artist,
        artists=[tags.artist] if tags.artist else [],
        album=tags.album or None,
        # Only the year is kept, so the date is the year's first day.
        release_date=date(year, 1, 1) if year else None,
        genre=tags.genre,
        description=tags.comment,
        duration=duration,
        isrc=tags.isrc,
        label=tags.label,
    )


def suggestion_for(local: LocalFile, settings: Settings) -> TagSuggestion:
    """The cleanup's suggestion, keeping the album artist and track number the file has."""
    return replace(
        suggest(local.source, settings),
        album_artist=local.tags.album_artist or None,
        track_no=local.tags.track_no,
        track_total=local.tags.track_total,
    )


def identity(source: SourceInfo) -> dict[str, str]:
    """The title, artist and album the file has now, which Spotify's playlists know it by."""
    return {"title": source.title, "artist": source.uploader, "album": source.album or ""}


def changes_identity(source: SourceInfo, tags: Tags) -> bool:
    """Whether writing `tags` gives the file a new title, artist or album, which Spotify's
    playlists would lose it over."""
    current = identity(source)
    return any(getattr(tags, name) != current[name] for name in IDENTITY_FIELDS)


def embedded_cover(path: Path) -> bytes | None:
    """The cover image in the file, or None when it has none. Raises ScmpError if it can't read."""
    check_present(path)
    with _as_unreadable(path):
        _tags, cover = tagger.read(path)
    return cover


def _open_audio(path: Path, fmt: FileFormat) -> mutagen.FileType:
    """Open the file's audio, checking it's the kind its extension says."""
    with _as_unreadable(path), file_errors("read the audio"):
        audio = mutagen.File(path)
    if not isinstance(audio, CONTAINERS[fmt]):
        kind = type(audio).__name__ if audio is not None else "not audio mutagen knows"
        raise ScmpError(
            ErrorKind.convert_failed, f"{path.name} is {kind}", reason="unreadable_file"
        )
    return audio


@contextmanager
def _as_unreadable(path: Path) -> Iterator[None]:
    """Mark a convert_failed error as an unreadable file, and a disk error as a missing file
    when the file has gone, or else as a locked one, such as a file another program holds."""
    try:
        yield
    except ScmpError as error:
        if error.reason is None and error.kind == ErrorKind.convert_failed:
            error.reason = "unreadable_file"
        elif error.reason is None and error.kind == ErrorKind.disk:
            error.reason = "file_locked" if path.is_file() else "file_missing"
        raise
