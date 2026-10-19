"""Step 9 of the suggestion engine: the final main and featured lists, from the publisher's
artists, the artists the title credits, and the uploader."""

from dataclasses import dataclass

from scmp3.core.suggest import rules
from scmp3.core.suggest.draft import Claim, Draft
from scmp3.core.suggest.names import (
    any_same_artist,
    comma_parts,
    dedupe,
    dedupe_artists,
    fullest_spelling,
    has_stand_in,
    joined_names,
    preferred_spelling,
    same_artist,
    split_credit,
)

# The claims that take an artist from the title.
ARTIST_KINDS = ("artist_from_title", "strip_redundant_artist")


@dataclass(frozen=True)
class TitleArtist:
    """An artist credit taken from the title."""

    name: str  # as it's credited
    base: str  # the same without any feature that was put back into it
    restored: tuple[str, ...] = ()  # the featured names put back into it


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
    """The artists the title credits. A feature turned off goes back into the name."""
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
    """The publisher artists, joined by new ones from the title; else the uploader.

    A name that lost a letter, like "Michael Bubl?", takes the uploader's spelling of it.
    """
    if not publisher:
        main = dedupe_artists([artist.name for artist in from_title], fullest_spelling)
        return _spelled_as_uploader(main or dedupe([uploader]), uploader)
    # First on the publisher's names, so the uploader's spelling wins over the title's.
    main = _spelled_as_uploader(list(publisher), uploader)
    spelled_by_publisher = set(main)
    for artist in from_title:
        _add_title_artist(main, artist, spelled_by_publisher)
    # Again at the end, since a title artist can bring a lost letter of its own.
    return _spelled_as_uploader(dedupe_artists(main, fullest_spelling), uploader)


def _spelled_as_uploader(names: list[str], uploader: str) -> list[str]:
    """The names, each that lost a letter spelled as the uploader is when it's the same artist."""
    if has_stand_in(uploader):
        return names
    return [
        uploader if has_stand_in(name) and same_artist(name, uploader) else name for name in names
    ]


def _add_title_artist(main: list[str], artist: TitleArtist, spelled_by_publisher: set[str]) -> None:
    """Add a title artist the publisher list doesn't name yet.

    `spelled_by_publisher` holds the names in `main` as the publisher spells them.
    """
    matches = [index for index, name in enumerate(main) if same_artist(name, artist.base)]
    if matches:
        _respell(main, matches[0], artist, from_publisher=main[matches[0]] in spelled_by_publisher)
        return
    parts = joined_names(artist.base)
    # One name in common with a publisher artist is enough, so "Kaia & Lou V" joins no "Kaia".
    if any_same_artist(parts, main):
        return
    # A feature put back must show, so only a plain credit counts as named in "Rival, Cadmium".
    if artist.name == artist.base and _all_in_comma_lists(parts, main):
        return
    main.append(artist.name)


def _all_in_comma_lists(parts: list[str], main: list[str]) -> bool:
    """Whether comma lists like "Rival, Cadmium, Harley Bird" name every one of `parts`.

    One isn't enough, since "Tyler" may be part of "Tyler, The Creator".
    """
    listed = [part for name in main for part in comma_parts(name)]
    return bool(listed) and all(any_same_artist([part], listed) for part in parts)


def _respell(main: list[str], index: int, artist: TitleArtist, *, from_publisher: bool) -> None:
    """Use the title's spelling of the artist at `index` when it's the fuller one."""
    if artist.name != artist.base:
        # "Kaia ft. Lou V", with its feature put back, takes the place of the publisher's "Kaia".
        main[index] = artist.name
        # It names Lou V now, so the publisher's own "Lou V" goes.
        main[:] = [name for name in main if not any_same_artist([name], artist.restored)]
        return
    if from_publisher:
        # The publisher's spelling wins, unless "Michael Bubl?" lost a letter the title still has.
        main[index] = preferred_spelling(main[index], artist.name)
    else:
        # Two spellings in the title, as in "Zoe x Zoë", keep the fuller one.
        main[index] = fullest_spelling(main[index], artist.name)


def merge_featured(from_title: list[str], from_publisher: list[str], uploader: str) -> list[str]:
    """The featured names from the title, then the artist field, each artist once.

    A name in both is spelled as the artist field has it, unless that spelling lost a letter. A
    lost letter the uploader's name has is filled in, as for main artists.
    """
    publisher = dedupe_artists(from_publisher)
    merged = dedupe_artists([_spelled_as(name, publisher) for name in from_title] + publisher)
    return _spelled_as_uploader(merged, uploader)


def credited_spelling(name: str, publisher: list[str], uploader: str) -> str:
    """How the tags spell an artist: as the publisher does, with a lost letter from the uploader.

    `publisher` is every name the publisher gives, its featured ones too.
    """
    listed = [*publisher, *(part for other in publisher for part in comma_parts(other))]
    return _spelled_as_uploader([_spelled_as(name, listed)], uploader)[0]


def settle(main: list[str], featured: list[str]) -> tuple[list[str], list[str]]:
    """List each artist once. A name the title marks as featured leaves the main list."""
    featured = dedupe_artists(featured)
    kept = [name for name in main if not any_same_artist([name], featured)]
    if kept:
        featured = [_spelled_as(name, main) for name in featured]
    else:
        # Every main artist is also featured, so they stay main and drop out of the featured list.
        kept = [_main_spelling(name, featured) for name in main]
        featured = [name for name in featured if not any_same_artist([name], main)]
    # "Rival, Cadmium, Harley Bird" already names a featured Harley Bird.
    listed = [part for name in kept for part in comma_parts(name)]
    return kept, [name for name in featured if not any_same_artist([name], listed)]


def _spelled_as(name: str, others: list[str]) -> str:
    """`name` spelled as `others` has the same artist, unless that spelling lost a letter."""
    for other in others:
        if same_artist(name, other):
            return preferred_spelling(other, name)
    return name


def _main_spelling(name: str, featured: list[str]) -> str:
    """A main artist's spelling, or a featured one of the same artist when it lost a letter."""
    for other in featured:
        if same_artist(name, other):
            return preferred_spelling(name, other)
    return name
