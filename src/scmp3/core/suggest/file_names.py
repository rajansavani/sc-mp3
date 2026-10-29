"""A title for a file with no title tag, read from its file name, as "Kaia - Song.mp3" gives
"Kaia - Song". Its "Artist - Title" split starts on, since that's how files are usually named."""

import re

from scmp3.core.models import SourceInfo

# A track number before the name, as in "01 - Song", "01. Song" or "01_Song". A digit after the
# dot makes it part of the title, as in "1.5 Degrees". iTunes writes "01 Song", but only a
# zero-padded number counts there, so "50 Cent - In Da Club" and "7 rings" stay whole.
_TRACK_NUMBER = re.compile(r"^(?:\d{1,3}\s*[._](?!\d)|\d{1,3}\s+-\s|0\d{1,2}\s)")
# A dash a track number leaves at the start, as in "01_-_Kaia_-_Song".
_LEADING_DASH = re.compile(r"^\s*-\s+")
_FOLDERS = re.compile(r"[\\/]")


def title_to_read(source: SourceInfo) -> str:
    """The title the rules read: the source's own, or a tagless file's name."""
    if not is_from_file_name(source):
        return source.title
    return name_as_title(source.url)


def is_from_file_name(source: SourceInfo) -> bool:
    """Whether the title comes from the file name, because the file has no title tag."""
    return source.kind == "file" and not source.title.strip()


def name_as_title(path: str) -> str:
    """A file name as a title: without its folder, extension and track number."""
    name = _FOLDERS.split(path)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    title = _TRACK_NUMBER.sub("", stem, count=1)
    # Some downloaders write "Kaia_-_Song", with underscores for spaces.
    if " " not in title:
        title = title.replace("_", " ")
    return _LEADING_DASH.sub("", title).strip() or stem.strip()
