"""Steps 1-3 and 6-8 of the suggestion engine: normalizing, promo and noise, producer credits,
the keep list, emoji and capitals, and the final tidy."""

import re

from scmp3.core.suggest.draft import (
    BRACKET_PAIRS,
    CLOSERS,
    NOT_EMOTICON,
    OPENERS,
    VERSION,
    Context,
    Draft,
    has_words,
    is_emoticon,
    names_pattern,
)
from scmp3.core.suggest.words import Wordlists, pattern, phrase_pattern

_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-"})
_ANY_BRACKET = re.escape(OPENERS + CLOSERS)
# Separators that can be left dangling once part of the title is gone.
_SEPARATOR = r"[-|/:]"
PIPES = re.compile("[|\uff5c]")
_EMPTY_GROUP = re.compile(
    "|".join(rf"{NOT_EMOTICON}{re.escape(o)}\s*{re.escape(c)}" for o, c in BRACKET_PAIRS.items())
)
_SPACE_AFTER_OPENER = re.compile(rf"{NOT_EMOTICON}([{re.escape(OPENERS)}])\s+")
_SPACE_BEFORE_CLOSER = re.compile(rf"\s+([{re.escape(CLOSERS)}])")
# A run of separators collapses to its first, so "PREMIERE: - Song" becomes "PREMIERE: Song".
_REPEATED_SEPARATORS = re.compile(rf"(\s?{_SEPARATOR}+)(?:\s+{_SEPARATOR}+)+(?=\s|$)")
# A separator that starts an emoticon, like the ":" in ":)", isn't trimmed.
_LEADING_SEPARATORS = re.compile(rf"^(?:\s*{_SEPARATOR}++(?![{_ANY_BRACKET}]))+\s*")
_TRAILING_SEPARATORS = re.compile(rf"(?:\s*{_SEPARATOR}+)+\s*$")
# Trailing numbers don't stop " - Part 2" or " - Remastered 2011" from reading as a version.
_NUMBER = r"(?:\d+|i{1,3}|iv|vi{0,3}|ix|x)"
_HASHTAG = r"#[^\W\d_]\w*"
# Emoji blocks, leaving out the sharp, flat and natural signs (U+266D to U+266F).
_EMOJI = "[\U0001f000-\U0001faff\u2300-\u23ff\u2600-\u266c\u2670-\u27bf\u2b00-\u2bff]"
# Joiners, variation selectors and flag tags that glue emoji together.
_EMOJI_GLUE = "[\u200d\ufe0e\ufe0f\U000e0020-\U000e007f]"
# A keycap emoji is a digit, "#" or "*", an optional variation selector, then U+20E3.
_KEYCAP = "[0-9#*]\ufe0f?\u20e3"
_EMOJI_ATOM = f"(?:{_KEYCAP}|{_EMOJI}{_EMOJI_GLUE}*)"
_EMOJI_RUN = re.compile(f"{_EMOJI_ATOM}(?:\\s*{_EMOJI_ATOM})*")


def normalize(title: str) -> str:
    """Step 1: collapse whitespace and read en and em dashes as hyphens."""
    return " ".join(title.translate(_DASHES).split())


def claim_bracket_versions(draft: Draft, context: Context) -> None:
    """Step 6 in brackets: a group with a keep word, like "(Kaia Remix)", is kept as the version."""
    words = context.words
    keep = pattern(words.keep)
    strong_keep = pattern(words.keep_strong)
    credit = pattern(rf"^\W*(?:{words.feature}|{words.feature_bracketed}|{words.producer})")
    promo = pattern(f"{words.promo}|{words.promo_tail}")
    feature_inside = pattern(rf"\s{words.feature}\s*:?\s*\S")
    for group in draft.groups():
        inner = group.inner
        # "(feat. Little Mix)" and "[prod. Mix Master]" are credits, whatever names they hold.
        if not keep.search(inner) or credit.search(inner):
            continue
        # "Full Version" doesn't save "[Free Download - Full Version]" from promo removal.
        if promo.search(inner) and not strong_keep.search(inner):
            continue
        feature = feature_inside.search(inner)
        if feature is None or not keep.search(inner[: feature.start()]):
            draft.claim(VERSION, group.start, group.end)
            continue
        # In "(Kaia Remix feat. Lou V)", the feature is left for the feature rule to claim.
        draft.claim(VERSION, group.start, group.start + 1 + feature.start())
        draft.claim(VERSION, group.end - 1, group.end)


def is_dash_version(segment: str, words: Wordlists) -> bool:
    """Step 6 after a dash: whether a " - " ending like "Lou V Remix" or "Part 2" is a version."""
    if len(segment.split()) > 6:
        return False
    ends = pattern(rf"{words.keep_dash}(?:\s+{_NUMBER})*\W*$")
    leads = pattern(rf"^\W*{words.keep_leads}")
    return bool(ends.search(segment) or leads.search(segment))


def is_bare_version(segment: str, words: Wordlists) -> bool:
    """Whether a " - " ending is only version words, like "VIP Mix" but not "Lou V Remix"."""
    word = rf"(?:{words.keep_dash}|{_NUMBER}|[+&/])"
    return bool(pattern(rf"^\W*{word}(?:\s+{word})*\W*$").search(segment))


def claim_bracket_producers(draft: Draft, context: Context) -> None:
    """Step 3 in brackets: "(prod. Hazy)" and "[produced by Hazy]"."""
    credit = pattern(rf"^\s*{context.words.producer}\s*:?\s*\S")
    # A credit with brackets inside, like "(prod. Hazy (UK))", is taken whole.
    for group in [*draft.groups(), *draft.nested_groups()]:
        if credit.search(group.inner):
            draft.claim_credit("remove_producer", group)


def claim_inline_producers(draft: Draft, context: Context) -> None:
    """Step 3 outside brackets: "prod. Hazy" up to a dash, a bracket, a feature or the end."""
    words = context.words
    regex = pattern(rf"{words.producer}\s*:?\s*{names_pattern(words.feature)}")
    for start, end, _names in draft.inline_matches(regex):
        draft.claim("remove_producer", start, end)


def claim_bracket_promo(draft: Draft, context: Context) -> None:
    """Step 2 in brackets: "[FREE DL]", "(Official Video)", "[Monstercat Release]"."""
    words = context.words
    inside = pattern(f"{words.promo}|{words.promo_tail}")
    whole = pattern(rf"^\s*\**{words.promo_bracket_only}\**\s*$")
    ending = pattern(rf"{words.promo_bracket_endings}\W*$")
    for group in draft.groups():
        inner = group.inner
        if inside.search(inner) or whole.search(inner) or ending.search(inner):
            draft.claim("remove_promo", group.start, group.end)


def claim_leading_promo(draft: Draft, context: Context) -> None:
    """Step 2 at the start, before a separator: "EXCLUSIVE PREMIERE: Kaia - Midnight Drive"."""
    phrase = rf"\**{context.words.promo}\**"
    regex = pattern(rf"^\s*(?P<promo>{phrase}(?:\s+{phrase})*)\s*{_SEPARATOR}")
    match = regex.search(draft.masked())
    if match:
        draft.claim("remove_promo", *match.span("promo"))


def claim_pipe_segment(draft: Draft, context: Context) -> None:
    """Step 2: everything from the first pipe on, as in "Song | Monstercat Release"."""
    masked = draft.masked()
    for match in PIPES.finditer(masked):
        if has_words(masked[: match.start()]):
            draft.claim("remove_promo", match.start(), draft.next_claim_start(match.start()))
            return


def claim_trailing_promo(draft: Draft, context: Context) -> None:
    """Step 2 at the end: promo phrases and hashtags, peeled off one at a time."""
    words = context.words
    regex = pattern(
        rf"(?P<tags>(?<!\S){_HASHTAG}(?:\s+{_HASHTAG})*)\s*$"
        rf"|(?P<tail>\**{words.promo_tail}[^{_ANY_BRACKET}|]*?)\s*$"
        rf"|(?P<phrase>\**{words.promo}\**!*)\s*$"
    )
    while True:
        masked = draft.masked()
        match = regex.search(masked)
        # A title that is nothing but promo stays as it is.
        if match is None or not has_words(masked[: match.start()]):
            return
        group = match.lastgroup or "phrase"
        start, end = match.span(group)
        enabled = group != "phrase" or _is_clear_promo(masked[start:end], masked[:start])
        if not draft.claim("remove_promo", start, end, enabled=enabled):
            return


def _is_clear_promo(promo: str, before: str) -> bool:
    """Whether a trailing phrase is surely promo. "Mutually Exclusive" isn't, so it starts off."""
    if len(promo.strip("*!").split()) > 1 or promo.startswith("*"):
        return True
    # A lone word counts when it's in capitals and the title before it isn't.
    return promo.isupper() and not before.isupper()


def claim_custom_words(draft: Draft, context: Context) -> None:
    """Step 2: the user's own words, wherever they appear."""
    for word in context.settings.custom_words:
        phrase = normalize(word)
        if not phrase:
            continue
        for match in pattern(phrase_pattern(phrase)).finditer(draft.masked()):
            draft.claim("custom_word", *match.span())


def claim_emoji(draft: Draft, context: Context) -> None:
    """Step 7: each run of emoji at an edge, so emoji on both sides of a title make two changes."""
    masked = draft.masked()
    for match in _EMOJI_RUN.finditer(masked):
        # A run between two words waits for `claim_remaining_emoji`, after the other rules.
        if not _between_words(masked, *match.span()):
            draft.claim("remove_emoji", *match.span())


def claim_remaining_emoji(draft: Draft, context: Context) -> None:
    """Step 7, last: the other emoji. One still between two words is only offered.

    "Paper Moon (fire) prod. Sable" loses its emoji once the credit is gone, but the star in
    "Midnight Drive (star) Kaia Remix" may be part of the title, so it starts off.
    """
    masked = draft.masked()
    for match in _EMOJI_RUN.finditer(masked):
        enabled = not _between_words(masked, *match.span())
        draft.claim("remove_emoji", *match.span(), enabled=enabled)


def _between_words(text: str, start: int, end: int) -> bool:
    """Whether text[start:end] has a letter or digit on each side, spaces aside."""
    before, after = text[:start].rstrip(), text[end:].lstrip()
    return bool(before and after) and before[-1].isalnum() and after[0].isalnum()


def is_all_caps(text: str) -> bool:
    """Whether the letters are capitals, at least two, and most of them have a case.

    A Japanese title ending in "[MV]" has two capitals among many letters, so it doesn't count.
    """
    letters = [char for char in text if char.isalpha()]
    cased = [char for char in letters if char.isupper() or char.islower()]
    return len(cased) >= 2 and 2 * len(cased) > len(letters) and all(c.isupper() for c in cased)


def title_case(text: str, keep: frozenset[str]) -> str:
    """Step 7: capitalize each word, leaving words like "VIP" and anything with a digit alone."""
    return re.sub(r"\S+", lambda match: _title_word(match.group(), keep), text)


def _title_word(word: str, keep: frozenset[str]) -> str:
    """One word in title case. Only its first cased letter is a capital, so "DON'T" is "Don't"."""
    core = word.strip("()[]{}.,!?;:'\"-")
    if core.upper() in keep or any(char.isdigit() for char in word):
        return word
    lowered = word.lower()
    for index, char in enumerate(lowered):
        if char.isupper() or char.islower():
            return lowered[:index] + char.upper() + lowered[index + 1 :]
    return word


def tidy(text: str) -> str:
    """Step 8 for the title: drop empty brackets, balance the rest, trim dangling separators."""
    text = _drop_empty_groups(_squash(text))
    text = _REPEATED_SEPARATORS.sub(r"\1", balance_brackets(text))
    text = _TRAILING_SEPARATORS.sub("", _LEADING_SEPARATORS.sub("", text))
    return _squash(text)


def tidy_version(text: str, *, spaced: bool = True) -> str | None:
    """Step 8 for the version. It keeps its leading separator, as in " - VIP Mix".

    `spaced` says whether a space comes before it. A full-width bracket often has none.
    """
    text = _drop_empty_groups(_squash(text))
    text = _REPEATED_SEPARATORS.sub(r"\1", " " + balance_brackets(text))
    text = _squash(_TRAILING_SEPARATORS.sub("", text))
    if not has_words(text):
        return None
    return " " + text if spaced else text


def balance_brackets(text: str) -> str:
    """Drop unmatched closers and close open brackets, dropping any with nothing after them."""
    open_at, unmatched = _scan_brackets(text)
    dropped = set(unmatched)
    closing = ""
    for index in open_at:
        if has_words(text[index + 1 :]):
            closing = BRACKET_PAIRS[text[index]] + closing
        else:
            dropped.add(index)
    kept = "".join(char for index, char in enumerate(text) if index not in dropped)
    return kept + closing


def carry_open_bracket(title: str, version: str) -> tuple[str, str]:
    """Move a bracket the title leaves open into the version, when the version closes it.

    With its feature kept, "Song (feat. Lou V (VIP Mix))" keeps the whole bracket as the version.
    """
    open_at, _unmatched = _scan_brackets(title)
    if not open_at:
        return title, version
    start = open_at[-1]
    _still_open, closers = _scan_brackets(version)
    if not closers or version[closers[0]] != BRACKET_PAIRS[title[start]]:
        return title, version
    return title[:start], title[start:] + version


def ends_with_stray_closer(text: str) -> bool:
    """Whether `text` ends in a closing bracket with no opening one before it."""
    _open_at, unmatched = _scan_brackets(text)
    return bool(unmatched) and unmatched[-1] == len(text.rstrip()) - 1


def _scan_brackets(text: str) -> tuple[list[int], list[int]]:
    """Where the brackets left open are, and where the closers with no opener are."""
    open_at: list[int] = []
    unmatched: list[int] = []
    for index, char in enumerate(text):
        if is_emoticon(text, index):
            continue
        if char in OPENERS:
            open_at.append(index)
        elif char in CLOSERS:
            if open_at and BRACKET_PAIRS[text[open_at[-1]]] == char:
                open_at.pop()
            else:
                unmatched.append(index)
    return open_at, unmatched


def _drop_empty_groups(text: str) -> str:
    """Remove "()" and "[ ]", repeating so "([])" goes too."""
    while True:
        shorter = _EMPTY_GROUP.sub(" ", text)
        if shorter == text:
            return _squash(text)
        text = shorter


def _squash(text: str) -> str:
    """Collapse whitespace, including just inside brackets."""
    text = _SPACE_AFTER_OPENER.sub(r"\1", text)
    text = _SPACE_BEFORE_CLOSER.sub(r"\1", text)
    return " ".join(text.split())
