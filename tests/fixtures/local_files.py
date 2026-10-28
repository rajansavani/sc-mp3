"""Files you already have, for the re-tag tests: a copy of a test audio file with tags in it."""

import io
import shutil
from pathlib import Path

from PIL import Image

from scmp3.core import artwork, tagger
from scmp3.core.artwork import Cover
from scmp3.core.models import Tags

OLD_TAGS = Tags(
    title="Sunday Mornings (Official Audio)",
    artist="Teo Marr",
    album="Sunday Mornings",
    album_artist="Teo Marr & Friends",
    year=2019,
    genre="Soul",
    track_no=3,
    track_total=12,
    comment="https://soundcloud.com/teo-marr/sunday-mornings",
    source_url="https://soundcloud.com/teo-marr/sunday-mornings",
    source_id="soundcloud:tracks:42",
    isrc="GBUM71029604",
    label="Marr Records",
)


def make_cover(side: int = 400) -> Cover:
    """A real cover, made by artwork.process from a generated PNG."""
    buffer = io.BytesIO()
    Image.new("RGB", (side, side), (40, 90, 160)).save(buffer, "PNG")
    return artwork.process(buffer.getvalue())


def tagged_copy(audio: Path, dest: Path, tags: Tags = OLD_TAGS, cover: Cover | None = None) -> Path:
    """A copy of a test audio file with these tags written into it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(audio, dest)
    tagger.write(dest, tags, cover)
    return dest


def exclusive_handle(path: Path) -> int:
    """Open the file the way a program that shares it with nobody does. Windows only."""
    import ctypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    create_file.restype = ctypes.c_void_p
    generic_read, open_existing = 0x80000000, 3
    handle = create_file(str(path), generic_read, 0, None, open_existing, 0, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    return handle


def close_handle(handle: int) -> None:
    import ctypes

    close = ctypes.WinDLL("kernel32").CloseHandle
    close.argtypes = (ctypes.c_void_p,)
    close(handle)
