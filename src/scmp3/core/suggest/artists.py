"""Steps 4, 5 and 9 of the suggestion engine: the artist in the title, featured artists, and the
final main and featured lists."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from scmp3.core.suggest import rules
from scmp3.core.suggest.draft import VERSION, Claim, Context, Draft, has_words, names_pattern
from scmp3.core.suggest.names import (
    channel_key,
    dedupe,
    name_key,
    spacing_key,
    split_credit,
    split_names,
)
from scmp3.core.suggest.words import Wordlists, pattern, phrase_pattern

_SPACED_DASH = re.compile(r"(?<=\s)-(?=\s)")
# Separators and spaces trimmed off each part between spaced dashes.
_SEGMENT_TRIM = " -|/:"
# Symbols that join artists in one credit, as in "Kaia & Lou V" or "Kaia x Lou V".
_JOINERS = "\\s*[,&+]\\s*|\\s+[xX\u00d7]\\s+"
# The claims that take an artist from the title.
ARTIST_KINDS = ("artist_from_title", "strip_redundant_artist")
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


@dataclass(frozen=True)
class TitleArtist:
    """An artist credit taken from the title."""

    name: str  # as it's credited
    base: str  # the same without any feature that was put back into it
    restored: tuple[str, ...] = ()  # the featured names put back into it


def claim_bracket_features(draft: Draft, context: Context) -> None:
    """Step 5 in brackets: "(feat. Lou V)", "[ft. A & B]", "(with Lou V)" and "(w/ Lou V)"."""
    words = context.words
    keyword = pattern(rf"^\s*{words.feature}\s*:?\s*(?P<names>.*\S)")
    bracket_keyword = pattern(rf"^\s*{words.feature_bracketed}\s*(?P<names>.*\S)")
    for group in draft.groups():
        match = keyword.search(group.inner) or bracket_keyword.search(group.inner)
        if match is None:
            continue
        names = _feature_names(match["names"], context)
        # "(With You)" and "(with Strings)" are part of the title.
        if not names or name_key(names[0]) in words.not_names:
            continue
        draft.claim("extract_feature", group.start, group.end, names=tuple(names))


def claim_inline_features(draft: Draft, context: Context) -> None:
    """Step 5 outside brackets: "ft. Drake" up to a dash, a bracket, a producer or the end."""
    words = context.words
    regex = pattern(rf"{words.feature}\s*:?\s*{names_pattern(words.producer)}")
    for start, end, names_text in draft.inline_matches(regex):
        names = _feature_names(names_text, context)
        if names:
            sure = not _may_be_a_measure(draft.text[:start], words)
            draft.claim("extract_feature", start, end, enabled=sure, names=tuple(names))


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
        if pattern(_JOINERS).search(name):
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
        and not _SPACED_DASH.search(masked[: pipe.start()])
        and not _names_promo_or_label(masked[pipe.end() :], context)
    )
    if part.redundant or part.from_publisher or from_channel:
        _claim_artist_part(draft, context, part, unclear=False)


def _names_promo_or_label(text: str, context: Context) -> bool:
    """Whether text after a pipe is promo or a label tag, like "Monstercat Release"."""
    words = context.words
    promo = pattern(f"{words.promo}|{words.promo_tail}|{words.label_words}")
    if promo.search(text) or pattern(rf"{words.promo_bracket_endings}\W*$").search(text):
        return True
    key = channel_key(text)
    return key == channel_key(context.uploader) or key in words.channels


def read_artist_part(
    draft: Draft, context: Context, start: int, part_end: int
) -> ArtistPart | None:
    """Read the text between `start` and the separator at `part_end` as an artist credit."""
    masked = draft.masked()
    end = part_end
    while start < end and masked[start] in _SEGMENT_TRIM:
        start += 1
    feature = pattern(context.words.feature).search(masked, start, end)
    if feature:
        end = feature.start()
    while end > start and masked[end - 1] in _SEGMENT_TRIM:
        end -= 1
    text = masked[start:end].strip()
    if not has_words(text):
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
        redundant=spacing_key(text) == spacing_key(context.uploader),
        from_publisher=_names_publisher(text, context),
        is_list=has_feature or _is_collaboration(text, context.words),
        has_feature=has_feature,
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
    if part.redundant:
        draft.claim("strip_redundant_artist", part.start, part.end, part_end=part.part_end)
        return
    signal = part.from_publisher or part.is_list or uploader_is_label(context)
    draft.claim(
        "artist_from_title",
        part.start,
        part.end,
        enabled=signal and not unclear,
        names=tuple(split_credit(part.text)),
        part_end=part.part_end,
    )


def dash_segments(masked: str) -> list[Segment]:
    """The non-empty parts of the text between spaced dashes, trimmed of separators."""
    cuts = [match.start() for match in _SPACED_DASH.finditer(masked)]
    bounds = [-1, *cuts, len(masked)]
    segments: list[Segment] = []
    for index in range(len(bounds) - 1):
        start, end = bounds[index] + 1, bounds[index + 1]
        while start < end and masked[start] in _SEGMENT_TRIM:
            start += 1
        while end > start and masked[end - 1] in _SEGMENT_TRIM:
            end -= 1
        if start < end:
            segments.append(Segment(start, end, bounds[index] if index else start))
    return segments


def uploader_is_label(context: Context) -> bool:
    """Whether the uploader looks like a label or channel rather than the artist."""
    uploader = context.uploader
    if context.publisher and not _overlaps([uploader], context.publisher):
        return True
    if channel_key(uploader) in context.words.channels:
        return True
    if uploader.strip() in context.words.channels_exact:
        return True
    return bool(pattern(context.words.label_words).search(uploader))


def _is_collaboration(text: str, words: Wordlists) -> bool:
    """Whether a credit joins several artists, as in "Kaia & Lou V" or "Kaia vs. Lou V"."""
    return bool(pattern(_JOINERS).search(text) or pattern(words.together).search(text))


def _credit_parts(text: str, words: Wordlists) -> list[str]:
    """The names a credit joins, for matching only: "Chase & Status" gives Chase and Status."""
    return [part for part in pattern(rf"{_JOINERS}|\s+{words.together}\s+").split(text) if part]


def _names_publisher(text: str, context: Context) -> bool:
    """Whether a credit is a publisher artist, whole or by one of the names it joins."""
    return _overlaps([text, *_credit_parts(text, context.words)], context.publisher)


def _overlaps(names: Iterable[str], others: Iterable[str]) -> bool:
    """Whether any name matches one of `others`, ignoring case and spacing."""
    keys = {spacing_key(other) for other in others}
    return any(spacing_key(name) in keys for name in names)


def features_inside(draft: Draft, artist: Claim) -> list[Claim]:
    """The feature claims inside an artist part, like "ft. Rihanna" in "Drake ft. Rihanna"."""
    if artist.part_end is None:
        return []
    return [
        claim
        for claim in draft.claims
        if claim.kind == "extract_feature" and artist.start <= claim.start < artist.part_end
    ]


def carried_features(draft: Draft) -> set[int]:
    """Where the features inside a taken artist part start. They leave the title with it."""
    return {
        feature.start
        for artist in draft.claims
        if artist.kind in ARTIST_KINDS and artist.enabled
        for feature in features_inside(draft, artist)
    }


def title_artists(draft: Draft) -> list[TitleArtist]:
    """Step 9: the artists the title credits. A feature turned off goes back into the name."""
    credits: list[TitleArtist] = []
    for artist in draft.claims:
        if artist.kind not in ARTIST_KINDS or not artist.enabled:
            continue
        kept = [feature for feature in features_inside(draft, artist) if not feature.enabled]
        # A dropped uploader name credits nobody, unless a feature went back into it.
        if artist.kind == "strip_redundant_artist" and not kept:
            continue
        credits.extend(_credits_with_features(draft, artist, kept))
    return credits


def _credits_with_features(draft: Draft, artist: Claim, kept: list[Claim]) -> list[TitleArtist]:
    """The artist part's credits, with the features in `kept` put back into the last name."""
    base = draft.span(artist)
    if not kept:
        return [TitleArtist(name, name) for name in artist.names]
    end = max(feature.end for feature in kept)
    cut = [feature for feature in features_inside(draft, artist) if feature not in kept]
    text = draft.text[artist.start : end]
    for feature in sorted(cut, key=lambda claim: claim.start, reverse=True):
        start, stop = feature.start - artist.start, feature.end - artist.start
        text = text[:start] + " " + text[stop:]
    restored = tuple(name for feature in kept for name in feature.names)
    names, bases = split_credit(rules.tidy(text)), split_credit(base)
    if len(names) != len(bases):
        return [TitleArtist(rules.tidy(text), base, restored)]
    return [
        TitleArtist(name, name_base, restored) for name, name_base in zip(names, bases, strict=True)
    ]


def choose_main(publisher: list[str], from_title: list[TitleArtist], uploader: str) -> list[str]:
    """Step 9: the publisher artists, joined by new ones from the title; else the uploader."""
    if not publisher:
        return dedupe([artist.name for artist in from_title]) or dedupe([uploader])
    main = list(publisher)
    for artist in from_title:
        _add_title_artist(main, artist)
    return dedupe(main)


def _add_title_artist(main: list[str], artist: TitleArtist) -> None:
    """Add a title artist the publisher list doesn't have yet, whole or split."""
    keys = [spacing_key(name) for name in main]
    base = spacing_key(artist.base)
    if base in keys:
        # "Kaia ft. Lou V", with its feature put back, takes the place of the publisher's "Kaia".
        if artist.name != artist.base:
            main[keys.index(base)] = artist.name
            # It names Lou V now, so the publisher's own "Lou V" goes.
            restored = {spacing_key(name) for name in artist.restored}
            main[:] = [name for name in main if spacing_key(name) not in restored]
        return
    parts = {spacing_key(part) for part in re.split(_JOINERS, artist.base)}
    if not parts & set(keys):
        main.append(artist.name)


def settle(main: list[str], featured: list[str]) -> tuple[list[str], list[str]]:
    """Step 9: list each artist once. A name the title marks as featured leaves the main list."""
    featured = dedupe(featured)
    featured_keys = {name_key(name) for name in featured}
    kept = [name for name in main if name_key(name) not in featured_keys]
    if kept:
        return kept, featured
    # Every main artist is also featured, so they stay main and drop out of the featured list.
    main_keys = {name_key(name) for name in main}
    return main, [name for name in featured if name_key(name) not in main_keys]
