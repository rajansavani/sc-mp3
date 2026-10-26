"""Tests for comparing, deduplicating and splitting artist names."""

import pytest

from scmp3.core.suggest.names import (
    any_same_artist,
    bare_name,
    channel_key,
    comma_parts,
    dedupe,
    dedupe_artists,
    fullest_spelling,
    has_stand_in,
    joined_names,
    preferred_spelling,
    same_artist,
    same_spelling,
    split_artist_list,
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


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("gunk, zayok, edyn ", ["gunk", "zayok", "edyn"]),
        ("Kaia, Lou V & Mara", ["Kaia", "Lou V", "Mara"]),
        ("Kaia, Lou V, and Mara", ["Kaia", "Lou V", "Mara"]),
        ("Kaia x Lou V", ["Kaia", "Lou V"]),
        ("Lil Nas X x Kaia", ["Lil Nas X", "Kaia"]),
        ("Kaia, , kaia, Lou V", ["Kaia", "Lou V"]),
    ],
)
def test_split_artist_list_splits_a_publisher_list_into_its_names(
    text: str, names: list[str]
) -> None:
    assert split_artist_list(text) == names


@pytest.mark.parametrize(
    "text",
    [
        "Chase & Status",
        "Simon and Garfunkel",
        "Kaia feat. Lou V",
        # Small capitals spelled out letter by letter, with an x among them.
        "\u1d0a x \ua731 \u1d1b \u1d22 \u1d07 \u0274",
    ],
)
def test_split_artist_list_keeps_a_name_without_a_comma_or_x_whole(text: str) -> None:
    assert split_artist_list(text) == [text]


def test_split_artist_list_never_splits_the_names_it_is_told_to_keep_whole() -> None:
    whole = ["tyler, the creator", "chase & status"]

    assert split_artist_list("Tyler, The Creator", whole) == ["Tyler, The Creator"]
    assert split_artist_list("Okoye, Chase & Status", whole) == ["Okoye", "Chase & Status"]
    assert split_artist_list("Okoye, Tyler, The Creator & Kaia", whole) == [
        "Okoye",
        "Tyler, The Creator",
        "Kaia",
    ]


def test_a_name_spelled_out_letter_by_letter_keeps_its_x() -> None:
    assert split_credit("j x s t z e n") == ["j x s t z e n"]
    assert split_names("j x s t z e n & Kaia") == ["j x s t z e n", "Kaia"]


def test_same_artist_ignores_symbols_at_the_edges_so_one_person_is_never_listed_twice() -> None:
    assert same_artist("Axurya \u2727", "Axurya")
    assert same_artist("\u2605 Kaia \u2605", "kaia")
    assert same_artist("Kaia\u2122", "Kaia")
    assert not same_spelling("Kaia\u2122", "Kaia")
    assert not same_artist("\U0001f525", "\U0001f319")


def test_bare_name_drops_symbols_and_their_marks_at_the_edges_only() -> None:
    assert bare_name("\u2661\u0303 Kaia \u2727") == "Kaia"
    assert bare_name("Kaia \u2716 Lou V") == "Kaia \u2716 Lou V"
    assert bare_name("$uicideboy$") == "$uicideboy$"


def test_split_artist_list_keeps_whole_names_spelled_with_and_or_without_a_space() -> None:
    whole = ["tyler, the creator", "simon & garfunkel"]

    assert split_artist_list("Okoye, Simon and Garfunkel", whole) == [
        "Okoye",
        "Simon and Garfunkel",
    ]
    assert split_artist_list("Tyler,The Creator, Kaia", whole) == ["Tyler,The Creator", "Kaia"]


def test_split_artist_list_splits_on_a_capital_x_only_between_names_in_capitals() -> None:
    assert split_artist_list("Kaia, Brand X Music") == ["Kaia", "Brand X Music"]
    assert split_artist_list("KAIA X LOU V") == ["KAIA", "LOU V"]


def test_bare_name_drops_a_symbol_and_its_mark_at_the_end() -> None:
    assert bare_name("Kaia \u2661\u0303") == "Kaia"


def test_same_spelling_ignores_variation_selectors() -> None:
    assert same_spelling("Kaia \u2661\ufe0e", "Kaia \u2661")
    assert same_spelling("Kaia \u2764\ufe0f", "Kaia \u2764")


def test_split_names_drop_invisible_characters_at_their_edges() -> None:
    assert split_artist_list("Lou V, \u200bKaia\u200e") == ["Lou V", "Kaia"]
    assert split_names("\u200eLou V\u200e & Mara") == ["Lou V", "Mara"]
