"""File and folder names that are safe on Windows, macOS and Linux, within their limits."""

import dataclasses
import re
import unicodedata
from dataclasses import dataclass
from typing import Self


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
# Combining marks, which belong with the character before them. They include the variation
# selectors and the keycap mark of emoji.
MARK_CATEGORIES = frozenset({"Mn", "Mc", "Me"})
# The joiner in emoji sequences like a family or a couple.
ZWJ = "\u200d"
SKIN_TONES = range(0x1F3FB, 0x1F400)
# Tag letters, which make the flags of England, Scotland and Wales.
TAGS = range(0xE0020, 0xE0080)
# The canonical combining class of a virama, which joins two consonants into one conjunct.
VIRAMA = 9
# Regional indicators, which make a flag in pairs.
REGIONAL_INDICATORS = range(0x1F1E6, 0x1F200)
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
        return safe_stem(name, NAME_LIMITS)
    ext = match.group()
    return safe_stem(name[: match.start()], NAME_LIMITS.less(len(ext))) + ext


def safe_stem(stem: str, limits: NameLimits, fallback: str = FALLBACK_STEM) -> str:
    """Apply every filename rule to a name without its extension, or use `fallback` if empty."""
    stem = cap_length(_clean_characters(stem), limits)
    return _avoid_windows_aliases(stem, limits) or fallback


def _clean_characters(text: str) -> str:
    """NFC, no surrogates, illegal characters as dashes, and no repeated spaces or dashes."""
    text = "".join(char for char in text if ord(char) not in SURROGATES)
    text = unicodedata.normalize("NFC", text)
    text = ILLEGAL_CHARACTERS.sub("-", text)
    text = SPACE_RUN.sub(" ", text)
    return DASH_RUN.sub("-", text)


def _trim_ends(text: str) -> str:
    return text.strip(END_JUNK)


def cap_length(stem: str, limits: NameLimits) -> str:
    """Trim the ends, cut `stem` to fit `limits`, then trim whatever the cut exposed."""
    stem = _trim_ends(stem)
    return _trim_ends(stem[: _fitting_length(stem, limits)])


def _fitting_length(stem: str, limits: NameLimits) -> int:
    """How many characters of `stem` fit `limits`, never parting a character from its marks."""
    bytes_used = units_used = 0
    end = 0
    for char in stem[: limits.characters]:
        bytes_used += len(char.encode("utf-8"))
        units_used += _decomposed_utf16_units(char)
        if bytes_used > limits.utf8_bytes or units_used > limits.utf16_units:
            break
        end += 1
    # Never part a flag, an emoji sequence or a letter from its marks.
    while 0 < end < len(stem) and _joins_previous(stem, end):
        end -= 1
    return end


def _joins_previous(text: str, index: int) -> bool:
    """Whether text[index] belongs with the character before it, so a cut there would part them."""
    char, before = text[index], text[index - 1]
    if unicodedata.category(char) in MARK_CATEGORIES or ZWJ in (char, before):
        return True
    # A letter after a virama makes a conjunct with the letter before it, as in Devanagari "क्ष".
    if unicodedata.combining(before) == VIRAMA and char.isalpha():
        return True
    if ord(char) in SKIN_TONES or ord(char) in TAGS:
        return True
    return ord(char) in REGIONAL_INDICATORS and _ends_half_a_flag(text, index)


def _ends_half_a_flag(text: str, index: int) -> bool:
    """Whether an odd run of regional indicators ends just before `index`."""
    count = 0
    while index - count - 1 >= 0 and ord(text[index - count - 1]) in REGIONAL_INDICATORS:
        count += 1
    return count % 2 == 1


def _decomposed_utf16_units(char: str) -> int:
    # HFS+ stores names decomposed, which never takes fewer units than the composed form NTFS keeps.
    return utf16_units(unicodedata.normalize("NFD", char))


def utf16_units(text: str) -> int:
    """The length of `text` in UTF-16 units, the way Windows counts names and paths."""
    # surrogatepass, because a Windows path can hold a lone surrogate.
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _avoid_windows_aliases(stem: str, limits: NameLimits) -> str:
    """Add "_" where Windows would read the name as a device, or as another file's short name."""
    device, dot, rest = stem.partition(".")
    device = device.rstrip(" ")
    if device.upper() in RESERVED_NAMES:
        # The "_" can push the name past the limits, so cap it again.
        return cap_length(f"{device}_{dot}{rest}", limits)
    if len(stem) <= SHORT_NAME_MAX_LENGTH and SHORT_NAME.fullmatch(stem):
        # Saving as KAIASO~1.mp3 would otherwise replace "Kaia Something Long.mp3".
        return f"{stem}_"
    return stem
