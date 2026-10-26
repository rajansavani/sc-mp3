"""Comparing, deduplicating and splitting artist names."""

import re
import unicodedata
from collections.abc import Callable, Iterable

# Featured names split on these. Never on " and ", because of bands like "Simon and Garfunkel".
_LIST_SEPARATORS = re.compile(r"\s*(?:,|&|\+)\s*")
# A credit taken from the title splits only on "+" and a spaced x, so "Chase & Status" stays whole.
_CREDIT_SEPARATORS = re.compile(r"\s*\+\s*")
_SPACED_X = re.compile("\\s+[x\u00d7]\\s+")
_SPACED_CAPITAL_X = re.compile(r"\s+X\s+")
# Stray characters trimmed off each name. Brackets go only when they're unmatched.
_NAME_TRIM = " ,;:-|"
_OPENERS = "([{\u3010\uff08\uff3b"
_CLOSERS = ")]}\u3011\uff09\uff3d"
# The selectors that ask for a symbol as text or as emoji, which don't change which symbol it is.
_STYLE_SELECTORS = "\ufe0e\ufe0f"
# What a source puts where it lost a letter it couldn't encode, as in "Michael Bubl?".
_STAND_INS = "?\ufffd"
# A bracketed group with no brackets inside, held whole while featured names are split.
_INNER_GROUP = re.compile(
    "|".join(
        f"{re.escape(o)}[^{re.escape(_OPENERS + _CLOSERS)}]*{re.escape(c)}"
        for o, c in zip(_OPENERS, _CLOSERS, strict=True)
    )
)
# Stands in for a held bracket group while names are split.
_HELD_GROUP = re.compile("\x01([0-9]+)\x01")
# Symbols that join artists in one credit, as in "Kaia & Lou V" or "Kaia x Lou V".
JOINERS = "\\s*[,&+]\\s*|\\s+[xX\u00d7]\\s+"
_COMMA = re.compile(r"\s*,\s*")
# The "&" or "and" that ends a comma list, as in "a, b & c" or "a, b, and c".
_LIST_END = re.compile(r"(?:^|\s+)(?:&|and)\s+", re.IGNORECASE)
# Stands in for a name held whole while a publisher list is split.
_HELD_NAME = re.compile("\x02([0-9]+)\x02")


def name_key(name: str) -> str:
    """A name compared case-insensitively, for removing duplicates."""
    return " ".join(unicodedata.normalize("NFC", name).casefold().split())


def channel_key(name: str) -> str:
    """A name compared ignoring case, spaces and punctuation, so "Mr Suicide Sheep" matches."""
    return "".join(char for char in name_key(name) if char.isalnum())


def accent_key(name: str) -> str:
    """A name compared ignoring case, spacing and accents, so "Bublé" matches "Buble"."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    return "".join(
        char
        for char in decomposed
        if not unicodedata.combining(char) and not char.isspace() and char not in _STYLE_SELECTORS
    )


def has_stand_in(name: str) -> bool:
    """Whether a name has a "?" or U+FFFD, which may stand in for a lost letter."""
    return any(char in _STAND_INS for char in name)


def same_artist(name: str, other: str) -> bool:
    """Whether two spellings name one artist, ignoring case, spacing, accents, and symbols at the
    edges, so "Axurya ✧" is "Axurya" and the same person is never listed twice.
    """
    if same_spelling(name, other):
        return True
    bare, other_bare = bare_name(name), bare_name(other)
    if not bare or not other_bare or (bare, other_bare) == (name, other):
        return False
    return same_spelling(bare, other_bare)


def same_spelling(name: str, other: str) -> bool:
    """Whether two spellings match ignoring case, spacing and accents, symbols included.

    A stand-in matches any one letter, so "Bubl?" matches both "Buble" and "Bublé".
    """
    first, second = accent_key(name), accent_key(other)
    if first == second:
        return True
    if len(first) != len(second):
        return False
    pairs = list(zip(first, second, strict=True))
    if not all(a == b or _stands_in(a, b) or _stands_in(b, a) for a, b in pairs):
        return False
    # A name of nothing but stand-ins could be anyone.
    return any(a == b and a not in _STAND_INS for a, b in pairs)


def bare_name(name: str) -> str:
    """A name without the spaces and symbols at its edges, like the stars in "★ Kaia ★"."""
    start, end = inside_decorations(name, 0, len(name))
    return name[start:end]


def inside_decorations(text: str, start: int, end: int) -> tuple[int, int]:
    """`start` and `end` moved inward past spaces, decorating symbols and the marks on them."""
    while start < end and (text[start].isspace() or is_decoration(text[start])):
        start += 1
        while start < end and _is_mark(text[start]):
            start += 1
    while end > start:
        before = end - 1
        while before > start and _is_mark(text[before]):
            before -= 1
        if not (text[before].isspace() or is_decoration(text[before])):
            break
        end = before
    return start, end


def is_decoration(char: str) -> bool:
    """Whether a character is a symbol like ✧ or ♡. An ASCII one, like the "$" in "$NOT", isn't."""
    return not char.isascii() and unicodedata.category(char) in ("So", "Sk", "Sm")


def _is_mark(char: str) -> bool:
    """Whether a character is a combining mark, which belongs to the character before it."""
    return unicodedata.category(char) in ("Mn", "Mc", "Me")


def _stands_in(char: str, letter: str) -> bool:
    """Whether `char` is a stand-in where the other spelling has `letter`."""
    return char in _STAND_INS and letter.isalpha()


def any_same_artist(names: Iterable[str], others: Iterable[str]) -> bool:
    """Whether any of `names` is one of `others`, spelled the same or nearly."""
    others = list(others)
    return any(same_artist(name, other) for name in names for other in others)


def preferred_spelling(first: str, second: str) -> str:
    """Of two spellings of one artist, the first, unless it lost a letter the second has."""
    if has_stand_in(first) and not has_stand_in(second):
        return second
    return first


def fullest_spelling(first: str, second: str) -> str:
    """Of two spellings of one artist, the one without a lost letter, then with accents.

    For names no publisher spells, so "Zoe" and "Zoë" keep "Zoë". A tie keeps the first.
    """
    if has_stand_in(first) != has_stand_in(second):
        return second if has_stand_in(first) else first
    if _has_accents(second) and not _has_accents(first):
        return second
    return first


def _has_accents(name: str) -> bool:
    """Whether a name has a letter with an accent, like the "ë" in "Zoë"."""
    return any(unicodedata.combining(char) for char in unicodedata.normalize("NFD", name))


def dedupe_artists(
    names: Iterable[str], prefer: Callable[[str, str], str] = preferred_spelling
) -> list[str]:
    """The names without empty ones, counting near spellings of one artist once.

    `prefer` picks the spelling to keep from the one kept so far and a later one.
    """
    kept: list[str] = []
    for name in (name.strip() for name in names):
        if not name:
            continue
        same = [index for index, other in enumerate(kept) if same_artist(other, name)]
        if same:
            kept[same[0]] = prefer(kept[same[0]], name)
        else:
            kept.append(name)
    return kept


def joined_names(text: str) -> list[str]:
    """The names a credit joins with ",", "&", "+" or a spaced x, for matching only."""
    return [part for part in re.split(JOINERS, text) if part.strip()]


def comma_parts(name: str) -> list[str]:
    """The names a string lists with commas, for matching only: "Rival, Cadmium" gives two.

    The string itself is never split, so "Tyler, The Creator" stays one artist.
    """
    parts = [part.strip() for part in name.split(",")]
    if len(parts) < 2:
        return []
    return [part for part in parts if part]


def dedupe(names: Iterable[str]) -> list[str]:
    """The names without empty ones and without repeats, keeping the first spelling."""
    seen: set[str] = set()
    kept: list[str] = []
    for name in names:
        key = name_key(name)
        if key and key not in seen:
            seen.add(key)
            kept.append(name.strip())
    return kept


def split_names(text: str) -> list[str]:
    """Split featured names like "A, B & C x D". "Lil Nas X" stays whole: its X isn't spaced.

    Brackets stay whole, so "Lou V (London, UK)" is one name.
    """
    held: list[str] = []

    def hold(match: re.Match[str]) -> str:
        held.append(match.group())
        return f"\x01{len(held) - 1}\x01"

    # Innermost first, again and again, so a bracket inside a bracket is held too.
    while _INNER_GROUP.search(text):
        text = _INNER_GROUP.sub(hold, text)
    names = [_release(name, held) for name in _split(text, _LIST_SEPARATORS)]
    return dedupe(names)


def _release(text: str, held: list[str]) -> str:
    """`text` with each held bracket group put back, inside ones too."""
    while _HELD_GROUP.search(text):
        text = _HELD_GROUP.sub(lambda match: held[int(match[1])], text)
    return text


def split_artist_list(text: str, whole_names: Iterable[str] = ()) -> list[str]:
    """The names a publisher's free-text artist field lists, as in "gunk, zayok, edyn".

    It splits on commas and a spaced x, and on "&" or "and" only where they end a comma list, so
    a lone "Chase & Status" stays whole. `whole_names`, like "Tyler, The Creator", never split.
    """
    held: list[str] = []

    def hold(match: re.Match[str]) -> str:
        held.append(match.group())
        return f"\x02{len(held) - 1}\x02"

    for name in sorted(whole_names, key=len, reverse=True):
        text = re.sub(_whole_name_pattern(name), hold, text, flags=re.IGNORECASE)
    parts = _COMMA.split(text)
    if len(parts) > 1:
        parts[-1:] = _split_list_end(parts[-1])
    names = [name for part in parts for name in _split(part, _COMMA, capital_x=False)]
    return dedupe_artists(_HELD_NAME.sub(lambda match: held[int(match[1])], name) for name in names)


def _whole_name_pattern(name: str) -> str:
    """A pattern for a name never split, with "&" and "and" alike and any space after a comma."""
    words = [
        "(?:&|and)" if word.casefold() in ("&", "and") else re.escape(word) for word in name.split()
    ]
    pattern = r"\s+".join(words).replace(r",\s+", r",\s*")
    return rf"(?<!\w){pattern}(?!\w)"


def _split_list_end(last: str) -> list[str]:
    """The last part of a comma list, split at the "&" or "and" before its final name."""
    ends = list(_LIST_END.finditer(last))
    if not ends:
        return [last]
    end = ends[-1]
    return [last[: end.start()], last[end.end() :]]


def split_credit(text: str) -> list[str]:
    """Split a credit from the title on "x" and "+" only, so "Chase & Status" stays whole."""
    return _split(text, _CREDIT_SEPARATORS)


def _split(text: str, separators: re.Pattern[str], *, capital_x: bool = True) -> list[str]:
    """Split on `separators`, then on a spaced x inside each part.

    Without `capital_x`, a spaced capital X splits only names in capitals, so "Brand X Music" stays.
    """
    names: list[str] = []
    for part in separators.split(text):
        splitter = _x_splitter(part, capital_x=capital_x)
        names.extend(splitter.split(part) if splitter else [part])
    return dedupe(_trim(name) for name in names)


def _x_splitter(part: str, *, capital_x: bool) -> re.Pattern[str] | None:
    """The spaced x that splits `part`, or None when nothing does."""
    # A name spelled out letter by letter, like "j x s t z e n", has its x as a letter.
    if all(len(word) == 1 for word in part.split()):
        return None
    # Prefer a lowercase " x ", so "Lil Nas X x Kaia" splits in the right place.
    if _SPACED_X.search(part):
        return _SPACED_X
    return _SPACED_CAPITAL_X if capital_x or part.isupper() else None


def _trim(name: str) -> str:
    """A name without stray separators, invisible edges or unmatched brackets.

    "Drake (feat. Rihanna)" keeps its brackets.
    """
    name = _trim_edges(name)
    while name and name[-1] in _CLOSERS and _count(name, _CLOSERS) > _count(name, _OPENERS):
        name = _trim_edges(name[:-1])
    while name and name[0] in _OPENERS and _count(name, _OPENERS) > _count(name, _CLOSERS):
        name = _trim_edges(name[1:])
    return name


def _trim_edges(name: str) -> str:
    """`name` without separators, spaces or invisible characters at its edges."""
    while True:
        trimmed = strip_invisible(name.strip(_NAME_TRIM))
        if trimmed == name:
            return name
        name = trimmed


def strip_invisible(text: str) -> str:
    """`text` without spaces and invisible format characters, like U+200E, at its edges."""
    start, end = 0, len(text)
    while start < end and (text[start].isspace() or _is_invisible(text[start])):
        start += 1
    while end > start and (text[end - 1].isspace() or _is_invisible(text[end - 1])):
        end -= 1
    return text[start:end]


def _is_invisible(char: str) -> bool:
    """Whether a character is an invisible format character. Tag letters, as in flags, aren't."""
    tags = "\U000e0020" <= char <= "\U000e007f"
    return unicodedata.category(char) == "Cf" and not tags


def _count(text: str, chars: str) -> int:
    """How many of `chars` appear in `text`."""
    return sum(text.count(char) for char in chars)
