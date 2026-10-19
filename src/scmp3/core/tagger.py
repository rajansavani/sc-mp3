"""Writing and reading tags in MP3 and M4A files, as TECH_SPEC §10 lists them.

Writing replaces every tag in the file, so only these frames and atoms are left, plus an M4A's
gapless playback info. `tag_mp3` and `tag_m4a` do the work for each format.
"""

from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal

from scmp3.core import tag_m4a, tag_mp3
from scmp3.core.artwork import Cover
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import Tags

# The file types sc-mp3 saves, named as their extensions are.
FileFormat = Literal["mp3", "m4a"]

# What each number may be by the time it's written. The editor checks what the user types.
_NUMBER_RANGES: dict[str, range] = {
    "year": range(1, 10_000),
    "track_no": range(1, 1000),
    "track_total": range(1, 1000),
}


def write(path: Path, tags: Tags, cover: Cover | None, *, fmt: FileFormat | None = None) -> None:
    """Replace all the tags in an MP3 or M4A file with `tags` and `cover`.

    The extension gives the format unless `fmt` names it, as for a `.tmp` copy being re-tagged.
    """
    file_format = _format_of(path, fmt)
    _check_tags(tags)
    file_format.write(path, tags, cover)


def read(path: Path) -> tuple[Tags, bytes | None]:
    """The tags in an MP3 or M4A file, and its cover image if it has one."""
    return _format_of(path, None).read(path)


@dataclass(frozen=True)
class _Format:
    """How to write and read one kind of audio file."""

    write: Callable[[Path, Tags, Cover | None], None]
    read: Callable[[Path], tuple[Tags, bytes | None]]


# Keyed by the FileFormat names, which are also the extensions.
_FORMATS: dict[str, _Format] = {
    "mp3": _Format(tag_mp3.write_mp3, tag_mp3.read_mp3),
    "m4a": _Format(tag_m4a.write_m4a, tag_m4a.read_m4a),
}


def _format_of(path: Path, fmt: FileFormat | None) -> _Format:
    """The format `fmt` names, or else the one the file's extension gives."""
    file_format = _FORMATS.get(fmt or path.suffix.lower().removeprefix("."))
    if file_format is not None:
        return file_format
    # Inputs are checked long before tagging, so this is a bug in the caller.
    if fmt:
        detail = f"there's no tag format called {fmt!r}"
    else:
        detail = f"can't tell the tag format from the extension {path.suffix!r}; pass fmt"
    raise ScmpError(ErrorKind.internal, detail)


def _check_tags(tags: Tags) -> None:
    """Refuse values no tag should hold. The editor checks what users type, so these are bugs."""
    for name, allowed in _NUMBER_RANGES.items():
        value = getattr(tags, name)
        if value is not None and value not in allowed:
            detail = f"{name} is {value!r}, outside {allowed.start} to {allowed[-1]}"
            raise ScmpError(ErrorKind.internal, detail)
    for item in fields(tags):
        value = getattr(tags, item.name)
        if isinstance(value, str) and _has_lone_surrogate(value):
            detail = f"{item.name} has a lone surrogate, which no tag can store"
            raise ScmpError(ErrorKind.internal, detail)


def _has_lone_surrogate(text: str) -> bool:
    """Whether `text` holds a lone surrogate, the one thing UTF-8 and UTF-16 can't encode."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False
