"""Steps 4 and 5 of the suggestion engine: the artist in the title and featured artists."""

import re
from dataclasses import dataclass
from typing import Literal

from scmp3.core.suggest import decorations, file_names, rules, youtube
from scmp3.core.suggest.draft import (
    SEPARATORS,
    VERSION,
    Claim,
    Context,
    Draft,
    Group,
    has_words,
    names_pattern,
    spaced_dashes,
)
from scmp3.core.suggest.names import (
    JOINERS,
    any_same_artist,
    channel_key,
    name_key,
    same_artist,
    split_credit,
    split_names,
    strip_invisible,
)
from scmp3.core.suggest.words import Wordlists, pattern, phrase_pattern

# Stands in for a publisher name while feature text is split, so the name stays whole.
_HELD = re.compile("\x00([0-9]+)\x00")

Ending = Literal["version", "title", "unclear"]


@dataclass(frozen=True)
class Segment:
    """A part of the title between spaced dashes."""

    start: int
    end: int
    dash: int  # where the dash before it is; the first part has none, so its own start


@dataclass(frozen=True)
class ArtistPart:
    """The text before a title's artist separator, read as a possible artist credit."""

    start: int
    end: int  # where the credit ends, before any feature keyword
    part_end: int  # where the separator after it is
    text: str
    redundant: bool  # it's the uploader
    from_publisher: bool  # it's a publisher artist, or names one
    is_list: bool  # it joins several artists, or has a feature
    has_feature: bool
    wrapped: tuple[Claim, ...] = ()  # emoji claims around it that go with it


def claim_bracket_features(draft: Draft, context: Context) -> None:
    """Step 5 in brackets: "(feat. Lou V)", "[ft. A & B]", "(with Lou V)" and "(w/ Lou V)"."""
    _claim_feature_groups(draft, context, draft.groups())


def claim_nested_features(draft: Draft, context: Context) -> None:
    """Step 5 in brackets with brackets inside, once the inner ones are claimed.

    "(feat. Lou V (UK))" features "Lou V (UK)". An inner version or producer credit keeps its
    own claim, as in "(feat. Lou V (prod. Hazy))".
    """
    _claim_feature_groups(draft, context, [*draft.groups(), *draft.nested_groups()])


def _claim_feature_groups(draft: Draft, context: Context, groups: list[Group]) -> None:
    """Claim each group that starts with a feature keyword."""
    words = context.words
    keyword = pattern(rf"^\s*{words.feature}\s*:?\s*(?P<names>.*\S)")
    bracket_keyword = pattern(rf"^\s*{words.feature_bracketed}\s*(?P<names>.*\S)")
    for group in groups:
        match = keyword.search(group.inner) or bracket_keyword.search(group.inner)
        if match is None:
            continue
        names = _feature_names(match["names"], context)
        # "(With You)" and "(with Strings)" are part of the title.
        if not names or name_key(names[0]) in words.not_names:
            continue
        draft.claim_credit("extract_feature", group, names=tuple(names))


def claim_inline_features(draft: Draft, context: Context) -> None:
    """Step 5 outside brackets: "ft. Drake" up to a dash, a bracket, a producer or the end."""
    words = context.words
    regex = pattern(rf"{words.feature}\s*:?\s*{names_pattern(words.producer)}")
    for start, end, names_text in draft.inline_matches(regex):
        names = _feature_names(names_text, context)
        if names:
            measure = _may_be_a_measure(draft.text[:start], words)
            sure = not measure and _looks_like_names(names_text, words)
            draft.claim("extract_feature", start, end, enabled=sure, names=tuple(names))


def _looks_like_names(text: str, words: Wordlists) -> bool:
    """Whether text after an unbracketed "ft" reads as names, so the feature is sure.

    "ft nobody lmfao" and "ft me sigilkore song" are jokes, and "ft Lou V Sigilkore edit" ends in
    a version that may hold part of the credit, so those are only offered.
    """
    key = name_key(text)
    if any(key == word or key.startswith(f"{word} ") for word in words.joke_starts):
        return False
    return not pattern(rf"{words.made_by}\W*$").search(text)


def _may_be_a_measure(before: str, words: Wordlists) -> bool:
    """Whether "ft" after this text may mean feet or Fort, as in "Six Ft" or "in Ft. Lauderdale".

    A small number counts, but not a year like "2024" or a part number like "Pt. 2".
    """
    if pattern(rf"{words.part_words}\s*[0-9]+\s*$").search(before):
        return False
    return bool(pattern(rf"(?:(?<!\S)[0-9]{{1,2}}|{words.uncertain_after})\s*$").search(before))


def _feature_names(text: str, context: Context) -> list[str]:
    """Split featured names, keeping a publisher artist like "Chase & Status" whole."""
    held: list[str] = []

    def hold(match: re.Match[str]) -> str:
        held.append(match.group())
        return f"\x00{len(held) - 1}\x00"

    for name in sorted(context.publisher, key=len, reverse=True):
        if pattern(JOINERS).search(name):
            text = pattern(phrase_pattern(name)).sub(hold, text)
    names = split_names(text)
    return [_HELD.sub(lambda match: held[int(match[1])], name) for name in names]


def claim_title_artist(draft: Draft, context: Context) -> None:
    """Step 4: read "Artist - Title", after setting aside a " - VIP Mix" style version."""
    masked = draft.masked()
    segments = dash_segments(masked)
    if len(segments) < 2:
        return
    part = read_artist_part(draft, context, segments[0].start, segments[1].dash)
    if part is None:
        return
    last = segments[-1]
    ending = _read_ending(len(segments), part, masked[last.start : last.end], context)
    if ending == "version":
        draft.claim(VERSION, last.dash, last.end)
        if len(segments) == 2:
            return
    _claim_artist_part(draft, context, part, unclear=ending == "unclear")


def claim_pipe_artist(draft: Draft, context: Context) -> None:
    """Step 4 with a pipe: in "Kaia | Midnight Drive" from Kaia, the pipe works like the dash."""
    masked = draft.masked()
    pipe = rules.PIPES.search(masked)
    if pipe is None or not has_words(masked[pipe.end() :]):
        return
    part = read_artist_part(draft, context, 0, pipe.start())
    if part is None:
        return
    # A channel's "Okoye | Boiler Room London" names the artist first and the event after.
    from_channel = (
        uploader_is_label(context)
        and not any(dash < pipe.start() for dash in spaced_dashes(masked))
        and not _names_promo_or_label(masked[pipe.end() :], context)
    )
    if part.redundant or part.from_publisher or from_channel:
        _claim_artist_part(draft, context, part, unclear=False)


def _names_promo_or_label(text: str, context: Context) -> bool:
    """Whether text after a pipe is promo or a label tag, like "Monstercat Release".

    The channel's own event, like "Boiler Room London: Beeyou Records", is neither.
    """
    words = context.words
    promo = pattern(f"{words.promo}|{words.promo_tail}")
    if promo.search(text) or pattern(rf"{words.promo_bracket_endings}\W*$").search(text):
        return True
    key, uploader = channel_key(text), channel_key(context.uploader)
    if key == uploader or key in words.channels:
        return True
    # The label words in an event's name, like "Records" in a host's, don't make it a label tag.
    if uploader and key.startswith(uploader):
        return False
    return bool(pattern(words.label_words).search(text))


def read_artist_part(
    draft: Draft, context: Context, start: int, part_end: int
) -> ArtistPart | None:
    """Read the text between `start` and the separator at `part_end` as an artist credit."""
    masked = draft.masked()
    end = part_end
    while start < end and masked[start] in SEPARATORS:
        start += 1
    feature = pattern(context.words.feature).search(masked, start, end)
    if feature:
        end = feature.start()
    while end > start and masked[end - 1] in SEPARATORS:
        end -= 1
    # A closing bracket left by a credit cut short, as in "Kaia (feat. Lou V (prod. Hazy))".
    while end > start and rules.ends_with_stray_closer(masked[start:end]):
        end -= 1
        while end > start and masked[end - 1] in SEPARATORS:
            end -= 1
    bounds = decorations.artist_bounds(draft, context, start, end, part_end)
    start, end = bounds.start, bounds.end
    text = strip_invisible(decorations.part_text(draft, bounds))
    # A name of only symbols, like "✝✝✝", counts only as the uploader or a listed artist spells it.
    if not text or (not has_words(text) and not decorations.spelled_with_symbols(text, context)):
        return None
    has_feature = feature is not None or any(
        claim.kind == "extract_feature" and start <= claim.start < part_end
        for claim in draft.claims
    )
    return ArtistPart(
        start=start,
        end=end,
        part_end=part_end,
        text=text,
        redundant=same_artist(text, context.uploader),
        from_publisher=_names_publisher(text, context),
        is_list=has_feature or _is_collaboration(text, context.words),
        has_feature=has_feature,
        wrapped=bounds.wrapped,
    )


def _read_ending(parts: int, part: ArtistPart, ending: str, context: Context) -> Ending:
    """Whether the last " - " part is the version, the title, or too unclear to decide."""
    words = context.words
    if parts > 2:
        return "version" if rules.is_dash_version(ending, words) else "title"
    if part.redundant or part.from_publisher or not rules.is_dash_version(ending, words):
        return "title"
    # The publisher names the artist, so "Midnight Drive - Lou V Remix" is title and version.
    if context.publisher:
        return "version"
    # From a label, "Kaia - Live" may be artist and title, the same shape as "Song - VIP".
    one_word_from_label = uploader_is_label(context) and len(ending.split()) == 1
    if rules.is_bare_version(ending, words) and not one_word_from_label:
        return "version"
    return "unclear"


def _claim_artist_part(draft: Draft, context: Context, part: ArtistPart, *, unclear: bool) -> None:
    """Claim the artist part: dropped when it's the uploader, else taken as the artist."""
    # Emoji that wrap the part, as in "⚡ Kaia ⚡", go with it rather than staying behind.
    draft.claims = [claim for claim in draft.claims if claim not in part.wrapped]
    if part.redundant:
        draft.claim("strip_redundant_artist", part.start, part.end, part_end=part.part_end)
        return
    # An uploader who made the version, as in "obvious - oklou (tikkoii edit)", is an artist,
    # though the publisher names someone else. A known channel or label still counts as one.
    differs = _differs_from_publisher(context) and not _version_by_uploader(draft, context)
    from_label = differs or uploader_is_channel(context)
    signal = (
        part.from_publisher
        or part.is_list
        or from_label
        or youtube.names_another_artist(draft, context)
        or _names_a_tagless_files_artist(context)
    )
    draft.claim(
        "artist_from_title",
        part.start,
        part.end,
        enabled=signal and not unclear,
        names=tuple(split_credit(part.text)),
        part_end=part.part_end,
    )


def _names_a_tagless_files_artist(context: Context) -> bool:
    """Whether the title is the name of a file with no artist tag, like "Kaia - Song.mp3"."""
    return file_names.is_from_file_name(context.source) and not context.uploader.strip()


def _version_by_uploader(draft: Draft, context: Context) -> bool:
    """Whether a version in the title names the uploader, as "(tikkoii edit)" from tikkoii does."""
    uploader = context.uploader.strip()
    if not has_words(uploader):
        return False
    named = pattern(rf"{phrase_pattern(uploader)}(?:'s)?\s+{context.words.made_by}")
    return any(claim.kind == VERSION and named.search(draft.span(claim)) for claim in draft.claims)


def dash_segments(masked: str) -> list[Segment]:
    """The non-empty parts of the text between spaced dashes, trimmed of separators."""
    cuts = spaced_dashes(masked)
    bounds = [-1, *cuts, len(masked)]
    segments: list[Segment] = []
    for index in range(len(bounds) - 1):
        start, end = bounds[index] + 1, bounds[index + 1]
        while start < end and masked[start] in SEPARATORS:
            start += 1
        while end > start and masked[end - 1] in SEPARATORS:
            end -= 1
        if start < end:
            segments.append(Segment(start, end, bounds[index] if index else start))
    return segments


def uploader_is_label(context: Context) -> bool:
    """Whether the uploader looks like a label or channel rather than the artist."""
    return _differs_from_publisher(context) or uploader_is_channel(context)


def _differs_from_publisher(context: Context) -> bool:
    """Whether the publisher names artists and the uploader isn't one of them."""
    return bool(context.publisher) and not any_same_artist([context.uploader], context.publisher)


def uploader_is_channel(context: Context) -> bool:
    """Whether the uploader's name is a known channel's, a website's, or has a label word."""
    uploader = context.uploader
    if channel_key(uploader) in context.words.channels:
        return True
    if uploader.strip() in context.words.channels_exact:
        return True
    # A website's name, like "Thissongissick.com", is a channel's.
    if pattern(rf"\w{context.words.domains}\s*$").search(uploader):
        return True
    return bool(pattern(context.words.label_words).search(uploader))


def _is_collaboration(text: str, words: Wordlists) -> bool:
    """Whether a credit joins several artists, as in "Kaia & Lou V" or "Kaia vs. Lou V"."""
    return bool(pattern(JOINERS).search(text) or pattern(words.together).search(text))


def _credit_parts(text: str, words: Wordlists) -> list[str]:
    """The names a credit joins, for matching only: "Chase & Status" gives Chase and Status."""
    return [part for part in pattern(rf"{JOINERS}|\s+{words.together}\s+").split(text) if part]


def _names_publisher(text: str, context: Context) -> bool:
    """Whether a credit is a publisher artist, whole or by one of the names it joins."""
    return any_same_artist([text, *_credit_parts(text, context.words)], context.publisher)
