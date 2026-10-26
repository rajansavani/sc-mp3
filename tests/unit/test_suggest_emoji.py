"""Tests for telling real emoji from other symbols, and for when their removal starts on."""

import pytest

from scmp3.core.models import Settings, SourceInfo
from scmp3.core.suggest.draft import Context, Draft
from scmp3.core.suggest.emoji import claim_emoji, find_emoji, settle_emoji
from scmp3.core.suggest.words import load_wordlists

CONTEXT = Context(
    SourceInfo(
        kind="soundcloud",
        id="soundcloud:tracks:1",
        url="https://soundcloud.com/kaia/midnight-drive",
        title="",
        uploader="Kaia",
    ),
    Settings(),
    load_wordlists(),
)


def found(text: str) -> list[str]:
    """The emoji runs `find_emoji` finds in `text`."""
    return [text[start:end] for start, end in find_emoji(text)]


@pytest.mark.parametrize(
    "emoji",
    [
        "\U0001f525",  # fire, shown as emoji by default
        "\u2764\ufe0f",  # a heart made emoji by U+FE0F
        "\U0001f44d\U0001f3fd",  # a thumbs up with a skin tone
        "\u261d\U0001f3fd",  # a skin tone on a base that's text by default
        "\U0001f469\u200d\u2764\ufe0f\u200d\U0001f468",  # a couple, joined by ZWJs
        "\U0001f3c3\u200d\u2640",  # a joined sign without U+FE0F
        "\U0001f1e7\U0001f1f7",  # the flag of Brazil
        "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",  # England
        "1\ufe0f\u20e3",  # a keycap
        "\u2601\ufe0f",  # a cloud made emoji
    ],
)
def test_real_emoji_are_found_whole(emoji: str) -> None:
    assert found(f"Paper {emoji} Moon") == [emoji]


@pytest.mark.parametrize(
    "symbol",
    [
        "\u2727",  # ✧
        "\u2605",  # ★
        "\u2606",  # ☆
        "\u2661",  # ♡
        "\u266a",  # ♪
        "\u2729",  # ✩
        "\u22c6",  # ⋆
        "\u2122",  # ™, an emoji only with U+FE0F
        "\u2764",  # a heart without U+FE0F
        "\u2601\ufe0e",  # a cloud asked to show as text
        "#",
        "7",
        "\u0d26\u0d4d\u0d26\u0d3f(\u02f5 \u2022\u0300 \u1d17 - \u02f5 )",
    ],
)
def test_symbols_and_faces_arent_emoji(symbol: str) -> None:
    assert found(f"Paper {symbol} Moon") == []


def test_emoji_with_spaces_between_them_are_one_run() -> None:
    assert found("Paper Moon \U0001f525 \U0001f525\U0001f319 x") == [
        "\U0001f525 \U0001f525\U0001f319"
    ]


def settled(text: str, removed: tuple[int, int] | None = None) -> list[tuple[str, bool]]:
    """Each emoji claim in `text` and whether it starts on, with `removed` claimed by a rule."""
    draft = Draft(text)
    if removed:
        draft.claim("remove_promo", *removed)
    claim_emoji(draft, CONTEXT)
    settle_emoji(draft, CONTEXT)
    return [
        (draft.span(claim), claim.enabled) for claim in draft.claims if claim.kind == "remove_emoji"
    ]


def test_emoji_start_off_when_nothing_around_them_is_removed() -> None:
    assert settled("\U0001f525 Paper Moon \U0001f525") == [
        ("\U0001f525", False),
        ("\U0001f525", False),
    ]


def test_emoji_touching_removed_text_start_on() -> None:
    text = "Paper Moon \U0001f525 FREE DL \U0001f525"
    start = text.index("FREE DL")

    assert settled(text, (start, start + len("FREE DL"))) == [
        ("\U0001f525", True),
        ("\U0001f525", True),
    ]


def test_an_emoji_between_kept_words_stays_off_even_next_to_removed_text() -> None:
    text = "Paper \U0001f525 [FREE DL] Moon"
    start = text.index("[")
    draft = Draft(text)
    draft.claim("remove_promo", start, start + len("[FREE DL]"))
    draft.claim("remove_emoji", text.index("\U0001f525"), text.index("\U0001f525") + 1)

    settle_emoji(draft, CONTEXT)

    assert [claim.enabled for claim in draft.claims] == [True, False]


def test_an_emoji_next_to_a_credit_stays_off() -> None:
    text = "Paper Moon \U0001f525 (feat. Lou V)"
    start = text.index("(")
    draft = Draft(text)
    draft.claim("extract_feature", start, len(text))
    claim_emoji(draft, CONTEXT)

    settle_emoji(draft, CONTEXT)

    assert [(claim.kind, claim.enabled) for claim in draft.claims] == [
        ("extract_feature", True),
        ("remove_emoji", False),
    ]
