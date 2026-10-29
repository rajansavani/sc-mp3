"""Symbols and emoji around a title's artist part. They stay with the name when they belong to it,
and move to the title when they only decorate the part."""

from dataclasses import dataclass

from scmp3.core.suggest.draft import Claim, Context, Draft
from scmp3.core.suggest.names import inside_decorations, joined_names, same_spelling


@dataclass(frozen=True)
class PartBounds:
    """Where an artist part starts and ends once the symbols and emoji around it are placed."""

    start: int
    end: int
    wrapped: tuple[Claim, ...] = ()  # emoji claims that wrap the part and go with it


def artist_bounds(
    draft: Draft, context: Context, start: int, end: int, part_end: int
) -> PartBounds:
    """The artist part's bounds, with the symbols and emoji at its edges placed.

    Symbols or emoji on both sides wrap the name, as in "★ Kaia ★", so they stay with it. A name
    the uploader or a publisher name spells with its symbols, as in "Axurya ✧", keeps them too.
    Other symbols at an edge, like the heart in "♡ Roy Bee - Kiss Me Again ♡", stay with the title.
    """
    masked = draft.masked()
    before = _emoji_before(draft, start)
    after = _emoji_after(draft, end, part_end)
    inner_start, inner_end = inside_decorations(masked, start, end)
    if (before or inner_start > start) and (after or inner_end < end):
        new_start = before[0].start if before else start
        new_end = after[-1].end if after else end
        return PartBounds(new_start, new_end, (*before, *after))
    # Each edge is checked on the name at that edge, so "Lou V & Kaia ✧" keeps Kaia's star.
    names = joined_names(masked[start:end].strip()) or [""]
    wrapped: list[Claim] = []
    if before and spelled_with_symbols(draft.text[before[0].start : start] + names[0], context):
        start, wrapped = before[0].start, [*before]
    elif not spelled_with_symbols(names[0], context):
        start = inner_start
    if after and spelled_with_symbols(names[-1] + draft.text[end : after[-1].end], context):
        end, wrapped = after[-1].end, [*wrapped, *after]
    elif not spelled_with_symbols(names[-1], context):
        end = inner_end
    return PartBounds(start, end, tuple(wrapped))


def part_text(draft: Draft, bounds: PartBounds) -> str:
    """The artist part's text: other rules' claims blanked out, the emoji that wrap it kept."""
    chars = list(draft.masked())
    for claim in bounds.wrapped:
        chars[claim.start : claim.end] = draft.text[claim.start : claim.end]
    return "".join(chars[bounds.start : bounds.end]).strip()


def _emoji_before(draft: Draft, start: int) -> list[Claim]:
    """The emoji claims that run up to `start`, with only spaces between them."""
    found: list[Claim] = []
    edge = start
    for claim in sorted(_emoji_claims(draft), key=lambda claim: claim.end, reverse=True):
        if claim.end <= edge and not draft.text[claim.end : edge].strip():
            found.insert(0, claim)
            edge = claim.start
    return found


def _emoji_after(draft: Draft, end: int, part_end: int) -> list[Claim]:
    """The emoji claims that run on from `end`, before the separator, with only spaces between."""
    found: list[Claim] = []
    edge = end
    for claim in sorted(_emoji_claims(draft), key=lambda claim: claim.start):
        if (
            edge <= claim.start
            and claim.end <= part_end
            and not draft.text[edge : claim.start].strip()
        ):
            found.append(claim)
            edge = claim.end
    return found


def _emoji_claims(draft: Draft) -> list[Claim]:
    """The claims on emoji in `draft`."""
    return [claim for claim in draft.claims if claim.kind == "remove_emoji"]


def spelled_with_symbols(name: str, context: Context) -> bool:
    """Whether the uploader or a publisher name is `name`, its symbols included."""
    name = name.strip()
    return any(same_spelling(name, other) for other in [context.uploader, *context.publisher])
