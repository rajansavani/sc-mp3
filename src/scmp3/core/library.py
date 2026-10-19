"""Library rules: what a saved file is called and where it goes, even when the name is taken.

placing.py gets the file there safely.
"""

import dataclasses
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from scmp3.core.errors import ErrorKind, ScmpError
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
TRACK_PREFIX = "{track:02} "
# A bracketed part of a title, such as the artists a title style adds: " (feat. Lou V)".
BRACKETED_PART = re.compile(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])")

# The temp names placing.py makes. They live here, since the path limit keeps room for them.
PARTIAL_SUFFIX = ".partial"
RETAG_SUFFIX = ".retag.tmp"
# The most a temp name adds to a name: a leading dot and .retag.tmp.
TEMP_NAME_EXTRA = len(".") + len(RETAG_SUFFIX)


@dataclass(frozen=True)
class NameLimits:
    """The most a file or folder name may hold, counted three ways."""

    characters: int
    utf8_bytes: int  # ext4 and APFS allow 255
    utf16_units: int  # NTFS allows 255, and so does HFS+, which counts them decomposed

    def less(self, ascii_length: int) -> Self:
        """These limits with room kept for ASCII text, such as an extension."""
        return type(self)(
            self.characters - ascii_length,
            self.utf8_bytes - ascii_length,
            self.utf16_units - ascii_length,
        )

    def with_utf16_at_most(self, units: int) -> Self:
        return dataclasses.replace(self, utf16_units=min(self.utf16_units, units))


# For the whole name, extension included. Under 255, so the temp names fit too.
NAME_LIMITS = NameLimits(characters=150, utf8_bytes=240, utf16_units=240)
# Windows paths stop at 259 UTF-16 units unless long paths are turned on, which is off by default.
MAX_PATH_UNITS: int | None = 259 if sys.platform == "win32" else None
# A name is never shortened below this to fit the path.
MIN_STEM_UNITS = 20

FALLBACK_STEM = "Untitled"
# Illegal on Windows, plus every control character.
ILLEGAL_CHARACTERS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f-\x9f]')
# Half of a surrogate pair, as a cut-off JavaScript string can send. No filesystem takes one.
SURROGATES = range(0xD800, 0xE000)
SPACE_RUN = re.compile(r"\s+")
# A run of dashes, even with spaces between them, as in "A - - B".
DASH_RUN = re.compile(r"-(?:\s*-)+")
# Windows rejects trailing dots and spaces, a leading dot hides a file on macOS and Linux,
# and empty fields leave dangling dashes.
END_JUNK = " .-"
# A short run of letters and digits after the last dot, such as .mp3.
EXTENSION = re.compile(r"\.[0-9A-Za-z]{1,5}$")
# Combining marks, which belong with the character before them.
MARK_CATEGORIES = frozenset({"Mn", "Mc", "Me"})
# Windows device names, reserved even with an extension. Python's ntpath.isreserved list, plus
# COM0 and LPT0, which Microsoft's naming rules and OneDrive also refuse.
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{digit}" for digit in "0123456789¹²³"}
    | {f"LPT{digit}" for digit in "0123456789¹²³"}
)
# An NTFS short name such as KAIASO~1, which Windows resolves to another file: at most
# 8 characters, with no spaces or dots, ending in ~ and a number.
SHORT_NAME = re.compile(r"[^ .]*~[0-9]+")
SHORT_NAME_MAX_LENGTH = 8


def sanitize(name: str) -> str:
    """Make a file or folder name safe on Windows, macOS and Linux, keeping its extension."""
    # Cleaning before looking for the extension means junk after it can't hide it.
    name = _clean_characters(name).rstrip(END_JUNK)
    match = EXTENSION.search(name)
    if match is None:
        return _safe_stem(name, NAME_LIMITS)
    ext = match.group()
    return _safe_stem(name[: match.start()], NAME_LIMITS.less(len(ext))) + ext


def _safe_stem(stem: str, limits: NameLimits) -> str:
    """Apply every filename rule to a name without its extension."""
    stem = _cap_length(_clean_characters(stem), limits)
    return _avoid_windows_aliases(stem, limits) or FALLBACK_STEM


def _clean_characters(text: str) -> str:
    """NFC, no surrogates, illegal characters as dashes, and no repeated spaces or dashes."""
    text = "".join(char for char in text if ord(char) not in SURROGATES)
    text = unicodedata.normalize("NFC", text)
    text = ILLEGAL_CHARACTERS.sub("-", text)
    text = SPACE_RUN.sub(" ", text)
    return DASH_RUN.sub("-", text)


def _trim_ends(text: str) -> str:
    return text.strip(END_JUNK)


def _cap_length(stem: str, limits: NameLimits) -> str:
    """Trim the ends, cut `stem` to fit `limits`, then trim whatever the cut exposed."""
    stem = _trim_ends(stem)
    return _trim_ends(stem[: _fitting_length(stem, limits)])


def _fitting_length(stem: str, limits: NameLimits) -> int:
    """How many characters of `stem` fit `limits`, never parting a character from its marks."""
    utf8_bytes = utf16_units = 0
    end = 0
    for char in stem[: limits.characters]:
        utf8_bytes += len(char.encode("utf-8"))
        utf16_units += _decomposed_utf16_units(char)
        if utf8_bytes > limits.utf8_bytes or utf16_units > limits.utf16_units:
            break
        end += 1
    # Python counts a character outside the BMP as one, so only marks can be split from a base.
    while 0 < end < len(stem) and unicodedata.category(stem[end]) in MARK_CATEGORIES:
        end -= 1
    return end


def _decomposed_utf16_units(char: str) -> int:
    # HFS+ stores names decomposed, which never takes fewer units than the composed form NTFS keeps.
    return _utf16_units(unicodedata.normalize("NFD", char))


def _utf16_units(text: str) -> int:
    # surrogatepass, because a Windows path can hold a lone surrogate.
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _avoid_windows_aliases(stem: str, limits: NameLimits) -> str:
    """Add "_" where Windows would read the name as a device, or as another file's short name."""
    device, dot, rest = stem.partition(".")
    device = device.rstrip(" ")
    if device.upper() in RESERVED_NAMES:
        # The "_" can push the name past the limits, so cap it again.
        return _cap_length(f"{device}_{dot}{rest}", limits)
    if len(stem) <= SHORT_NAME_MAX_LENGTH and SHORT_NAME.fullmatch(stem):
        # Saving as KAIASO~1.mp3 would otherwise replace "Kaia Something Long.mp3".
        return f"{stem}_"
    return stem


def render_filename(template: str, tags: Tags) -> str:
    """Fill in a filename template from the tags and make it safe. The result has no extension."""
    return _render(template, tags, NAME_LIMITS)


def _render(template: str, tags: Tags, limits: NameLimits) -> str:
    rendered = TOKEN_PATTERN.sub(lambda match: _token_value(match, tags), template)
    return _safe_stem(EMPTY_BRACKETS.sub("", rendered), limits)


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
    the path within MAX_PATH, and a library folder too deep for any name raises ScmpError(disk).
    """
    folder = library_dir
    template = filename_template(settings, style)
    album_track = is_album_track(
        tags,
        in_album_set=in_album_set,
        album_from_singles_setting=album_from_singles_setting,
        source_titles=source_titles,
    )
    # Album subfolders are for albums only, so singles stay at the top of the library.
    if settings.album_subfolders and album_track:
        folder = library_dir / render_filename(ALBUM_FOLDER_TEMPLATE, tags)
        template = _with_track_prefix(template)
    # Callers may pass "mp3" or ".mp3".
    ext = "." + ext.lstrip(".")
    limits = NAME_LIMITS.less(len(ext))
    stem = _render(template, tags, limits)
    room = _path_room(folder, ext)
    if room is not None and _utf16_units(stem) > room:
        if room < MIN_STEM_UNITS:
            raise ScmpError(ErrorKind.disk, "the library folder path is too long for any file name")
        stem = _render(template, tags, limits.with_utf16_at_most(room))
    return folder / f"{stem}{ext}"


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
    used = _utf16_units(str(folder.absolute())) + len(os.sep) + len(ext) + TEMP_NAME_EXTRA
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
    stem = _cap_length(path.stem, limits)
    return path.with_name(f"{stem}{suffix}{path.suffix}")
