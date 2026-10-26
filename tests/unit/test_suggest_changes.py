"""Tests for suggest and apply_changes: change ids, toggles, labels, other fields and purity."""

import builtins
import io
import socket
from dataclasses import replace
from datetime import date

import pytest

from scmp3.core.models import Settings, SourceInfo, TagSuggestion
from scmp3.core.suggest import apply_changes, render, suggest
from scmp3.core.suggest.labels import change_label
from scmp3.core.suggest.words import load_wordlists

SAMPLE = SourceInfo(
    kind="soundcloud",
    id="soundcloud:tracks:1",
    url="https://soundcloud.com/kaia/midnight-drive",
    title="Midnight Drive (feat. Lou V) [prod. Hazy] FREE DL",
    uploader="Kaia",
    release_date=date(2026, 3, 1),
    upload_date=date(2025, 12, 31),
    genre="#Hip-hop",
)


def switched(suggestion: TagSuggestion, flips: dict[str, bool]) -> TagSuggestion:
    """The suggestion with some changes switched, as the review screen does."""
    changes = [
        replace(change, enabled=flips.get(change.id, change.enabled))
        for change in suggestion.changes
    ]
    return replace(suggestion, changes=changes)


def test_apply_changes_with_nothing_switched_gives_back_the_suggestion() -> None:
    suggestion = suggest(SAMPLE, Settings())

    assert apply_changes(suggestion, SAMPLE, Settings()) == suggestion


def test_change_ids_stay_the_same_when_other_changes_are_switched() -> None:
    suggestion = suggest(SAMPLE, Settings())
    first = suggestion.changes[0].id

    rebuilt = apply_changes(switched(suggestion, {first: False}), SAMPLE, Settings())

    assert [change.id for change in rebuilt.changes] == [change.id for change in suggestion.changes]
    assert [change.enabled for change in rebuilt.changes] == [False, True, True]


def test_apply_changes_keeps_fields_that_dont_come_from_the_title() -> None:
    suggestion = suggest(SAMPLE, Settings())
    in_a_set = replace(
        suggestion, album="Lantern", album_artist="Mara", track_no=3, track_total=9, year=2020
    )
    first = suggestion.changes[0].id

    rebuilt = apply_changes(switched(in_a_set, {first: False}), SAMPLE, Settings())

    assert (rebuilt.album, rebuilt.album_artist, rebuilt.track_no, rebuilt.track_total) == (
        "Lantern",
        "Mara",
        3,
        9,
    )
    assert rebuilt.year == 2020
    assert rebuilt.featured == []


def test_apply_changes_ignores_ids_it_doesnt_know() -> None:
    suggestion = suggest(SAMPLE, Settings())
    with_stale = replace(
        suggestion,
        changes=[*suggestion.changes, replace(suggestion.changes[0], id="remove_promo:gone")],
    )

    assert apply_changes(with_stale, SAMPLE, Settings()).changes == suggestion.changes


def test_a_custom_word_added_since_the_suggestion_starts_on() -> None:
    suggestion = suggest(SAMPLE, Settings())
    settings = Settings(custom_words=["Drive"])

    rebuilt = apply_changes(suggestion, SAMPLE, settings)

    assert rebuilt.base_title == "Midnight"
    assert "custom_word:Drive" in [change.id for change in rebuilt.changes if change.enabled]


def test_turning_off_a_publisher_feature_keeps_the_artist_name_whole() -> None:
    source = replace(SAMPLE, title="Midnight Drive", artists=["Kaia feat. Lou V"])
    suggestion = suggest(source, Settings())

    rebuilt = apply_changes(
        switched(suggestion, {"extract_feature:feat. Lou V": False}), source, Settings()
    )

    assert (rebuilt.main_artists, rebuilt.featured) == (["Kaia feat. Lou V"], [])


def test_repeated_changes_get_numbered_ids() -> None:
    source = replace(SAMPLE, title="\u2728 Midnight Drive \u2728")

    ids = [change.id for change in suggest(source, Settings()).changes]

    assert ids == ["remove_emoji:\u2728", "remove_emoji:\u2728#2"]


def test_labels_use_the_design_wording() -> None:
    labels = [change.label for change in suggest(SAMPLE, Settings()).changes]

    assert labels == [
        "Lou V credited as featured artist",
        "Removed “[prod. Hazy]”",
        "Removed “FREE DL”",
    ]


def test_a_feature_label_spells_each_name_as_the_tags_do() -> None:
    capitals = replace(SAMPLE, title="Kaia - Song (feat. LOU V)", artists=["Kaia", "Lou V"])
    accent = replace(SAMPLE, title="Song (feat. Zoe)", artists=["Kaia feat. Zo\u00eb"])
    lost = replace(
        SAMPLE,
        title="Crazy Love",
        uploader="Michael Bubl\u00e9",
        artists=["Kaia feat. Michael Bubl?"],
    )

    def feature_labels(source: SourceInfo) -> list[str]:
        changes = suggest(source, Settings()).changes
        return [change.label for change in changes if change.kind == "extract_feature"]

    assert feature_labels(capitals) == ["Lou V credited as featured artist"]
    assert feature_labels(accent) == ["Zo\u00eb credited as featured artist"]
    assert feature_labels(lost) == ["Michael Bubl\u00e9 credited as featured artist"]


def test_a_feature_label_stays_the_same_when_the_feature_is_turned_off() -> None:
    source = replace(SAMPLE, title="Song (feat. Zoe)", artists=["Kaia feat. Zo\u00eb"])
    suggestion = suggest(source, Settings())

    rebuilt = apply_changes(
        switched(suggestion, {"extract_feature:(feat. Zoe)": False}), source, Settings()
    )

    assert [change.label for change in rebuilt.changes] == [
        change.label for change in suggestion.changes
    ]


def test_a_credit_cut_short_by_a_version_shows_its_bracket_closed_in_the_label() -> None:
    source = replace(SAMPLE, title="Paper Moon (prod. Hazy (Extended Mix))")

    labels = [change.label for change in suggest(source, Settings()).changes]

    assert labels == ["Removed “(prod. Hazy)”"]


@pytest.mark.parametrize(
    ("kind", "span", "names", "label"),
    [
        ("extract_feature", "(feat. A & B)", ("A", "B"), "A and B credited as featured artists"),
        (
            "extract_feature",
            "ft. A, B & C",
            ("A", "B", "C"),
            "A, B and C credited as featured artists",
        ),
        ("artist_from_title", "Juice WRLD", (), "Took the artist “Juice WRLD” from the title"),
        ("strip_redundant_artist", "Kaia", (), "Removed the artist name “Kaia” from the title"),
        ("fix_caps", "LUCID DREAMS", (), "Changed “LUCID DREAMS” to title case"),
        ("custom_word", "Sessions", (), "Removed “Sessions”"),
    ],
)
def test_each_kind_of_change_has_a_plain_label(
    kind: str, span: str, names: tuple[str, ...], label: str
) -> None:
    assert change_label(kind, span, names) == label


def test_the_year_comes_from_the_release_date_then_the_upload_date() -> None:
    assert suggest(SAMPLE, Settings()).year == 2026
    assert suggest(replace(SAMPLE, release_date=None), Settings()).year == 2025
    assert suggest(replace(SAMPLE, release_date=None, upload_date=None), Settings()).year is None


def test_the_genre_loses_its_leading_hash() -> None:
    assert suggest(SAMPLE, Settings()).genre == "Hip-hop"
    assert suggest(replace(SAMPLE, genre=" # "), Settings()).genre is None


def test_the_album_comes_from_the_publisher_and_is_otherwise_left_to_the_setting() -> None:
    assert suggest(replace(SAMPLE, album="Lantern"), Settings()).album == "Lantern"
    assert suggest(replace(SAMPLE, album=""), Settings()).album is None


def test_an_emoji_only_title_keeps_its_emoji() -> None:
    suggestion = suggest(replace(SAMPLE, title="\U0001f525\U0001f525"), Settings())

    assert suggestion.base_title == "\U0001f525\U0001f525"
    assert [change.enabled for change in suggestion.changes] == [False]


def test_youtube_sources_get_the_same_general_rules() -> None:
    source = replace(SAMPLE, kind="youtube", title="Kaia - Midnight Drive (Official Video)")

    tags = render(suggest(source, Settings()), "artist_field", {}, Settings())

    assert (tags.title, tags.artist) == ("Midnight Drive", "Kaia")


def test_suggest_leaves_its_inputs_unchanged_and_gives_the_same_answer_twice() -> None:
    settings = Settings(custom_words=["Hazy"])
    source_before, settings_before = SAMPLE.to_dict(), settings.to_dict()

    first = suggest(SAMPLE, settings)
    second = suggest(SAMPLE, settings)

    assert first == second
    assert (SAMPLE.to_dict(), settings.to_dict()) == (source_before, settings_before)


def test_suggest_and_render_do_no_io_once_the_word_lists_are_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_wordlists()
    settings = Settings()

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the suggestion engine tried to open a file or a socket")

    with monkeypatch.context() as patched:
        for target, name in [(builtins, "open"), (io, "open"), (socket, "socket")]:
            patched.setattr(target, name, refuse)
        suggestion = suggest(SAMPLE, settings)
        tags = render(apply_changes(suggestion, SAMPLE, settings), None, {}, settings)

    assert tags.title == "Midnight Drive"


def test_a_feature_in_both_the_title_and_the_artist_field_is_one_toggle() -> None:
    source = replace(SAMPLE, title="Midnight Drive (feat. Lou V)", artists=["Kaia feat. Lou V"])
    suggestion = suggest(source, Settings())
    only = suggestion.changes[0]

    rebuilt = apply_changes(switched(suggestion, {only.id: False}), source, Settings())

    assert [change.id for change in suggestion.changes] == ["extract_feature:(feat. Lou V)"]
    assert (rebuilt.base_title, rebuilt.main_artists, rebuilt.featured) == (
        "Midnight Drive (feat. Lou V)",
        ["Kaia feat. Lou V"],
        [],
    )


def test_the_uploader_emoji_change_shows_switched_off_only_when_the_uploader_is_the_artist() -> (
    None
):
    moon = "Kaia \U0001f319"
    plain = replace(SAMPLE, title="Midnight Drive", uploader=moon)
    with_publisher = replace(plain, artists=["Kaia"])

    changes = suggest(plain, Settings()).changes
    assert [(change.kind, change.enabled) for change in changes] == [("remove_emoji", False)]
    assert suggest(plain, Settings()).main_artists == [moon]
    assert suggest(with_publisher, Settings()).changes == []


def test_an_uploader_name_with_emoji_still_matches_an_artist_prefix() -> None:
    moon = "Kaia \U0001f319"
    source = replace(SAMPLE, title="Kaia - Midnight Drive", uploader=moon)

    suggestion = suggest(source, Settings())

    assert (suggestion.base_title, suggestion.main_artists) == ("Midnight Drive", [moon])
    assert [(change.kind, change.enabled) for change in suggestion.changes] == [
        ("strip_redundant_artist", True),
        ("remove_emoji", False),
    ]


def test_a_bracketed_feature_put_back_into_a_title_artist_keeps_its_brackets() -> None:
    source = replace(
        SAMPLE,
        title="Drake (feat. Rihanna) - Take Care",
        uploader="Hazy",
        artists=["Kaia feat. Lou V"],
    )
    suggestion = suggest(source, Settings())

    rebuilt = apply_changes(
        switched(suggestion, {"extract_feature:(feat. Rihanna)": False}), source, Settings()
    )

    assert render(rebuilt, "artist_field", {}, Settings()).artist == (
        "Kaia, Drake (feat. Rihanna), Lou V"
    )


def test_an_album_from_the_source_loses_invisible_characters_at_its_edges() -> None:
    source = replace(SAMPLE, album="\u200eNight Shift\ufeff")

    assert suggest(source, Settings()).album == "Night Shift"


def test_an_album_of_only_invisible_characters_counts_as_missing() -> None:
    assert suggest(replace(SAMPLE, album="\u200e"), Settings()).album is None
