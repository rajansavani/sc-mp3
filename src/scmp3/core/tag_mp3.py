"""MP3 tags: an ID3v2.3 tag of exactly the TECH_SPEC §10 frames, with no ID3v1 tag.

Call these through `tagger.write` and `tagger.read`, which check the values first.
"""

import re
import string
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPUB,
    TRCK,
    TSRC,
    TXXX,
    WOAS,
    Encoding,
    Frame,
    ID3NoHeaderError,
    ID3v1SaveOptions,
    PictureType,
    TextFrame,
)
from mutagen.mp3 import MPEGInfo

from scmp3.core.artwork import Cover
from scmp3.core.models import Tags
from scmp3.core.tag_common import file_errors, joined

SOURCE_ID_DESC = "SCMP3_SOURCE_ID"

# ID3v2.3 has no UTF-8, so text frames use UTF-16.
_TEXT_ENCODING = Encoding.UTF16

# The plain text frames, by the Tags field each one holds.
_MP3_TEXT_FRAMES: dict[str, type[TextFrame]] = {
    "title": TIT2,
    "artist": TPE1,
    "album_artist": TPE2,
    "album": TALB,
    "isrc": TSRC,
    "label": TPUB,
}

# With all ASCII punctuation safe, quote() escapes only spaces, control and non-ASCII characters.
_URL_SAFE = string.punctuation

# An ID3v1 genre reference such as "(17)" or "(RX)", maybe followed by a name that refines it.
_GENRE_REFERENCE = re.compile(r"\((\d+|RX|CR)\)(.*)", re.DOTALL)

_SPECIAL_GENRES = {"RX": "Remix", "CR": "Cover"}

# An ID3v2 header and a v2.4 footer are both 10 bytes. Footers start with "3DI".
_ID3_HEADER_BYTES = 10

_MOVE_CHUNK_BYTES = 1024 * 1024


def write_mp3(path: Path, tags: Tags, cover: Cover | None) -> None:
    """Replace the MP3's tags with an ID3v2.3 tag of the §10 frames, and remove any ID3v1 tag."""
    _check_mpeg_audio(path)
    id3 = ID3()
    for frame in _mp3_frames(tags, cover):
        id3.add(frame)
    # This turns TDRC, a v2.4 frame, into TYER. Saving as v2.3 doesn't do it by itself.
    id3.update_to_v23()
    # Added after the update, which would turn a genre like "140" into an ID3v1 genre name.
    if tags.genre:
        id3.add(TCON(encoding=_TEXT_ENCODING, text=[_escape_genre(tags.genre)]))
    with file_errors("write the MP3 tags"):
        # The new tag replaces the first old ID3v2 tag whole, so none of its frames survive.
        id3.save(path, v2_version=3, v1=ID3v1SaveOptions.REMOVE)
        _remove_stale_tags(path)


def read_mp3(path: Path) -> tuple[Tags, bytes | None]:
    """The tags and cover of an MP3, read from the same frames write_mp3 uses."""
    with file_errors("read the MP3 tags"):
        try:
            # Untranslated at first, since translating rewrites a genre like "140" as a name.
            id3 = ID3(path, translate=False)
        except ID3NoHeaderError:
            return _blank_tags(), None
        genre_frame = id3.get("TCON")
        raw_genres = list(genre_frame.text) if genre_frame else []
        from_id3v1 = id3.version < (2, 0, 0)
        # This turns v2.2 and v2.3 frames into the v2.4 ones read below, such as TYER into TDRC.
        id3.update_to_v24()
    texts = {name: _id3_text(id3, frame.__name__) for name, frame in _MP3_TEXT_FRAMES.items()}
    track_no, track_total = _parse_track(_id3_text(id3, "TRCK"))
    tags = Tags(
        title=texts["title"] or "",
        artist=texts["artist"] or "",
        album=texts["album"] or "",
        album_artist=texts["album_artist"] or "",
        year=_id3_year(id3),
        genre=joined(_genre_name(value, from_id3v1) for value in raw_genres),
        track_no=track_no,
        track_total=track_total,
        comment=_id3_comment(id3),
        source_url=_id3_url(id3),
        source_id=_id3_text(id3, f"TXXX:{SOURCE_ID_DESC}"),
        isrc=texts["isrc"],
        label=texts["label"],
    )
    return tags, _id3_cover(id3)


def _check_mpeg_audio(path: Path) -> None:
    """Refuse a file with no MPEG audio, such as an M4A named .mp3, which a tag would break."""
    # This also refuses a missing file, where mutagen would create one holding only the tag.
    with file_errors("read the MP3 audio"), path.open("rb") as file:
        MPEGInfo(file)


def _remove_stale_tags(path: Path) -> None:
    """Remove old tag bytes mutagen leaves after the new tag: a v2.4 footer, or more tags."""
    with path.open("r+b") as file:
        size = file.seek(0, 2)
        new_tag_end = _tag_end(file, 0, size)
        stale_end = new_tag_end
        while True:
            next_end = _tag_end(file, stale_end, size)
            if next_end == stale_end:
                break
            stale_end = next_end
        if stale_end > new_tag_end:
            _remove_bytes(file, new_tag_end, stale_end, size)


def _tag_end(file: BinaryIO, offset: int, file_size: int) -> int:
    """Where the ID3v2 tag or footer at `offset` ends, or `offset` when there's none."""
    file.seek(offset)
    header = file.read(_ID3_HEADER_BYTES)
    if len(header) < _ID3_HEADER_BYTES:
        return offset
    if header.startswith(b"3DI"):
        return offset + _ID3_HEADER_BYTES
    body_size = _synchsafe(header[6:10])
    if not header.startswith(b"ID3") or body_size is None:
        return offset
    footer_size = _ID3_HEADER_BYTES if header[5] & 0x10 else 0
    end = offset + _ID3_HEADER_BYTES + body_size + footer_size
    # A tag that would run past the end isn't one, so those bytes are left alone.
    return end if end <= file_size else offset


def _synchsafe(data: bytes) -> int | None:
    """An ID3 size, stored as 7 bits a byte, or None when a byte has its top bit set."""
    if any(byte & 0x80 for byte in data):
        return None
    total = 0
    for byte in data:
        total = (total << 7) | byte
    return total


def _remove_bytes(file: BinaryIO, start: int, stop: int, file_size: int) -> None:
    """Remove the bytes from `start` to `stop`, moving the rest of the file up in chunks."""
    read_at, write_at = stop, start
    while read_at < file_size:
        file.seek(read_at)
        chunk = file.read(_MOVE_CHUNK_BYTES)
        if not chunk:
            break
        file.seek(write_at)
        file.write(chunk)
        read_at += len(chunk)
        write_at += len(chunk)
    file.truncate(write_at)


def _blank_tags() -> Tags:
    """The tags of a file that has none."""
    return Tags(title="", artist="", album="", album_artist="")


def _mp3_frames(tags: Tags, cover: Cover | None) -> list[Frame]:
    """The ID3 frames for `tags` and `cover`, except the genre, leaving out empty fields."""
    frames: list[Frame] = []
    for name, frame_type in _MP3_TEXT_FRAMES.items():
        value = getattr(tags, name)
        if value:
            frames.append(frame_type(encoding=_TEXT_ENCODING, text=[value]))
    if tags.year is not None:
        frames.append(TDRC(encoding=_TEXT_ENCODING, text=[f"{tags.year:04d}"]))
    track = _track_text(tags.track_no, tags.track_total)
    if track:
        frames.append(TRCK(encoding=_TEXT_ENCODING, text=[track]))
    if tags.comment:
        frames.append(COMM(encoding=_TEXT_ENCODING, lang="eng", desc="", text=[tags.comment]))
    if tags.source_url:
        frames.append(WOAS(url=_ascii_url(tags.source_url)))
    if tags.source_id:
        frames.append(TXXX(encoding=_TEXT_ENCODING, desc=SOURCE_ID_DESC, text=[tags.source_id]))
    if cover is not None:
        # Latin-1 for the empty description, which old players read most reliably.
        picture = APIC(
            encoding=Encoding.LATIN1,
            mime="image/jpeg",
            type=PictureType.COVER_FRONT,
            desc="",
            data=cover.jpeg,
        )
        frames.append(picture)
    return frames


def _track_text(track_no: int | None, track_total: int | None) -> str | None:
    """TRCK's text: `n`, or `n/N` when the total is known."""
    if track_no is None:
        return None
    if track_total is None:
        return str(track_no)
    return f"{track_no}/{track_total}"


def _escape_genre(genre: str) -> str:
    """ID3 reads a "(" at the start as an ID3v1 genre reference, so a real one is doubled."""
    return f"({genre}" if genre.startswith("(") else genre


def _genre_name(text: str, from_id3v1: bool) -> str:
    """One TCON value as the genre it names. Only ID3v1 numbers become names, not typed ones."""
    if from_id3v1:
        return _id3v1_genre(text) or text
    if text.startswith("(("):
        return text[1:]
    match = _GENRE_REFERENCE.fullmatch(text)
    if match is None:
        return text
    reference, refinement = match.groups()
    return refinement or _SPECIAL_GENRES.get(reference) or _id3v1_genre(reference) or text


def _id3v1_genre(number: str) -> str | None:
    """The name of an ID3v1 genre number, or None when it isn't one."""
    if not number.isdecimal() or int(number) >= len(TCON.GENRES):
        return None
    return TCON.GENRES[int(number)]


def _ascii_url(url: str) -> str:
    """The URL with spaces and non-ASCII characters percent-encoded, since WOAS holds Latin-1."""
    return quote(url, safe=_URL_SAFE)


def _id3_text(id3: ID3, key: str) -> str | None:
    """The text of the frame with this key, or None when there's no such frame."""
    frame = id3.get(key)
    return joined(frame.text) if frame else None


def _id3_year(id3: ID3) -> int | None:
    """The year in TDRC, which update_to_v24 fills from a v2.3 TYER frame."""
    frame = id3.get("TDRC")
    if not frame or not frame.text:
        return None
    return frame.text[0].year


def _id3_comment(id3: ID3) -> str | None:
    """The plain comment. Other taggers add described ones, such as iTunes' iTunNORM data."""
    for frame in id3.getall("COMM"):
        if frame.desc == "":
            return joined(frame.text)
    return None


def _id3_url(id3: ID3) -> str | None:
    """The source link in WOAS."""
    frame = id3.get("WOAS")
    return frame.url if frame else None


def _id3_cover(id3: ID3) -> bytes | None:
    """The front cover, or the first picture when none is marked as the front."""
    pictures = id3.getall("APIC")
    for picture in pictures:
        if picture.type == PictureType.COVER_FRONT:
            return picture.data
    return pictures[0].data if pictures else None


def _parse_track(text: str | None) -> tuple[int | None, int | None]:
    """The track number and total from TRCK text like `3` or `3/12`."""
    if not text:
        return None, None
    number, _, total = text.partition("/")
    return _positive_int(number), _positive_int(total)


def _positive_int(text: str) -> int | None:
    """The number in `text`, or None when it's blank, not a number, or 0."""
    text = text.strip()
    if not text.isdecimal():
        return None
    return int(text) or None
