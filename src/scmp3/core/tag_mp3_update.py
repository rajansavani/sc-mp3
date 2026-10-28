"""Updating an MP3's tag for a re-tag: only the frames of the fields that changed, and the
pictures when the cover changed, keeping every other frame, the tag's version and any ID3v1 tag.

Call it through `tagger.update`, which checks the values first.
"""

from collections.abc import Callable, Collection
from pathlib import Path

from mutagen.id3 import ID3, TCON, TDRC, TRCK, TYER, ID3NoHeaderError, ID3v1SaveOptions, TextFrame

from scmp3.core.artwork import Cover
from scmp3.core.models import Tags
from scmp3.core.tag_common import file_errors
from scmp3.core.tag_mp3 import (
    MP3_TEXT_FRAMES,
    TEXT_ENCODING,
    check_mpeg_audio,
    escape_genre,
    picture_frame,
    track_text,
)

# The frames a year can be in, which a new year replaces: v2.4's date, or v2.3's year, day and time.
_DATE_FRAMES = ("TDRC", "TYER", "TDAT", "TIME")


def update_mp3(
    path: Path, tags: Tags, fields: Collection[str], cover: Cover | None, replace_cover: bool
) -> None:
    """Rewrite only the frames of `fields`, and the pictures with `replace_cover`, keeping every
    other frame, the tag's version and any ID3v1 tag, which follows the new values."""
    check_mpeg_audio(path)
    with file_errors("update the MP3 tags"):
        id3, version = _load_for_update(path)
        for name in fields:
            _UPDATES[name](id3, tags, version)
        if replace_cover:
            id3.delall("APIC")
            if cover is not None:
                id3.add(picture_frame(cover))
        # No separator, so a v2.3 frame's several values stay null-separated, as they were read.
        id3.save(path, v2_version=version, v1=ID3v1SaveOptions.UPDATE, v23_sep=None)


def _load_for_update(path: Path) -> tuple[ID3, int]:
    """The file's tag as it is, untranslated, and the ID3v2 version to save it in."""
    try:
        id3 = ID3(path, translate=False)
    except ID3NoHeaderError:
        return ID3(), 3
    if id3.version >= (2, 4, 0):
        return id3, 4
    if id3.version >= (2, 3, 0):
        return id3, 3
    # mutagen can't write ID3v2.2 or a lone ID3v1 tag, so those frames move to v2.3.
    id3 = ID3(path)
    id3.update_to_v23()
    return id3, 3


def _set_text(id3: ID3, frame_type: type[TextFrame], value: str | None) -> None:
    """Replace a text frame, or remove it when the value is empty, as write_mp3 leaves it out."""
    id3.delall(frame_type.__name__)
    if value:
        id3.add(frame_type(encoding=TEXT_ENCODING, text=[value]))


def _update_year(id3: ID3, tags: Tags, version: int) -> None:
    for name in _DATE_FRAMES:
        id3.delall(name)
    if tags.year is not None:
        frame_type = TDRC if version == 4 else TYER
        id3.add(frame_type(encoding=TEXT_ENCODING, text=[f"{tags.year:04d}"]))


def _update_genre(id3: ID3, tags: Tags, version: int) -> None:
    _set_text(id3, TCON, escape_genre(tags.genre) if tags.genre else None)


def _update_track(id3: ID3, tags: Tags, version: int) -> None:
    _set_text(id3, TRCK, track_text(tags.track_no, tags.track_total))


def _text_update(name: str) -> Callable[[ID3, Tags, int], None]:
    return lambda id3, tags, version: _set_text(id3, MP3_TEXT_FRAMES[name], getattr(tags, name))


# How a re-tag rewrites each field it changes, by the Tags field.
_UPDATES: dict[str, Callable[[ID3, Tags, int], None]] = {
    **{name: _text_update(name) for name in ("title", "artist", "album", "album_artist")},
    "year": _update_year,
    "genre": _update_genre,
    "track_no": _update_track,
    "track_total": _update_track,
}
