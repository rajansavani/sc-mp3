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
        char for char in decomposed if not unicodedata.combining(char) and not char.isspace()
    )


def has_stand_in(name: str) -> bool:
    """Whether a name has a "?" or U+FFFD, which may stand in for a lost letter."""
    return any(char in _STAND_INS for char in name)


def same_artist(name: str, other: str) -> bool:
    """Whether two spellings name one artist, ignoring case, spacing and accents.

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


def split_credit(text: str) -> list[str]:
    """Split a credit from the title on "x" and "+" only, so "Chase & Status" stays whole."""
    return _split(text, _CREDIT_SEPARATORS)


def _split(text: str, separators: re.Pattern[str]) -> list[str]:
    """Split on `separators`, then on a spaced x inside each part."""
    names: list[str] = []
    for part in separators.split(text):
        # Prefer a lowercase " x ", so "Lil Nas X x Kaia" splits in the right place.
        splitter = _SPACED_X if _SPACED_X.search(part) else _SPACED_CAPITAL_X
        names.extend(splitter.split(part))
    return dedupe(_trim(name) for name in names)


def _trim(name: str) -> str:
    """A name without stray separators or unmatched brackets, so "Drake (feat. Rihanna)" stays."""
    name = name.strip(_NAME_TRIM)
    while name and name[-1] in _CLOSERS and _count(name, _CLOSERS) > _count(name, _OPENERS):
        name = name[:-1].strip(_NAME_TRIM)
    while name and name[0] in _OPENERS and _count(name, _OPENERS) > _count(name, _CLOSERS):
        name = name[1:].strip(_NAME_TRIM)
    return name


def _count(text: str, chars: str) -> int:
    """How many of `chars` appear in `text`."""
    return sum(text.count(char) for char in chars)
