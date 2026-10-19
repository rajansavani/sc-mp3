"""Tests for comparing, deduplicating and splitting artist names."""

import pytest

from scmp3.core.suggest.names import (
    any_same_artist,
    channel_key,
    comma_parts,
    dedupe,
    dedupe_artists,
    fullest_spelling,
    has_stand_in,
    joined_names,
    preferred_spelling,
    same_artist,
    split_credit,
    split_names,
)


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("Lou V", ["Lou V"]),
        ("Lou V, Mara & Juno", ["Lou V", "Mara", "Juno"]),
        ("Lou V + Mara", ["Lou V", "Mara"]),
        ("Lou V x Mara", ["Lou V", "Mara"]),
        ("LOU V X MARA", ["LOU V", "MARA"]),
        ("Lou V \u00d7 Mara", ["Lou V", "Mara"]),
        ("Simon and Garfunkel", ["Simon and Garfunkel"]),
        ("Lil Nas X & Mara", ["Lil Nas X", "Mara"]),
        ("Lil Nas X x Mara", ["Lil Nas X", "Mara"]),
        ("Lou V, lou v", ["Lou V"]),
        ("Lou V, , ", ["Lou V"]),
        ("Kaia\uff09", ["Kaia"]),
    ],
)
def test_split_names_splits_featured_names_on_comma_ampersand_plus_and_spaced_x(
    text: str, names: list[str]
) -> None:
    assert split_names(text) == names


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("Chase & Status", ["Chase & Status"]),
        ("Tyler, The Creator", ["Tyler, The Creator"]),
        ("Kaia vs. Lou V", ["Kaia vs. Lou V"]),
        ("Kaia x Lou V", ["Kaia", "Lou V"]),
        ("Kaia + Lou V", ["Kaia", "Lou V"]),
        ("Chase & Status x Kaia", ["Chase & Status", "Kaia"]),
    ],
)
def test_split_credit_keeps_ampersands_and_commas_whole(text: str, names: list[str]) -> None:
    assert split_credit(text) == names


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("Lou V (London, UK)", ["Lou V (London, UK)"]),
        ("Lou V (UK & Ireland), Mara", ["Lou V (UK & Ireland)", "Mara"]),
        ("Okoye (Lagos) & Lou V [UK + IE]", ["Okoye (Lagos)", "Lou V [UK + IE]"]),
        ("A (B (C, D)) & E", ["A (B (C, D))", "E"]),
        ("Lou V (UK), Lou V (UK)", ["Lou V (UK)"]),
    ],
)
def test_split_names_keeps_each_bracket_whole(text: str, names: list[str]) -> None:
    assert split_names(text) == names


def test_names_lose_only_unmatched_brackets() -> None:
    assert split_credit("Drake (feat. Rihanna)") == ["Drake (feat. Rihanna)"]
    assert split_names("(Lou V") == ["Lou V"]
    assert split_names("Lou V]") == ["Lou V"]


def test_dedupe_ignores_case_and_unicode_composition_and_keeps_the_first_spelling() -> None:
    composed, decomposed = "Zo\u00eb", "Zoe\u0308"

    assert dedupe([composed, "KAIA", decomposed, "kaia", " "]) == [composed, "KAIA"]


def test_channel_key_also_ignores_punctuation() -> None:
    assert channel_key("Mr. Suicide-Sheep") == channel_key("mrsuicidesheep")
    assert channel_key("UKF Drum & Bass") == channel_key("ukfdrumbass")


@pytest.mark.parametrize(
    ("name", "other"),
    [
        ("Juice WRLD", "juicewrld"),
        ("Michael Bubl\u00e9", "Michael Buble"),
        ("Zoe\u0308", "ZO\u00cb"),
        ("Michael Bubl?", "Michael Buble"),
        ("Michael Bubl?", "Michael Bubl\u00e9"),
        ("Michael Bubl\ufffd", "Michael Buble"),
        ("?uestlove", "Questlove"),
    ],
)
def test_same_artist_ignores_case_spacing_accents_and_stand_ins_for_a_letter(
    name: str, other: str
) -> None:
    assert same_artist(name, other)
    assert same_artist(other, name)


@pytest.mark.parametrize(
    ("name", "other"),
    [
        ("Kaia", "Kaio"),
        ("Michael Bubl?", "Michael Bubl"),
        ("Michael Bubl?", "Michael Bublee"),
        ("Who?", "Who!"),
        ("Who?", "Who "),
        ("???", "Lou"),
    ],
)
def test_same_artist_needs_the_same_letters_with_a_stand_in_matching_only_one_letter(
    name: str, other: str
) -> None:
    assert not same_artist(name, other)


def test_has_stand_in_finds_a_question_mark_or_a_replacement_character() -> None:
    assert has_stand_in("Michael Bubl?")
    assert has_stand_in("Michael Bubl\ufffd")
    assert not has_stand_in("Michael Bubl\u00e9")


def test_any_same_artist_matches_any_name_against_any_other() -> None:
    assert any_same_artist(["Kaia", "Zoe"], ["Lou V", "Zo\u00eb"])
    assert not any_same_artist(["Kaia"], ["Lou V"])
    assert not any_same_artist(["Kaia"], [])


def test_preferred_spelling_is_the_first_unless_it_lost_a_letter_the_second_has() -> None:
    assert preferred_spelling("Zo\u00eb", "Zoe") == "Zo\u00eb"
    assert preferred_spelling("Michael Bubl?", "Michael Buble") == "Michael Buble"
    assert preferred_spelling("Michael Buble", "Michael Bubl?") == "Michael Buble"
    assert preferred_spelling("Michael Bubl?", "Michael Bubl\ufffd") == "Michael Bubl?"


def test_fullest_spelling_prefers_no_lost_letter_then_accents_then_the_first() -> None:
    assert fullest_spelling("Zoe", "Zo\u00eb") == "Zo\u00eb"
    assert fullest_spelling("Zo\u00eb", "Zoe") == "Zo\u00eb"
    assert fullest_spelling("Michael Bubl?", "Michael Buble") == "Michael Buble"
    assert fullest_spelling("Michael Bubl\u00e9", "Michael Bubl?") == "Michael Bubl\u00e9"
    assert fullest_spelling("KAIA", "Kaia") == "KAIA"


def test_dedupe_artists_keeps_the_spelling_the_given_rule_prefers() -> None:
    assert dedupe_artists(["Zoe", "Zo\u00eb"], fullest_spelling) == ["Zo\u00eb"]
    assert dedupe_artists(["Zoe", "Zo\u00eb"]) == ["Zoe"]


def test_dedupe_artists_counts_near_spellings_once_and_fills_in_a_lost_letter() -> None:
    names = ["Michael Bubl?", "Kaia", " ", "kaia", "Michael Bubl\u00e9", "Zoe", "Zo\u00eb"]

    assert dedupe_artists(names) == ["Michael Bubl\u00e9", "Kaia", "Zoe"]


def test_joined_names_splits_a_credit_on_every_joiner_for_matching() -> None:
    assert joined_names("Tyler, The Creator & Kaia") == ["Tyler", "The Creator", "Kaia"]
    assert joined_names("Kaia x Lou V + Mara") == ["Kaia", "Lou V", "Mara"]
    assert joined_names("Kaia &") == ["Kaia"]


def test_comma_parts_lists_the_names_between_commas_for_matching_only() -> None:
    assert comma_parts("Rival, Cadmium, Harley Bird") == ["Rival", "Cadmium", "Harley Bird"]
    assert comma_parts("Tyler, The Creator") == ["Tyler", "The Creator"]
    assert comma_parts("Chase & Status") == []
