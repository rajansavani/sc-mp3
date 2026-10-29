"""The word lists in `wordlists.toml`, loaded once and turned into regular expressions."""

import functools
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources

from scmp3.core.suggest.names import channel_key

# A pattern that never matches, for an empty list.
NEVER = r"(?!)"


@dataclass(frozen=True)
class Wordlists:
    """Each list as a regex alternation of whole words, ready to go in a bigger pattern."""

    promo: str
    promo_tail: str
    promo_bracket_only: str
    promo_bracket_endings: str
    producer: str
    producer_remix: str  # words that make a producer credit a remix
    feature: str
    feature_bracketed: str
    not_names: frozenset[str]  # casefolded
    uncertain_after: str
    part_words: str
    keep: str  # every keep word, for brackets
    keep_strong: str  # the keep words that protect a bracket from promo removal
    keep_dash: str  # the keep words that also mark a " - " ending as a version
    keep_leads: str
    made_by: str  # the keep words after the name of whoever made the version
    label_words: str
    channels: frozenset[str]  # channel keys
    channels_exact: frozenset[str]  # names matched exactly as written
    domains: str  # top-level domains, dot included
    together: str
    whole_names: tuple[str, ...]  # matched ignoring case
    caps_keep: frozenset[str]  # uppercase
    video_labels: str  # YouTube's, marking another artist's song


@functools.cache
def load_wordlists() -> Wordlists:
    """Read `wordlists.toml` from the package. Cached, so the file is read once per process."""
    source = resources.files(__package__).joinpath("wordlists.toml")
    data = tomllib.loads(source.read_text(encoding="utf-8"))
    keep, promo, features = data["keep"], data["promo"], data["features"]
    return Wordlists(
        promo=alternation(promo["phrases"]),
        promo_tail=alternation(promo["with_tail"]),
        promo_bracket_only=alternation(promo["bracket_only"]),
        promo_bracket_endings=alternation(promo["bracket_endings"]),
        producer=alternation(data["producer"]["keywords"]),
        producer_remix=alternation(data["producer"]["remixes"]),
        feature=alternation(features["keywords"]),
        feature_bracketed=alternation(features["bracket_keywords"]),
        not_names=frozenset(word.casefold() for word in features["not_names"]),
        uncertain_after=alternation(features["uncertain_after"]),
        part_words=alternation(features["part_words"]),
        keep=alternation([*keep["words"], *keep["bracket_only"]]),
        keep_strong=alternation(
            word for word in [*keep["words"], *keep["bracket_only"]] if word not in keep["weak"]
        ),
        keep_dash=alternation(keep["words"]),
        keep_leads=alternation(keep["leads"]),
        made_by=alternation(keep["made_by"]),
        label_words=alternation(data["labels"]["words"]),
        channels=frozenset(channel_key(name) for name in data["labels"]["channels"]),
        channels_exact=frozenset(data["labels"]["channels_exact"]),
        domains=alternation(data["labels"]["domains"]),
        together=alternation(data["artists"]["together"]),
        whole_names=tuple(data["artists"]["whole_names"]),
        caps_keep=frozenset(word.upper() for word in data["caps"]["keep"]),
        video_labels=alternation(data["youtube"]["video_labels"]),
    )


def alternation(phrases: Iterable[str]) -> str:
    """One group matching any of `phrases` as whole words, longest first so "feat." beats "feat"."""
    unique = sorted({phrase.strip() for phrase in phrases if phrase.strip()}, key=len, reverse=True)
    if not unique:
        return NEVER
    return "(?:" + "|".join(phrase_pattern(phrase) for phrase in unique) + ")"


def phrase_pattern(phrase: str) -> str:
    """A regex for `phrase` as whole words, allowing any run of spaces between its words."""
    words = phrase.split()
    pattern = ""
    for index, word in enumerate(words):
        if index:
            # "buy = free dl" also matches "BUY=FREE DL", but "free dl" never matches "freedl".
            both_wordy = _is_wordy(words[index - 1][-1]) and _is_wordy(word[0])
            pattern += r"\s+" if both_wordy else r"\s*"
        pattern += re.escape(word)
    # Only edges that are letters or digits need a boundary; "prod." already ends at its dot.
    if _is_wordy(phrase[0]):
        pattern = r"(?<!\w)" + pattern
    if _is_wordy(phrase[-1]):
        pattern += r"(?!\w)"
    return pattern


def _is_wordy(char: str) -> bool:
    """Whether `char` counts as part of a word in regex terms."""
    return char.isalnum() or char == "_"


@functools.cache
def pattern(source: str) -> re.Pattern[str]:
    """Compile a case-insensitive pattern once, since the rules build them from the word lists."""
    return re.compile(source, re.IGNORECASE)
