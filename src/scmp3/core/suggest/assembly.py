"""Rebuilding a draft's text as a title and a version, leaving out the claims that are cut.

A version claim can sit inside another claim, as "(VIP Mix)" does in "(feat. Lou V (VIP Mix) &
Mara)". While the outer claim's change is on, the version inside it moves to the version.
"""

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass

from scmp3.core.suggest.draft import ARTIST_KINDS, CUT, VERSION, Claim, Draft, bracket_pairs


def assemble(
    draft: Draft,
    cut: Callable[[Claim], bool],
    transform: Callable[[str], str] = str,
    moved: Collection[int] = (),
) -> tuple[str, str]:
    """Rebuild the text as (title, version), leaving out each claim that `cut` accepts.

    The version is the trailing run of version claims, plus any claims mixed in with them, so
    a restored "FREE DL" after "(VIP Mix)" stays after it. `transform` changes the kept text.
    The version claims starting at `moved`, and those inside a cut claim that's on, lead the
    version.
    """
    claims = sorted(draft.claims, key=lambda claim: claim.start)
    outer = [claim for claim in claims if not _inside_another(claim, claims)]
    split_at = _version_start(draft.text, outer, cut)
    pieces = _Pieces(draft.text, _orphaned_closers(draft.text, claims, cut), transform)
    title: list[str] = []
    version: list[str] = []
    carried: list[str] = []
    position = 0
    for claim in outer:
        _add_gap(pieces, position, claim.start, split_at, title, version)
        if claim.kind == VERSION and claim.start in moved:
            title.append(CUT)
            carried.append(pieces.kept(claim.start, claim.end))
        else:
            target = version if claim.start >= split_at else title
            target.append(_claim_text(pieces, claim, cut))
            carried.extend(_versions_cut_with(pieces, claim, claims, cut))
        position = claim.end
    _add_gap(pieces, position, len(draft.text), split_at, title, version)
    moved_text = "".join(" " + text for text in carried)
    trailing = "".join(version)
    # The moved versions come first, a space before the version already at the end.
    if moved_text and trailing.strip() and not trailing[0].isspace():
        trailing = " " + trailing
    return "".join(title), moved_text + trailing


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


def _inside_another(claim: Claim, claims: list[Claim]) -> bool:
    """Whether `claim` lies within another claim, as a version inside a credit does."""
    return any(
        other is not claim and other.start <= claim.start and claim.end <= other.end
        for other in claims
    )


def _versions_cut_with(
    pieces: _Pieces, claim: Claim, claims: list[Claim], cut: Callable[[Claim], bool]
) -> list[str]:
    """The text of the version claims inside `claim`, when `claim` is on and cut.

    A credit switched off but cut, because it went back into an artist's name, keeps its version.
    """
    if claim.kind == VERSION or not claim.enabled or not cut(claim):
        return []
    return [
        pieces.kept(inner.start, inner.end)
        for inner in claims
        if inner.kind == VERSION and claim.start < inner.start and inner.end < claim.end
    ]


def _orphaned_closers(text: str, claims: list[Claim], cut: Callable[[Claim], bool]) -> set[int]:
    """Where the closers are whose opener is in a cut claim while they aren't."""
    cut_claims = [claim for claim in claims if claim.kind != VERSION and cut(claim)]

    def is_cut(index: int) -> bool:
        return any(claim.start <= index < claim.end for claim in cut_claims)

    return {end for start, end in bracket_pairs(text) if is_cut(start) and not is_cut(end)}


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


def _version_start(text: str, claims: list[Claim], cut: Callable[[Claim], bool]) -> int:
    """Where the trailing version starts, or the end of the text when there's none.

    An artist at the end that stays, as in "Numb (Live in Munich) - Linkin Park" with its change
    off, ends the run, so the text before it stays as typed.
    """
    start = len(text)
    end = len(text)
    for claim in reversed(claims):
        if text[claim.end : end].strip():
            break
        if claim.kind in ARTIST_KINDS and not cut(claim):
            break
        if claim.kind == VERSION:
            start = claim.start
        end = claim.start
    # A separator just before the version goes with it, as in "Song - (VIP Mix)".
    lead = re.search(r"\s[-|/:]+\s*$", text[:start])
    if start < len(text) and lead:
        return lead.start()
    return start
