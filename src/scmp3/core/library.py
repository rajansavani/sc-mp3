"""Library rules: what a saved file is called and where it goes, even when the name is taken.

filenames.py makes each name safe, and placing.py gets the file there safely.
"""

import dataclasses
import logging
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.filenames import NAME_LIMITS, NameLimits, cap_length, safe_stem, utf16_units
from scmp3.core.models import CollisionPolicy, Settings, Tags, TitleStyle

# What the user can answer when asked about a file that already exists.
CollisionChoice = Literal["keep_both", "replace", "skip"]
AskFn = Callable[[Path], CollisionChoice]

# Each filename token and the Tags field it reads. A new token is one more line here.
TOKEN_FIELDS: dict[str, str] = {
    "artist": "artist",
    "title": "title",
    "album": "album",
    "album_artist": "album_artist",
    "year": "year",
    "track": "track_no",
}
# A token, optionally zero-padded to a width, as in {track:02}.
TOKEN_PATTERN = re.compile(r"\{(?P<name>[a-z_]+)(?::0(?P<width>[1-9]))?\}")
# Left behind by a token with no value, as in "({year})".
EMPTY_BRACKETS = re.compile(r"\(\s*\)|\[\s*\]")

DEFAULT_FILENAME_TEMPLATE = "{artist} - {title}"
# all_in_title already puts the artists in the title, so the filename doesn't repeat them.
STYLE_FILENAME_TEMPLATES: dict[TitleStyle, str] = {"all_in_title": "{title}"}
ALBUM_FOLDER_TEMPLATE = "{album_artist} - {album}"
# Inside the artist's own folder, the album folder doesn't repeat the artist.
ALBUM_IN_ARTIST_FOLDER_TEMPLATE = "{album}"
# The artist folder of a track whose album artist is empty once it's made safe.
UNKNOWN_ARTIST = "Unknown artist"
TRACK_PREFIX = "{track:02} "
# A bracketed part of a title, such as the artists a title style adds: " (feat. Lou V)".
BRACKETED_PART = re.compile(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])")

# The temp names placing.py makes. They live here, since the path limit keeps room for them.
PARTIAL_SUFFIX = ".partial"
RETAG_SUFFIX = ".retag.tmp"
# The most a temp name adds to a name: a leading dot and .retag.tmp.
TEMP_NAME_EXTRA = len(".") + len(RETAG_SUFFIX)


# Artist folders are cut shorter than file names, since they add to every path inside them.
ARTIST_FOLDER_LIMITS = dataclasses.replace(NAME_LIMITS, characters=100)
# Windows paths stop at 259 UTF-16 units unless long paths are turned on, which is off by default.
MAX_PATH_UNITS: int | None = 259 if sys.platform == "win32" else None
# A name is never shortened below this to fit the path.
MIN_STEM_UNITS = 20

log = logging.getLogger(__name__)


def render_filename(template: str, tags: Tags) -> str:
    """Fill in a filename template from the tags and make it safe. The result has no extension."""
    return _render(template, tags, NAME_LIMITS)


def _render(template: str, tags: Tags, limits: NameLimits) -> str:
    rendered = TOKEN_PATTERN.sub(lambda match: _token_value(match, tags), template)
    return safe_stem(EMPTY_BRACKETS.sub("", rendered), limits)


def _token_value(match: re.Match[str], tags: Tags) -> str:
    """The text for one token, or the token as written when it isn't one we know."""
    field_name = TOKEN_FIELDS.get(match["name"])
    if field_name is None:
        return match.group()
    value = getattr(tags, field_name)
    if value is None:
        return ""
    if isinstance(value, int) and match["width"]:
        return str(value).zfill(int(match["width"]))
    return str(value)


def style_filename_template(style: TitleStyle) -> str:
    """The file name template a title style uses when the settings have none of their own."""
    return STYLE_FILENAME_TEMPLATES.get(style, DEFAULT_FILENAME_TEMPLATE)


def filename_template(settings: Settings, style: TitleStyle | None = None) -> str:
    """The template from the settings, or the title style's default when none is set."""
    if settings.filename_template and settings.filename_template.strip():
        return settings.filename_template
    return style_filename_template(style or settings.title_style)


def _comparable(text: str) -> str:
    return " ".join(text.split()).casefold()


def _forms(text: str) -> set[str]:
    """The text as it is and without each of its bracketed parts in turn, ready to compare."""
    forms = {_comparable(text)}
    for match in BRACKETED_PART.finditer(text):
        forms.add(_comparable(text[: match.start()] + text[match.end() :]))
    return forms


def is_album_track(
    tags: Tags,
    *,
    in_album_set: bool = False,
    album_from_singles_setting: bool = False,
    source_titles: Sequence[str] = (),
) -> bool:
    """Whether a track is part of a real album, rather than a single with an album tag.

    A track from a set in album mode is one. A track whose album came from the album-for-singles
    setting isn't, whatever its title looks like. Any other album, from the source or typed by
    hand, is a single's when a form of it matches a form of the title, the base title or the
    source's raw title, which `source_titles` holds. A form is the text, or the text without one
    bracketed part, such as "(feat. Lou V)" or "[FREE DL]".
    """
    if not _comparable(tags.album):
        return False
    if in_album_set:
        return True
    if album_from_singles_setting:
        return False
    title_forms = set().union(*(_forms(title) for title in [tags.title, *source_titles]))
    return _forms(tags.album).isdisjoint(title_forms)


def plan_destination(
    library_dir: Path,
    tags: Tags,
    settings: Settings,
    ext: str,
    *,
    style: TitleStyle | None = None,
    in_album_set: bool = False,
    album_from_singles_setting: bool = False,
    source_titles: Sequence[str] = (),
) -> Path:
    """Where a file with these tags goes, before checking whether the name is taken.

    `style` is the track's own title style, when it differs from the setting. The other keywords
    say where the album came from, for `is_album_track`. On Windows the name is shortened to keep
    the path within MAX_PATH, and a folder too deep for any name raises ScmpError(disk). It never
    reads the disk, so previews can call it freely; `reuse_folders_by_case` does that when saving.
    """
    # Album subfolders are for albums only, so singles never get one.
    in_album_folder = settings.album_subfolders and is_album_track(
        tags,
        in_album_set=in_album_set,
        album_from_singles_setting=album_from_singles_setting,
        source_titles=source_titles,
    )
    folder = _track_folder(library_dir, tags, settings, in_album_folder=in_album_folder)
    template = filename_template(settings, style)
    if in_album_folder:
        template = _with_track_prefix(template)
    # Callers may pass "mp3" or ".mp3".
    ext = "." + ext.lstrip(".")
    limits = NAME_LIMITS.less(len(ext))
    stem = _render(template, tags, limits)
    room = _path_room(folder, ext)
    if room is not None and utf16_units(stem) > room:
        if room < MIN_STEM_UNITS:
            raise ScmpError(ErrorKind.disk, "the library folder path is too long for any file name")
        stem = _render(template, tags, limits.with_utf16_at_most(room))
    return folder / f"{stem}{ext}"


def _track_folder(
    library_dir: Path, tags: Tags, settings: Settings, *, in_album_folder: bool
) -> Path:
    """The folder a track goes in: the library, its artist's folder, its album's, or both."""
    folder = library_dir
    album_template = ALBUM_FOLDER_TEMPLATE
    if settings.artist_folders:
        folder = folder / _artist_folder_name(tags.album_artist)
        album_template = ALBUM_IN_ARTIST_FOLDER_TEMPLATE
    if in_album_folder:
        folder = folder / render_filename(album_template, tags)
    return folder


def _artist_folder_name(album_artist: str) -> str:
    """An album artist made safe as a folder name, shorter than a file name and never empty."""
    return safe_stem(album_artist, ARTIST_FOLDER_LIMITS, fallback=UNKNOWN_ARTIST)


def reuse_folders_by_case(path: Path, library_dir: Path) -> Path:
    """`path` with each of its folders in the library swapped for one there that differs by case.

    This lists folders on disk, so it runs when saving, just before the collision check.
    plan_destination never reads the disk.
    """
    folder = library_dir
    for name in path.parent.relative_to(library_dir).parts:
        folder = folder / folder_name_to_reuse(name, _folder_names(folder))
    return folder / path.name


def folder_name_to_reuse(name: str, existing: Sequence[str]) -> str:
    """`name` if it's in `existing`, else the first one there that matches it ignoring case."""
    # On Linux, names are case-sensitive, and "KAIA" would otherwise sit next to "Kaia".
    if name in existing:
        return name
    wanted = _caseless(name)
    for candidate in existing:
        if _caseless(candidate) == wanted:
            return candidate
    return name


def _folder_names(parent: Path) -> list[str]:
    """The names of the folders in `parent`, sorted, or none when it can't be listed."""
    try:
        with os.scandir(parent) as entries:
            return sorted(entry.name for entry in entries if entry.is_dir())
    except (FileNotFoundError, NotADirectoryError):
        # Nothing has been saved there yet.
        return []
    except OSError as error:
        # Moving the file reports a folder it can't use, so here the planned name is good enough.
        log.warning("Couldn't look for a folder to reuse: %s", error.strerror or error)
        return []


def _caseless(name: str) -> str:
    """`name` ready to compare ignoring case, whatever Unicode form the disk gave it in."""
    # Unicode's canonical caseless match: decompose, fold the case, then decompose again.
    decomposed = unicodedata.normalize("NFD", name)
    return unicodedata.normalize("NFD", decomposed.casefold())


def _with_track_prefix(template: str) -> str:
    """Put the track number in front, as "03 ", unless the template already has it."""
    if "{track" in template:
        return template
    return TRACK_PREFIX + template


def _path_room(folder: Path, ext: str) -> int | None:
    """The UTF-16 units left for a name in `folder` under MAX_PATH, or None without a limit."""
    if MAX_PATH_UNITS is None:
        return None
    # The separator and the longest temp name need room too.
    used = utf16_units(str(folder.absolute())) + len(os.sep) + len(ext) + TEMP_NAME_EXTRA
    return MAX_PATH_UNITS - used


def resolve_collision(path: Path, policy: CollisionPolicy, ask: AskFn | None = None) -> Path | None:
    """The path to save to when `path` may be taken, or None to skip saving.

    Under the "ask" policy, `ask` gets the taken path and returns the user's choice.
    """
    if not path.exists():
        return path
    choice = _ask(path, ask) if policy == "ask" else policy
    if choice == "replace":
        return path
    if choice == "keep_both":
        return _next_free_name(path)
    if choice == "skip":
        return None
    raise ScmpError(ErrorKind.internal, f"unknown collision choice {choice!r}")


def _ask(path: Path, ask: AskFn | None) -> str:
    if ask is None:
        raise ScmpError(ErrorKind.internal, "the ask policy needs an ask callback")
    return ask(path)


def _next_free_name(path: Path) -> Path:
    """The first free name of "Song (2).mp3", "Song (3).mp3" and so on."""
    number = 2
    candidate = _numbered_name(path, number)
    while candidate.exists():
        number += 1
        candidate = _numbered_name(path, number)
    return candidate


def _numbered_name(path: Path, number: int) -> Path:
    suffix = f" ({number})"
    # The number counts toward the limits, so a long name gets shorter to make room.
    limits = NAME_LIMITS.less(len(path.suffix) + len(suffix))
    room = _path_room(path.parent, path.suffix)
    if room is not None:
        limits = limits.with_utf16_at_most(max(room - len(suffix), MIN_STEM_UNITS))
    stem = cap_length(path.stem, limits)
    return path.with_name(f"{stem}{suffix}{path.suffix}")
