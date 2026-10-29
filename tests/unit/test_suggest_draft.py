"""Tests for the draft a title is cleaned up in: its bracketed groups and emoticons."""

import pytest

from scmp3.core.suggest.assembly import assemble
from scmp3.core.suggest.draft import CUT, Draft, is_emoticon, spaced_dashes


def spans(draft: Draft, groups: str) -> list[str]:
    """The text of each group `draft` finds, using its `groups` or `nested_groups` method."""
    return [draft.text[group.start : group.end] for group in getattr(draft, groups)()]


def test_groups_finds_only_the_innermost_brackets() -> None:
    draft = Draft("Song (feat. Lou V (UK)) [VIP]")

    assert spans(draft, "groups") == ["(UK)", "[VIP]"]


def test_nested_groups_finds_each_balanced_group_with_brackets_inside() -> None:
    draft = Draft("Song (feat. Lou V (UK)) [feat. A (B) & C (D)] (x (y (z)))")

    assert spans(draft, "nested_groups") == [
        "(feat. Lou V (UK))",
        "[feat. A (B) & C (D)]",
        "(y (z))",
        "(x (y (z)))",
    ]


@pytest.mark.parametrize(
    "title",
    ["Song (feat. Lou V (UK)", "Song [feat. Lou V (UK]", "Song (VIP)", "Song :( (feat. A)"],
)
def test_nested_groups_skips_unbalanced_brackets_and_plain_groups(title: str) -> None:
    assert spans(Draft(title), "nested_groups") == []


def test_nested_groups_skips_claimed_text() -> None:
    draft = Draft("Song (feat. Lou V (UK))")
    draft.claim("remove_promo", 18, 22)

    # Once "(UK)" is claimed, the outer bracket has no brackets left inside.
    assert spans(draft, "nested_groups") == []
    assert spans(draft, "groups") == ["(feat. Lou V (UK))"]


def test_nested_groups_ignores_emoticon_brackets() -> None:
    assert spans(Draft("Song (feat. A :) )"), "nested_groups") == []
    assert spans(Draft("Song (feat. A (B) :( )"), "nested_groups") == ["(feat. A (B) :( )"]


def claimed(draft: Draft) -> list[tuple[str, str]]:
    """Each claim's kind and text, in text order."""
    in_order = sorted(draft.claims, key=lambda claim: claim.start)
    return [(claim.kind, draft.span(claim)) for claim in in_order]


def test_claim_credit_takes_a_credit_bracket_whole() -> None:
    draft = Draft("Song (feat. Lou V (UK))")

    assert draft.claim_credit("extract_feature", draft.nested_groups()[0], names=("Lou V (UK)",))
    assert claimed(draft) == [("extract_feature", "(feat. Lou V (UK))")]


def test_claim_credit_leaves_a_version_that_ends_the_bracket_to_the_version() -> None:
    draft = Draft("Song (feat. Lou V (VIP Mix))")
    draft.claim("version", 18, 27)

    assert draft.claim_credit("extract_feature", draft.groups()[0], names=("Lou V",))
    assert claimed(draft) == [
        ("extract_feature", "(feat. Lou V"),
        ("version", "(VIP Mix)"),
        ("version", ")"),
    ]


def test_claim_credit_leaves_an_inner_producer_credit_its_own_claim() -> None:
    draft = Draft("Song (feat. Lou V (prod. Hazy))")
    draft.claim("remove_producer", 18, 30)

    assert draft.claim_credit("extract_feature", draft.groups()[0], names=("Lou V",))
    # The closing bracket is left to tidying, which drops it.
    assert claimed(draft) == [
        ("extract_feature", "(feat. Lou V"),
        ("remove_producer", "(prod. Hazy)"),
    ]


def test_claim_credit_takes_the_whole_group_around_a_version_in_the_middle() -> None:
    draft = Draft("Song (feat. Lou V (VIP Mix) & Mara)")
    draft.claim("version", 18, 27)

    assert draft.claim_credit("extract_feature", draft.groups()[0], names=("Lou V", "Mara"))
    assert claimed(draft) == [
        ("extract_feature", "(feat. Lou V (VIP Mix) & Mara)"),
        ("version", "(VIP Mix)"),
    ]


def test_claim_credit_refuses_a_group_around_a_claim_that_isnt_a_version() -> None:
    draft = Draft("Song (feat. Lou V (prod. Hazy) & Mara)")
    draft.claim("remove_producer", 18, 30)

    assert not draft.claim_credit("extract_feature", draft.groups()[0], names=("Lou V",))
    assert claimed(draft) == [("remove_producer", "(prod. Hazy)")]


def test_assemble_moves_a_version_inside_a_cut_credit_to_the_version() -> None:
    draft = Draft("Song (feat. Lou V (VIP Mix) & Mara)")
    draft.claim("version", 18, 27)
    draft.claim_credit("extract_feature", draft.groups()[0], names=("Lou V", "Mara"))

    assert assemble(draft, cut=lambda claim: True) == (f"Song {CUT}", " (VIP Mix)")
    assert assemble(draft, cut=lambda claim: False) == (draft.text, "")


def test_assemble_moves_the_versions_it_is_given_to_the_version() -> None:
    draft = Draft("AEREA (live) | Set")
    draft.claim("version", 6, 12)

    assert assemble(draft, cut=lambda claim: True, moved={6}) == (f"AEREA {CUT} | Set", " (live)")


def test_is_emoticon_spots_a_bracket_after_a_colon_or_semicolon() -> None:
    assert is_emoticon("i miss u :(", 10)
    assert is_emoticon("hi ;-)", 5)
    assert not is_emoticon("Song (VIP)", 5)


def test_spaced_dashes_skips_a_dash_inside_brackets() -> None:
    text = "gg fuckin ez \u0d26\u0d4d\u0d26\u0d3f(\u02f5 \u2022\u0300 \u1d17 - \u02f5 ) - Kaia"

    assert spaced_dashes(text) == [text.rindex(" - ") + 1]


def test_spaced_dashes_counts_a_dash_after_an_unclosed_or_emoticon_bracket() -> None:
    assert spaced_dashes("Kaia (VIP - Song") == [10]
    assert spaced_dashes("Kaia :( - Song") == [8]


def test_assemble_marks_a_closer_whose_opener_was_cut() -> None:
    draft = Draft("Song (feat. Lou V (prod. Hazy))")
    draft.claim("extract_feature", 5, 17)

    title, _version = assemble(draft, cut=lambda claim: True)

    assert title == f"Song {CUT} (prod. Hazy){CUT})"
