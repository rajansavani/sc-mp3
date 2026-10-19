"""M4A tags: exactly the TECH_SPEC §10 MP4 atoms, plus the file's gapless playback info.

Call these through `tagger.write` and `tagger.read`, which check the values first.
"""

import re
from pathlib import Path
from typing import Any

from mutagen.mp4 import MP4, AtomDataType, MP4Cover, MP4FreeForm, MP4Tags

from scmp3.core.artwork import Cover
from scmp3.core.models import Tags
from scmp3.core.tag_common import file_errors, joined

# The plain text atoms, by the Tags field each one holds.
_M4A_TEXT_ATOMS: dict[str, str] = {
    "title": "©nam",
    "artist": "©ART",
    "album_artist": "aART",
    "album": "©alb",
    "genre": "©gen",
    "comment": "©cmt",
}

# The freeform atoms, by the Tags field each one holds. Each holds UTF-8 text.
_M4A_FREEFORM_ATOMS: dict[str, str] = {
    "source_url": "----:com.scmp3:source_url",
    "source_id": "----:com.scmp3:source_id",
    "isrc": "----:com.apple.iTunes:ISRC",
    "label": "----:com.apple.iTunes:LABEL",
}

# Atoms a re-tag keeps, since they describe the audio, not the track: iTunes' gapless info.
_M4A_KEPT_ATOMS = ("----:com.apple.iTunes:iTunSMPB",)

_YEAR = re.compile(r"\d{4}")


def write_m4a(path: Path, tags: Tags, cover: Cover | None) -> None:
    """Replace the M4A's tags with the §10 atoms, keeping only its gapless playback info."""
    atoms = _m4a_atoms(tags, cover)
    with file_errors("write the M4A tags"):
        audio = MP4(path)
        old = audio.tags or MP4Tags()
        # A new tag object, since clearing the old one keeps the atoms mutagen couldn't parse.
        audio.tags = MP4Tags()
        audio.tags.update({key: old[key] for key in _M4A_KEPT_ATOMS if key in old})
        audio.tags.update(atoms)
        audio.save()


def read_m4a(path: Path) -> tuple[Tags, bytes | None]:
    """The tags and cover of an M4A, read from the same atoms write_m4a uses."""
    with file_errors("read the M4A tags"):
        # mutagen also reads the numeric `gnre` atom other taggers write into `©gen`.
        atoms = MP4(path).tags or MP4Tags()
    texts = {name: _m4a_text(atoms, key) for name, key in _M4A_TEXT_ATOMS.items()}
    freeform = {name: _m4a_freeform(atoms, key) for name, key in _M4A_FREEFORM_ATOMS.items()}
    track_no, track_total = _m4a_track(atoms)
    tags = Tags(
        title=texts["title"] or "",
        artist=texts["artist"] or "",
        album=texts["album"] or "",
        album_artist=texts["album_artist"] or "",
        year=_parse_year(_m4a_text(atoms, "©day")),
        genre=texts["genre"],
        track_no=track_no,
        track_total=track_total,
        comment=texts["comment"],
        source_url=freeform["source_url"],
        source_id=freeform["source_id"],
        isrc=freeform["isrc"],
        label=freeform["label"],
    )
    covers = atoms.get("covr") or []
    return tags, bytes(covers[0]) if covers else None


def _m4a_atoms(tags: Tags, cover: Cover | None) -> dict[str, list[Any]]:
    """The MP4 atoms for `tags` and `cover`, leaving out empty fields."""
    atoms: dict[str, list[Any]] = {}
    for name, key in _M4A_TEXT_ATOMS.items():
        value = getattr(tags, name)
        if value:
            atoms[key] = [value]
    for name, key in _M4A_FREEFORM_ATOMS.items():
        value = getattr(tags, name)
        if value:
            atoms[key] = [MP4FreeForm(value.encode("utf-8"), dataformat=AtomDataType.UTF8)]
    if tags.year is not None:
        atoms["©day"] = [f"{tags.year:04d}"]
    if tags.track_no is not None:
        # The second number is the total; 0 means unknown.
        atoms["trkn"] = [(tags.track_no, tags.track_total or 0)]
    if cover is not None:
        atoms["covr"] = [MP4Cover(cover.jpeg, imageformat=MP4Cover.FORMAT_JPEG)]
    return atoms


def _parse_year(text: str | None) -> int | None:
    """The year at the start of a date such as `2024` or `2024-05-01`."""
    match = _YEAR.match(text or "")
    return int(match[0]) if match else None


def _m4a_text(atoms: MP4Tags, key: str) -> str | None:
    """The text of the atom with this key, or None when there's no such atom."""
    values = atoms.get(key)
    return joined(values) if values else None


def _m4a_track(atoms: MP4Tags) -> tuple[int | None, int | None]:
    """The track number and total from `trkn`, where 0 means unknown."""
    values = atoms.get("trkn")
    if not values:
        return None, None
    number, total = values[0]
    return number or None, total or None


def _m4a_freeform(atoms: MP4Tags, key: str) -> str | None:
    """The text in a freeform atom, such as ours for the source id."""
    values = atoms.get(key)
    if not values:
        return None
    # Another tagger may have stored bytes that aren't UTF-8 under the same name.
    return bytes(values[0]).decode("utf-8", errors="replace")
