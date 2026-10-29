"""A title being cleaned up: its text, and the regions the rules have claimed in it.

Rules never edit the text. Each one claims the region it acts on, and `assembly.py` rebuilds the
title from the text and the claims that are switched on. That makes every change reversible.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

from scmp3.core.models import ChangeKind, Settings, SourceInfo
from scmp3.core.suggest.words import Wordlists

# Bracket pairs the rules look inside, with the full-width ones Japanese and Chinese titles use.
BRACKET_PAIRS = {
    "(": ")",
    "[": "]",
    "{": "}",
    "\u3010": "\u3011",
    "\uff08": "\uff09",
    "\uff3b": "\uff3d",
}
OPENERS = "".join(BRACKET_PAIRS)
CLOSERS = "".join(BRACKET_PAIRS.values())
_BRACKETS = OPENERS + CLOSERS
_ANY_BRACKET = re.escape(_BRACKETS)
# A bracket right after ":" or ";" is part of an emoticon like ":(" or ";-)", not a bracket.
NOT_EMOTICON = r"(?<![:;])(?<![:;]-)"
# A dash with a space on each side, as in "Artist - Title".
_SPACED_DASH = re.compile(r"(?<=\s)-(?=\s)")
# Innermost groups only, so "(a [b])" finds "[b]".
_GROUP = re.compile(
    "|".join(
        f"{NOT_EMOTICON}{re.escape(o)}[^{_ANY_BRACKET}]*{re.escape(c)}"
        for o, c in BRACKET_PAIRS.items()
    )
)


def names_pattern(stop: str) -> str:
    """Names after a keyword, up to a spaced dash, a bracket, a pipe, `stop` or the end."""
    ends = rf"\s+-\s|\s*[{_ANY_BRACKET}|]|\s+{stop}|\s*$"
    return rf"(?P<names>[^\s{_ANY_BRACKET}|][^{_ANY_BRACKET}|]*?)(?={ends})"


# Stands in for text a rule cut, so tidying closes up only the gaps cuts leave. It's whitespace to
# str.split and to regexes, and normalizing leaves no other whitespace but the space.
CUT = "\x1f"
# Kind of a claim that keeps its text and moves it into the version, like "(VIP Mix)".
VERSION: Literal["version"] = "version"
ClaimKind = ChangeKind | Literal["version"]
# The claims that take an artist out of the title.
ARTIST_KINDS = ("artist_from_title", "strip_redundant_artist")
# Separators and spaces trimmed off each part of a title between its dashes or pipes.
SEPARATORS = " -|/:"


@dataclass(frozen=True)
class Context:
    """What the rules may read besides the title."""

    source: SourceInfo
    settings: Settings
    words: Wordlists
    publisher: tuple[str, ...] = ()  # the publisher artists, with any feat. part cut
    uploader: str = ""  # the uploader's name without emoji


@dataclass(frozen=True)
class Claim:
    """A region of the text that one rule acts on."""

    kind: ClaimKind
    start: int
    end: int
    enabled: bool = True
    names: tuple[str, ...] = ()  # the artists it credits while it's on
    # For an artist taken from the title: where its part ends, so features inside go with it.
    part_end: int | None = None
    # Whether it started on. Text that starts off is part of the default title.
    default_on: bool = True


@dataclass(frozen=True)
class Group:
    """A bracketed part of the text, brackets included."""

    start: int
    end: int
    inner: str


@dataclass
class Draft:
    """The normalized text and the claims on it so far."""

    text: str
    claims: list[Claim] = field(default_factory=list)

    def span(self, claim: Claim) -> str:
        """The exact text a claim covers."""
        return self.text[claim.start : claim.end]

    def masked(self) -> str:
        """The text with claimed regions blanked out, so later rules skip them. Offsets stay put."""
        chars = list(self.text)
        for claim in self.claims:
            chars[claim.start : claim.end] = " " * (claim.end - claim.start)
        return "".join(chars)

    def is_free(self, start: int, end: int) -> bool:
        """Whether no claim overlaps text[start:end]."""
        return all(end <= claim.start or claim.end <= start for claim in self.claims)

    def next_claim_start(self, position: int) -> int:
        """Where the first claim at or after `position` starts, or the end of the text."""
        starts = [claim.start for claim in self.claims if claim.start >= position]
        return min(starts, default=len(self.text))

    def claim(
        self,
        kind: ClaimKind,
        start: int,
        end: int,
        *,
        enabled: bool = True,
        names: tuple[str, ...] = (),
        part_end: int | None = None,
    ) -> bool:
        """Claim text[start:end] without its outer spaces. False when that's empty or taken."""
        while start < end and self.text[start].isspace():
            start += 1
        while end > start and self.text[end - 1].isspace():
            end -= 1
        if start >= end or not self.is_free(start, end):
            return False
        self.claims.append(Claim(kind, start, end, enabled, names, part_end))
        return True

    def claim_credit(self, kind: ClaimKind, group: Group, *, names: tuple[str, ...] = ()) -> bool:
        """Claim a bracketed credit whole, or all of it but the claims that end it.

        In "(feat. Lou V (VIP Mix))" the credit takes "(feat. Lou V" and the version the rest. In
        "(feat. Lou V (prod. Hazy))" the producer credit keeps its own change.
        """
        if self.claim(kind, group.start, group.end, names=names):
            return True
        inside = [
            claim for claim in self.claims if group.start < claim.start and claim.end < group.end
        ]
        if not inside:
            return False
        last = max(inside, key=lambda claim: claim.end)
        # A version in the middle, as in "(feat. Lou V (VIP Mix) & Mara)", stays inside the credit.
        if self.text[last.end : group.end - 1].strip():
            return self._claim_around(kind, group, inside, names)
        if not self.claim(kind, group.start, min(claim.start for claim in inside), names=names):
            return False
        if last.kind != VERSION:
            # Tidying drops the closing bracket left on its own.
            return True
        # A version takes the closing bracket too, which tidying then balances.
        return self.claim(VERSION, group.end - 1, group.end)

    def _claim_around(
        self, kind: ClaimKind, group: Group, inside: list[Claim], names: tuple[str, ...]
    ) -> bool:
        """Claim a whole group around the version claims inside it, which keep their own claims.

        A title that is only the credit, like "(feat. Lou V (VIP Mix) & Mara) [FREE DL]", stays
        whole. Text other rules claimed, like the promo, doesn't count as the title.
        """
        masked = self.masked()
        if not has_words(masked[: group.start] + masked[group.end :]):
            return False
        others = [
            claim
            for claim in self.claims
            if claim not in inside and claim.start < group.end and group.start < claim.end
        ]
        if others or any(claim.kind != VERSION for claim in inside):
            return False
        self.claims.append(Claim(kind, group.start, group.end, names=names))
        return True

    def groups(self) -> list[Group]:
        """The bracketed parts nobody has claimed yet."""
        return [
            Group(match.start(), match.end(), match.group()[1:-1])
            for match in _GROUP.finditer(self.masked())
        ]

    def nested_groups(self) -> list[Group]:
        """The unclaimed bracketed parts with balanced brackets inside, like "(feat. A (UK))"."""
        masked = self.masked()
        found: list[Group] = []
        open_at: list[int] = []
        with_inner: set[int] = set()
        # Groups a stray closer breaks, as in "[feat. A (UK]", are never reported.
        broken: set[int] = set()
        for index, char in enumerate(masked):
            if char not in _BRACKETS or is_emoticon(masked, index):
                continue
            if char in OPENERS:
                if open_at:
                    with_inner.add(open_at[-1])
                open_at.append(index)
            elif open_at and BRACKET_PAIRS[masked[open_at[-1]]] == char:
                start = open_at.pop()
                if start in with_inner and start not in broken:
                    found.append(Group(start, index + 1, masked[start + 1 : index]))
            else:
                broken.update(open_at)
        return found

    def inline_matches(self, regex: re.Pattern[str]) -> Iterator[tuple[int, int, str]]:
        """Each keyword-and-names match outside claims, as (start, end, names), cut at a claim."""
        masked = self.masked()
        for match in regex.finditer(masked):
            # A keyword needs a title before it, so "feat. X" alone isn't read as a credit.
            if not has_words(self.text[: match.start()]):
                continue
            end = min(match.end(), self.next_claim_start(match.start()))
            names = self.text[match.start("names") : end].strip()
            if names:
                yield match.start(), end, names


def has_words(text: str) -> bool:
    """Whether `text` has any letter or digit."""
    return any(char.isalnum() for char in text)


def is_emoticon(text: str, index: int) -> bool:
    """Whether the bracket at `index` ends an emoticon like ":(" or ";-)"."""
    return text[:index].endswith((":", ";", ":-", ";-"))


def spaced_dashes(text: str) -> list[int]:
    """Where the spaced dashes outside brackets are. A face like "(˵ •̀ ᴗ - ˵ )" never splits."""
    pairs = bracket_pairs(text)
    return [
        match.start()
        for match in _SPACED_DASH.finditer(text)
        if not any(start < match.start() < end for start, end in pairs)
    ]


def bracket_pairs(text: str) -> list[tuple[int, int]]:
    """Where each matched pair of brackets opens and closes. Emoticons and strays don't count."""
    pairs: list[tuple[int, int]] = []
    open_at: list[int] = []
    for index, char in enumerate(text):
        if char not in _BRACKETS or is_emoticon(text, index):
            continue
        if char in OPENERS:
            open_at.append(index)
        elif open_at and BRACKET_PAIRS[text[open_at[-1]]] == char:
            pairs.append((open_at.pop(), index))
    return pairs
