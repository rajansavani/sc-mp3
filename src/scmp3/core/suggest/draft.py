"""A title being cleaned up: its text, and the regions the rules have claimed in it.

Rules never edit the text. Each one claims the region it acts on, and the title is rebuilt from
the text and the claims that are switched on. That is what makes every change reversible.
"""

import re
from collections.abc import Callable, Iterator
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
        # Only claims with nothing after them but the closing bracket.
        if self.text[last.end : group.end - 1].strip():
            return False
        if not self.claim(kind, group.start, min(claim.start for claim in inside), names=names):
            return False
        if last.kind != VERSION:
            # Tidying drops the closing bracket left on its own.
            return True
        # A version takes the closing bracket too, which tidying then balances.
        return self.claim(VERSION, group.end - 1, group.end)

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
    pairs = _bracket_pairs(text)
    return [
        match.start()
        for match in _SPACED_DASH.finditer(text)
        if not any(start < match.start() < end for start, end in pairs)
    ]


def _bracket_pairs(text: str) -> list[tuple[int, int]]:
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


def assemble(
    draft: Draft,
    cut: Callable[[Claim], bool],
    transform: Callable[[str], str] = str,
) -> tuple[str, str]:
    """Rebuild the text as (title, version), leaving out each claim that `cut` accepts.

    The version is the trailing run of version claims, plus any claims mixed in with them, so
    a restored "FREE DL" after "(VIP Mix)" stays after it. `transform` changes the kept text.
    """
    claims = sorted(draft.claims, key=lambda claim: claim.start)
    split_at = _version_start(draft.text, claims)
    pieces = _Pieces(draft.text, _orphaned_closers(draft.text, claims, cut), transform)
    title: list[str] = []
    version: list[str] = []
    position = 0
    for claim in claims:
        _add_gap(pieces, position, claim.start, split_at, title, version)
        target = version if claim.start >= split_at else title
        target.append(_claim_text(pieces, claim, cut))
        position = claim.end
    _add_gap(pieces, position, len(draft.text), split_at, title, version)
    return "".join(title), "".join(version)


@dataclass(frozen=True)
class _Pieces:
    """The text being rebuilt, and how its kept parts are written out."""

    text: str
    orphans: set[int]  # closers whose opener was cut
    transform: Callable[[str], str]

    def as_written(self, start: int, end: int) -> str:
        """text[start:end], with a cut marked before each orphaned closer, so tidying drops it."""
        return "".join(
            CUT + self.text[index] if index in self.orphans else self.text[index]
            for index in range(start, end)
        )

    def kept(self, start: int, end: int) -> str:
        """text[start:end] as written, transformed."""
        return self.transform(self.as_written(start, end))


def _orphaned_closers(text: str, claims: list[Claim], cut: Callable[[Claim], bool]) -> set[int]:
    """Where the closers are whose opener is in a cut claim while they aren't."""
    cut_claims = [claim for claim in claims if claim.kind != VERSION and cut(claim)]

    def is_cut(index: int) -> bool:
        return any(claim.start <= index < claim.end for claim in cut_claims)

    return {end for start, end in _bracket_pairs(text) if is_cut(start) and not is_cut(end)}


def _add_gap(
    pieces: _Pieces, start: int, end: int, split_at: int, title: list[str], version: list[str]
) -> None:
    """Add the unclaimed text from `start` to `end`, splitting it where the version starts."""
    middle = min(max(split_at, start), end)
    title.append(pieces.kept(start, middle))
    version.append(pieces.kept(middle, end))


def _claim_text(pieces: _Pieces, claim: Claim, cut: Callable[[Claim], bool]) -> str:
    """The text a claim contributes: its own text, or a gap where it was cut."""
    if claim.kind == VERSION:
        return pieces.kept(claim.start, claim.end)
    # A gap keeps "Song(feat. X)Remix" from becoming "SongRemix"; tidying collapses it.
    if cut(claim):
        return CUT
    # Text a toggle put back stays as written; text that was never cut is the title's own.
    if claim.default_on:
        return pieces.as_written(claim.start, claim.end)
    return pieces.kept(claim.start, claim.end)


def _version_start(text: str, claims: list[Claim]) -> int:
    """Where the trailing version starts, or the end of the text when there's none."""
    start = len(text)
    end = len(text)
    for claim in reversed(claims):
        if text[claim.end : end].strip():
            break
        if claim.kind == VERSION:
            start = claim.start
        end = claim.start
    # A separator just before the version goes with it, as in "Song - (VIP Mix)".
    lead = re.search(r"\s[-|/:]+\s*$", text[:start])
    if start < len(text) and lead:
        return lead.start()
    return start
