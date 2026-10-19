"""Tests for comparing, deduplicating and splitting artist names."""

import pytest

from scmp3.core.suggest.names import channel_key, dedupe, spacing_key, split_credit, split_names


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


def test_names_lose_only_unmatched_brackets() -> None:
    assert split_credit("Drake (feat. Rihanna)") == ["Drake (feat. Rihanna)"]
    assert split_names("(Lou V") == ["Lou V"]
    assert split_names("Lou V]") == ["Lou V"]


def test_dedupe_ignores_case_and_unicode_composition_and_keeps_the_first_spelling() -> None:
    composed, decomposed = "Zo\u00eb", "Zoe\u0308"

    assert dedupe([composed, "KAIA", decomposed, "kaia", " "]) == [composed, "KAIA"]


def test_spacing_key_ignores_case_and_spaces() -> None:
    assert spacing_key("Juice WRLD") == spacing_key("juicewrld")


def test_channel_key_also_ignores_punctuation() -> None:
    assert channel_key("Mr. Suicide-Sheep") == channel_key("mrsuicidesheep")
    assert channel_key("UKF Drum & Bass") == channel_key("ukfdrumbass")
