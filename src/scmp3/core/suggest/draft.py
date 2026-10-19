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
_ANY_BRACKET = re.escape(OPENERS + CLOSERS)
# A bracket right after ":" or ";" is part of an emoticon like ":(" or ";-)", not a bracket.
NOT_EMOTICON = r"(?<![:;])(?<![:;]-)"
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

    def groups(self) -> list[Group]:
        """The bracketed parts nobody has claimed yet."""
        return [
            Group(match.start(), match.end(), match.group()[1:-1])
            for match in _GROUP.finditer(self.masked())
        ]

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
    title: list[str] = []
    version: list[str] = []
    position = 0
    for claim in claims:
        _add_gap(draft.text[position : claim.start], position, split_at, transform, title, version)
        target = version if claim.start >= split_at else title
        target.append(_claim_text(draft, claim, cut, transform))
        position = claim.end
    _add_gap(draft.text[position:], position, split_at, transform, title, version)
    return "".join(title), "".join(version)


def _add_gap(
    gap: str,
    offset: int,
    split_at: int,
    transform: Callable[[str], str],
    title: list[str],
    version: list[str],
) -> None:
    """Add unclaimed text that starts at `offset`, splitting it where the version starts."""
    middle = min(max(split_at - offset, 0), len(gap))
    title.append(transform(gap[:middle]))
    version.append(transform(gap[middle:]))


def _claim_text(
    draft: Draft, claim: Claim, cut: Callable[[Claim], bool], transform: Callable[[str], str]
) -> str:
    """The text a claim contributes: its own text, or a space where it was cut."""
    text = draft.span(claim)
    if claim.kind == VERSION:
        return transform(text)
    # A space keeps "Song(feat. X)Remix" from becoming "SongRemix"; tidying collapses it.
    if cut(claim):
        return " "
    # Text a toggle put back stays as written; text that was never cut is the title's own.
    return text if claim.default_on else transform(text)


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
