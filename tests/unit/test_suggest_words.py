"""Tests for the word lists and the whole-word patterns built from them."""

import tomllib
from importlib import resources

import pytest

from scmp3.core.suggest.words import NEVER, alternation, load_wordlists, pattern, phrase_pattern

# Lists matched exactly as written, so their case and punctuation matter.
EXACT_LISTS = {"channels_exact"}


def all_entries() -> list[str]:
    """Every entry in wordlists.toml that's matched ignoring case."""
    source = resources.files("scmp3.core.suggest").joinpath("wordlists.toml")
    data = tomllib.loads(source.read_text(encoding="utf-8"))
    return [
        entry
        for table in data.values()
        for name, entries in table.items()
        if name not in EXACT_LISTS
        for entry in entries
    ]


def test_the_word_lists_are_loaded_once() -> None:
    assert load_wordlists() is load_wordlists()


def test_word_list_entries_are_lowercase_and_trimmed() -> None:
    entries = all_entries()

    assert [entry for entry in entries if entry != entry.strip().lower()] == []


@pytest.mark.parametrize(
    ("phrase", "text", "matches"),
    [
        ("ft", "Left Behind", False),
        ("feat", "Defeat Me", False),
        ("ft", "Gift Of Gab", False),
        ("feat", "Featherweight", False),
        ("ft", "Song ft Drake", True),
        ("free dl", "FREE  DL", True),
        ("free dl", "freedl", False),
        ("buy = free dl", "BUY=FREE DL", True),
        ("prod.", "prod.Hazy", True),
        ("prod", "Prodigy", False),
        ("w/", "(w/Lou V)", True),
    ],
)
def test_phrases_match_whole_words_only(phrase: str, text: str, matches: bool) -> None:
    assert bool(pattern(phrase_pattern(phrase)).search(text)) is matches


def test_alternation_tries_longer_phrases_first() -> None:
    match = pattern(alternation(["feat", "feat."])).search("Song feat. Drake")

    assert match is not None
    assert match.group() == "feat."


def test_an_empty_alternation_never_matches() -> None:
    assert alternation([" ", ""]) == NEVER
    assert pattern(NEVER).search("anything") is None
