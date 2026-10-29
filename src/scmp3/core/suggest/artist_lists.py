"""Step 9 of the suggestion engine: the final main and featured lists, from the publisher's
artists, the artists the title credits, and the uploader."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from scmp3.core.suggest import rules
from scmp3.core.suggest.draft import ARTIST_KINDS, CUT, VERSION, Claim, Draft
from scmp3.core.suggest.names import (
    JOINERS,
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


def carried_versions(draft: Draft) -> set[int]:
    """Where the versions inside a taken artist part start, like "(live)" in "AEREA (live) | Set".

    They describe the track, so they move to the version rather than start the title.
    """
    return {
        claim.start
        for artist in draft.claims
        if artist.kind in ARTIST_KINDS and artist.enabled and artist.part_end is not None
        for claim in draft.claims
        if claim.kind == VERSION and artist.start <= claim.start < artist.part_end
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
    features = features_inside(draft, artist)
    cut = [feature for feature in features if feature not in kept]
    # A version beside the name moves to the track's version, so it isn't put back into the name.
    # One inside a feature goes back with the feature.
    cut += [
        claim
        for claim in draft.claims
        if claim.kind == VERSION
        and artist.start <= claim.start < end
        and not any(feature.start <= claim.start < feature.end for feature in features)
    ]
    text = draft.text[artist.start : end]
    for claim in sorted(cut, key=lambda claim: claim.start, reverse=True):
        start, stop = claim.start - artist.start, claim.end - artist.start
        text = text[:start] + CUT + text[stop:]
    restored = tuple(name for feature in kept for name in feature.names)
    names, bases = split_credit(rules.tidy(text)), split_credit(base)
    if len(names) != len(bases):
        return [TitleArtist(rules.tidy(text), base, restored)]
    return [
        TitleArtist(name, name_base, restored) for name, name_base in zip(names, bases, strict=True)
    ]


@dataclass(frozen=True)
class PublisherArtists:
    """The publisher's artists as main and featured names."""

    main: list[str]
    featured: list[str]
    listed: list[str]  # every name that came from a list of several, for matching title credits


def publisher_artists(
    lists: list[list[str]],
    from_title: list[TitleArtist],
    uploader: Sequence[str],
    producers: Sequence[str] = (),
) -> PublisherArtists:
    """Main and featured names from the publisher's artists, each given as the names it lists.

    In a list of several, the names the title's artist part credits are main, as described in
    `_leads`, and the others are featured. `uploader` has the uploader's spellings, such as with
    and without emoji. `producers` has the names that are producer credits, like "Prod. H2T".
    """
    # Whole, for a name that's never split like "Above & Beyond", and in parts.
    credited = [part for artist in from_title for part in [artist.base, *joined_names(artist.base)]]
    main: list[str] = []
    featured: list[str] = []
    listed: list[str] = []
    for names in lists:
        if len(names) < 2:
            main.extend(names)
            continue
        leads = _leads(names, credited, uploader, producers)
        main.extend(leads)
        featured.extend(name for name in names if name not in leads)
        listed.extend(names)
    return PublisherArtists(main, featured, listed)


def _leads(
    names: list[str], credited: list[str], uploader: Sequence[str], producers: Sequence[str]
) -> list[str]:
    """A list's main names: the ones the title's artist part credits, in its order, when it
    credits several, or one that isn't the uploader, as in "Obito - Song" from Prod. H2T.

    Otherwise the user's rule applies: the uploader when it's listed, every name of it when it's
    a duo like "Rival & Cadmium", or else the first name. A name that is a producer credit, like
    "Prod. H2T", is main only when every name is one.
    """
    named = _named(names, credited)
    uploader_parts = [part for spelling in uploader for part in [spelling, *joined_names(spelling)]]
    by_uploader = _named(names, uploader_parts)
    if len(named) > 1 or (named and not any_same_artist(named, by_uploader)):
        return named
    artists = [name for name in names if not any_same_artist([name], producers)] or names
    return [name for name in by_uploader if name in artists] or artists[:1]


def _named(names: list[str], credits: list[str]) -> list[str]:
    """The names in `names` that `credits` name, in the order `credits` names them."""
    return dedupe([name for credit in credits for name in names if same_artist(name, credit)])


def choose_main(
    publisher: list[str],
    from_title: list[TitleArtist],
    uploader: str,
    listed: Sequence[str] = (),
) -> list[str]:
    """The publisher artists, joined by new ones from the title; else the uploader.

    `listed` holds the names the publisher's lists give, featured ones too. A name that lost a
    letter, like "Michael Bubl?", takes the uploader's spelling of it.
    """
    if not publisher:
        main = dedupe_artists([artist.name for artist in from_title], fullest_spelling)
        return _spelled_as_uploader(main or dedupe([uploader]), uploader)
    # First on the publisher's names, so the uploader's spelling wins over the title's.
    main = _spelled_as_uploader(list(publisher), uploader)
    spelled_by_publisher = set(main)
    for artist in from_title:
        _add_title_artist(main, artist, spelled_by_publisher, listed)
    # Again at the end, since a title artist can bring a lost letter of its own.
    return _spelled_as_uploader(dedupe_artists(main, fullest_spelling), uploader)


def _spelled_as_uploader(names: list[str], uploader: str) -> list[str]:
    """The names, each that lost a letter spelled as the uploader is when it's the same artist."""
    if has_stand_in(uploader):
        return names
    return [
        uploader if has_stand_in(name) and same_artist(name, uploader) else name for name in names
    ]


def _add_title_artist(
    main: list[str], artist: TitleArtist, spelled_by_publisher: set[str], listed: Sequence[str]
) -> None:
    """Add a title artist the publisher's artists don't name yet.

    `spelled_by_publisher` holds the names in `main` as the publisher spells them.
    """
    matches = [index for index, name in enumerate(main) if same_artist(name, artist.base)]
    if matches:
        _respell(main, matches[0], artist, from_publisher=main[matches[0]] in spelled_by_publisher)
        return
    parts = joined_names(artist.base)
    # One name in common with a whole artist is enough, so "Kaia & Lou V" joins no "Kaia".
    whole = [name for name in main if not any_same_artist([name], listed)]
    if any_same_artist(parts, whole):
        return
    # A feature put back must show, so only a plain credit counts as named by a list.
    if artist.name == artist.base and _all_in_lists(parts, main, listed):
        return
    # "Obito & Lou V" with Obito listed adds Lou V beside the listed Obito, never Obito again.
    if artist.name == artist.base and any_same_artist(parts, listed):
        main.extend(_around(artist.base, listed))
        return
    main.append(artist.name)


def _around(credit: str, names: Sequence[str]) -> list[str]:
    """The runs of a credit between the names `names` has: "Obito & Lou V" around Obito is Lou V."""
    tokens = re.split(f"({JOINERS})", credit)
    parts, joiners = tokens[0::2], tokens[1::2]
    runs: list[str] = []
    run = ""
    for index, part in enumerate(parts):
        if any_same_artist([part], names):
            runs.append(run)
            run = ""
        elif run:
            run += joiners[index - 1] + part
        else:
            run = part
    runs.append(run)
    return [run.strip() for run in runs if run.strip()]


def _all_in_lists(parts: list[str], main: list[str], listed: Sequence[str]) -> bool:
    """Whether lists like "Rival, Cadmium, Harley Bird" name every one of `parts`.

    One isn't enough, since "Tyler" may be part of "Tyler, The Creator", and "Kaia & Lou V"
    isn't named by "Kaia, Mara".
    """
    names = [*listed, *(part for name in main for part in comma_parts(name))]
    return bool(names) and all(any_same_artist([part], names) for part in parts)


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
