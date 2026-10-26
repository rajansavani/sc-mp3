"""Tests for the title rules: normalizing, the keep list, capitals and the final tidy."""

import pytest

from scmp3.core.suggest.draft import CUT
from scmp3.core.suggest.rules import (
    balance_brackets,
    carry_open_bracket,
    ends_with_stray_closer,
    is_all_caps,
    is_bare_version,
    is_dash_version,
    normalize,
    tidy,
    tidy_version,
    title_case,
)
from scmp3.core.suggest.words import load_wordlists

WORDS = load_wordlists()


def test_normalize_collapses_whitespace_including_tabs_and_no_break_spaces() -> None:
    assert normalize("  Midnight\t\tDrive\u00a0 (VIP)\n") == "Midnight Drive (VIP)"


def test_normalize_reads_en_and_em_dashes_as_hyphens() -> None:
    assert normalize("Kaia \u2013 Midnight Drive \u2014 VIP") == "Kaia - Midnight Drive - VIP"


@pytest.mark.parametrize(
    ("messy", "clean"),
    [
        ("Midnight Drive ()", "Midnight Drive"),
        ("Midnight Drive ( [ ] )", "Midnight Drive"),
        ("Midnight Drive -", "Midnight Drive"),
        ("- Midnight Drive |", "Midnight Drive"),
        ("Midnight Drive //", "Midnight Drive"),
        ("Midnight Drive :", "Midnight Drive"),
        ("Kaia -  - Midnight Drive", "Kaia - Midnight Drive"),
        (f"Midnight  Drive ({CUT} VIP {CUT})", "Midnight Drive (VIP)"),
        ("Midnight Drive (VIP", "Midnight Drive (VIP)"),
        ("Midnight Drive (", "Midnight Drive"),
        (f"Midnight Drive{CUT}) (VIP)", "Midnight Drive (VIP)"),
        (f"\u2661 {CUT} - Kiss Me Again \u2661", "\u2661 Kiss Me Again \u2661"),
        (f"{CUT} \u2661 - Kiss Me Again", "\u2661 Kiss Me Again"),
        ("\u2661 - Kiss Me Again", "\u2661 - Kiss Me Again"),
        ("Re-Run", "Re-Run"),
        ("PREMIERE: - Midnight Drive", "PREMIERE: Midnight Drive"),
    ],
)
def test_tidy_drops_empty_brackets_and_dangling_separators(messy: str, clean: str) -> None:
    assert tidy(messy) == clean


@pytest.mark.parametrize(
    "typed",
    [
        "Midnight Drive ( VIP )",
        "gg fuckin ez \u0d26\u0d4d\u0d26\u0d3f(\u02f5 \u2022\u0300 \u1d17 - \u02f5 ) \u2727",
        "so \u0d26\u0d4d\u0d26\u0d3f \u0f0e\u0eb6\u203f\u0f0e\u0eb6 ) mandy cover",
        "Crazy\u0d26\u0d4d\u0d26\u0d3f \u02c9\u0348\u0300\ua4b3\u02c9\u0348\u0301 )\u2727",
    ],
)
def test_tidy_keeps_faces_as_typed_with_their_spaces_and_lone_closers(typed: str) -> None:
    assert tidy(typed) == typed


@pytest.mark.parametrize(
    "emoticon",
    ["i miss u :(", "Midnight Drive :)", "Midnight Drive ;)", "Midnight Drive :-)", ":) hi"],
)
def test_tidy_leaves_emoticons_alone(emoticon: str) -> None:
    assert tidy(emoticon) == emoticon


def test_tidy_version_without_a_space_keeps_it_that_way() -> None:
    assert tidy_version("\uff08Kaia Remix\uff09", spaced=False) == "\uff08Kaia Remix\uff09"


def test_tidy_version_keeps_its_leading_separator() -> None:
    assert tidy_version("- VIP Mix  ") == " - VIP Mix"
    assert tidy_version("(VIP Mix) -") == " (VIP Mix)"


def test_tidy_version_is_none_without_words() -> None:
    assert tidy_version(" - ") is None
    assert tidy_version("") is None


def test_balance_brackets_closes_nested_brackets_in_order() -> None:
    assert balance_brackets("Song (VIP [Edit") == "Song (VIP [Edit])"


def test_carry_open_bracket_moves_a_bracket_the_version_closes_into_the_version() -> None:
    assert carry_open_bracket("Song (feat. Lou V ", "(VIP Mix))") == (
        "Song ",
        "(feat. Lou V (VIP Mix))",
    )


@pytest.mark.parametrize(
    ("title", "version"),
    [
        ("Song ", "(VIP Mix))"),
        ("Song (feat. Lou V ", "(VIP Mix)"),
        ("Song [feat. Lou V ", "(VIP Mix))"),
        ("Song :( ", "(VIP Mix))"),
    ],
)
def test_carry_open_bracket_leaves_brackets_the_version_doesnt_close(
    title: str, version: str
) -> None:
    assert carry_open_bracket(title, version) == (title, version)


def test_ends_with_stray_closer_spots_a_closing_bracket_with_no_opener() -> None:
    assert ends_with_stray_closer("Kaia      )")
    assert ends_with_stray_closer("Kaia ) ")
    assert not ends_with_stray_closer("Kaia (UK)")
    assert not ends_with_stray_closer("Kaia :)")
    assert not ends_with_stray_closer("Kaia")


@pytest.mark.parametrize(
    "segment",
    [
        "VIP Mix",
        "Lou V Remix",
        "Radio Edit",
        "Part 2",
        "Pt. II",
        "Remastered 2011",
        "Live at Wembley",
    ],
)
def test_a_dash_ending_with_a_keep_word_is_a_version(segment: str) -> None:
    assert is_dash_version(segment, WORDS)


@pytest.mark.parametrize(
    "segment",
    ["Lucid Dreams", "Intro", "Live Forever", "Part of Me", "One Two Three Four Five Six Remix"],
)
def test_a_dash_ending_without_a_keep_word_is_not_a_version(segment: str) -> None:
    assert not is_dash_version(segment, WORDS)


@pytest.mark.parametrize(
    ("segment", "bare"),
    [
        ("VIP Mix", True),
        ("Extended Mix", True),
        ("Part 2", True),
        ("Slowed + Reverb", True),
        ("Lou V Remix", False),
        ("Essential Mix 2023", False),
        ("Live at Wembley", False),
    ],
)
def test_a_bare_version_ending_is_only_version_words(segment: str, bare: bool) -> None:
    assert is_bare_version(segment, WORDS) is bare


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("LUCID DREAMS", True),
        ("Lucid DREAMS", False),
        ("A", False),
        ("4K", False),
        ("\u591c\u306b", False),
        ("\u591c\u306b\u99c6\u3051\u308b\u3010MV\u3011", False),
    ],
)
def test_is_all_caps_needs_two_capitals_that_are_most_of_the_letters(
    text: str, expected: bool
) -> None:
    assert is_all_caps(text) is expected


@pytest.mark.parametrize(
    ("caps", "titled"),
    [
        ("LUCID DREAMS", "Lucid Dreams"),
        ("DON'T STOP", "Don't Stop"),
        ("HIP-HOP", "Hip-hop"),
        ("(VIP MIX)", "(VIP Mix)"),
        ("DJ SHADOW", "DJ Shadow"),
        ("ROOM 2B", "Room 2B"),
        ("\u00c9T\u00c9", "\u00c9t\u00e9"),
        ("\u300cHELLO\u300d", "\u300cHello\u300d"),
    ],
)
def test_title_case_capitalizes_words_and_keeps_listed_ones_in_capitals(
    caps: str, titled: str
) -> None:
    assert title_case(caps, WORDS.caps_keep) == titled


def test_normalize_drops_invisible_characters_at_the_edges_only() -> None:
    assert normalize("\u200e\u200b Kaia\u2060 Lou V \ufeff") == "Kaia\u2060 Lou V"
    assert normalize("\u200f") == ""


def test_normalize_keeps_the_tags_that_end_a_flag() -> None:
    england = "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"

    assert normalize(f"Song {england}") == f"Song {england}"
