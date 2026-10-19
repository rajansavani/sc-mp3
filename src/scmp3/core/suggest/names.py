"""Comparing, deduplicating and splitting artist names."""

import re
import unicodedata
from collections.abc import Iterable

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


def name_key(name: str) -> str:
    """A name compared case-insensitively, for removing duplicates."""
    return " ".join(unicodedata.normalize("NFC", name).casefold().split())


def spacing_key(name: str) -> str:
    """A name compared ignoring case and spacing, so "juicewrld" matches "Juice WRLD"."""
    return "".join(name_key(name).split())


def channel_key(name: str) -> str:
    """A name compared ignoring case, spaces and punctuation, so "Mr Suicide Sheep" matches."""
    return "".join(char for char in name_key(name) if char.isalnum())


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
    """Split featured names like "A, B & C x D". "Lil Nas X" stays whole: its X isn't spaced."""
    return _split(text, _LIST_SEPARATORS)


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
