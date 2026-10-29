"""Tests for crediting: listed producers held back from the featured artists, and feature labels
that say when a name is a main artist already."""

from dataclasses import replace

import pytest

from scmp3.core.models import Settings, SourceInfo, TagSuggestion
from scmp3.core.suggest import apply_changes, suggest
from scmp3.core.suggest.draft import Draft
from scmp3.core.suggest.labels import feature_label
from scmp3.core.suggest.producers import is_producer_credit, marked_producers
from scmp3.core.suggest.words import load_wordlists

SAMPLE = SourceInfo(
    kind="soundcloud",
    id="soundcloud:tracks:1",
    url="https://soundcloud.com/kaia/midnight-drive",
    title="Song",
    uploader="Kaia",
)


def switched(suggestion: TagSuggestion, flips: dict[str, bool]) -> TagSuggestion:
    """The suggestion with some changes switched, as the review screen does."""
    changes = [
        replace(change, enabled=flips.get(change.id, change.enabled))
        for change in suggestion.changes
    ]
    return replace(suggestion, changes=changes)


def producer_claims(text: str) -> Draft:
    """A draft of `text` with every producer credit in it claimed, as the title rules claim them."""
    draft = Draft(text)
    for start in (index for index in range(len(text)) if text.startswith("(prod", index)):
        draft.claim("remove_producer", start, text.index(")", start) + 1)
    return draft


@pytest.mark.parametrize(
    ("title", "description", "producers"),
    [
        ("Song (prod. zayok & @edyn)", None, ["zayok", "edyn"]),
        ("Song (prod. dera x zayok)", None, ["zayok"]),
        ("Song", "for the homies\nproduced by zayok", ["zayok"]),
        ("Song", "zayok produced nothing here", []),
        ("Song (with zayok)", None, []),
        ("Song", "1. edyn - other song (prod. zayok)", []),
        ("Song", "beat by edyn, prod zayok, mix gunk", ["zayok"]),
    ],
)
def test_marked_producers_are_the_listed_names_a_producer_credit_names(
    title: str, description: str | None, producers: list[str]
) -> None:
    draft = producer_claims(title)
    listed = ["zayok", "edyn", "mara"]
    assert marked_producers(listed, draft, description, load_wordlists()) == producers


@pytest.mark.parametrize(
    ("name", "credit"),
    [("Prod. H2T", True), ("prod. by Hazy", True), ("Producer Kaia", False), ("Kaia", False)],
)
def test_a_name_can_itself_be_a_producer_credit(name: str, credit: bool) -> None:
    assert is_producer_credit(name, load_wordlists()) is credit


@pytest.mark.parametrize(
    ("names", "main", "featured", "label"),
    [
        (("Lou V",), ["Kaia"], ["Lou V"], "Lou V credited as featured artist"),
        (("Kaia",), ["Kaia"], [], "Removed “(feat. Kaia)”. Kaia is already a main artist."),
        (
            ("Harley Bird",),
            ["Rival, Cadmium, Harley Bird"],
            [],
            "Removed “(feat. Kaia)”. Harley Bird is already a main artist.",
        ),
        (
            ("Lou V", "Kaia"),
            ["Kaia"],
            ["Lou V"],
            "Lou V credited as featured artist. Kaia is already a main artist.",
        ),
        (
            ("Lou V", "Kaia"),
            ["Kaia", "Lou V"],
            [],
            "Removed “(feat. Kaia)”. Lou V and Kaia are already main artists.",
        ),
    ],
)
def test_a_feature_label_says_which_names_are_main_artists_already(
    names: tuple[str, ...], main: list[str], featured: list[str], label: str
) -> None:
    assert feature_label("(feat. Kaia)", names, main, featured) == label


def test_a_feature_of_the_uploader_says_it_only_removed_its_text() -> None:
    source = replace(SAMPLE, title="Song (feat. Kaia)")
    suggestion = suggest(source, Settings())

    assert suggestion.featured == []
    assert [change.label for change in suggestion.changes] == [
        "Removed “(feat. Kaia)”. Kaia is already a main artist."
    ]


def test_a_producer_credit_switched_on_features_the_producer() -> None:
    source = replace(SAMPLE, title="Song prod zayok", uploader="gunk", artists=["gunk, zayok"])
    suggestion = suggest(source, Settings())
    flipped = switched(suggestion, {"credit_producer:zayok": True})
    rebuilt = apply_changes(flipped, source, Settings())

    assert (suggestion.main_artists, suggestion.featured) == (["gunk"], [])
    assert (rebuilt.main_artists, rebuilt.featured) == (["gunk"], ["zayok"])
