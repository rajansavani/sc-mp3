"""The title corpus in tests/data/titles.toml, run through suggest, apply_changes and render."""

import re
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scmp3.core.models import Settings, SourceInfo, TagSuggestion
from scmp3.core.suggest import apply_changes, render, suggest
from scmp3.core.suggest.draft import CUT
from scmp3.core.suggest.rules import balance_brackets, normalize, tidy

CORPUS = Path(__file__).parent.parent / "data" / "titles.toml"
CASES: list[dict[str, Any]] = tomllib.loads(CORPUS.read_text(encoding="utf-8"))["case"]
PRESETS = ("artist_field", "feat_in_title", "all_in_title")
TOGGLES = [(case, toggle) for case in CASES for toggle in case.get("toggle", [])]

# Cases TECH_SPEC §8 says the corpus must have, as (title, uploader).
REQUIRED = [
    ("Left Behind", "Some Artist"),
    ("Defeat Me", "Some Artist"),
    ("Gift Of Gab", "Some Artist"),
    ("Dancing with Myself", "Some Artist"),
    ("Song (feat. Future) [Prod. Metro Boomin]", "Some Artist"),
    ("Song ft. Drake - VIP Mix", "Some Artist"),
    ("Juice WRLD - Lucid Dreams", "Lyrical Lemonade"),
    ("Simon and Garfunkel tribute (feat. A and B)", "Some Artist"),
    ("Midnight Drive (feat. Lou V) [prod. Hazy] FREE DL", "Kaia"),
]


def case_id(case: dict[str, Any]) -> str:
    """Name each test after what its case checks."""
    return case["checks"]


# The id and link each kind of source gets, since only the title and names matter here.
SOURCE_IDS = {
    "soundcloud": ("soundcloud:tracks:1", "https://soundcloud.com/kaia/midnight-drive"),
    "youtube": ("youtube:q3StatBloom", "https://www.youtube.com/watch?v=q3StatBloom"),
}


def source_for(case: dict[str, Any]) -> SourceInfo:
    """The source a case describes: SoundCloud unless its `kind` says otherwise.

    A file is known by its `path`, whose name gives the title when the file has no title tag.
    """
    kind = case.get("kind", "soundcloud")
    source_id, url = (case["path"], case["path"]) if kind == "file" else SOURCE_IDS[kind]
    return SourceInfo(
        kind=kind,
        id=source_id,
        url=url,
        title=case["title"],
        uploader=case["uploader"],
        artists=case.get("artists", []),
        description=case.get("description"),
    )


def settings_for(case: dict[str, Any]) -> Settings:
    """Default settings with the case's own changes."""
    return Settings(**case.get("settings", {}))


def switched(suggestion: TagSuggestion, flips: dict[str, bool]) -> TagSuggestion:
    """The suggestion with some changes switched, as the review screen does."""
    changes = [
        replace(change, enabled=flips.get(change.id, change.enabled))
        for change in suggestion.changes
    ]
    return replace(suggestion, changes=changes)


def _without_one(text: str, span: str) -> set[str]:
    """`text` with one occurrence of `span` cut, for each occurrence, tidied and balanced."""
    starts = [index for index in range(len(text)) if text.startswith(span, index)]
    return {balanced(text[:start] + CUT + text[start + len(span) :]) for start in starts}


def _moved_version(default: TagSuggestion, rebuilt: TagSuggestion) -> str:
    """The start of the default version that a switched-off change took back into the title."""
    version, left = (default.version or "").strip(), (rebuilt.version or "").strip()
    if left and not version.endswith(left):
        return ""
    return version[: len(version) - len(left)].strip()


def balanced(text: str) -> str:
    """`text` tidied, without unmatched closers, since a cut span can leave its closer behind."""
    return balance_brackets(tidy(text))


def test_corpus_has_at_least_60_cases_including_the_required_ones() -> None:
    present = {(case["title"], case["uploader"]) for case in CASES}
    assert len(CASES) >= 60
    assert [required for required in REQUIRED if required not in present] == []


def test_corpus_has_at_least_15_youtube_cases_of_each_kind_t061_asks_for() -> None:
    youtube = [case for case in CASES if case.get("kind") == "youtube"]
    titles = [case["title"] for case in youtube]
    assert len(youtube) >= 15
    assert any("(Official Music Video)" in title for title in titles)
    assert any("Lyric" in title for title in titles)
    assert any(case["uploader"].endswith(" - Topic") for case in youtube)
    assert any(case["uploader"].endswith("VEVO") for case in youtube)
    assert any(" - " in title and title.endswith("(Official Audio)") for title in titles)


def test_every_corpus_case_says_what_it_checks() -> None:
    checks = [case["checks"] for case in CASES]
    assert all(checks)
    assert len(set(checks)) == len(checks)


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_corpus_case_gives_the_expected_suggestion(case: dict[str, Any]) -> None:
    suggestion = suggest(source_for(case), settings_for(case))

    assert suggestion.base_title == case["base_title"]
    assert suggestion.main_artists == case["main"]
    assert suggestion.featured == case["featured"]
    assert (suggestion.version or "") == case["version"]
    assert [change.id for change in suggestion.changes if change.enabled] == case["on"]
    assert [change.id for change in suggestion.changes if not change.enabled] == case.get("off", [])


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_corpus_case_renders_each_preset_as_expected(case: dict[str, Any]) -> None:
    settings = settings_for(case)
    suggestion = suggest(source_for(case), settings)

    for preset in PRESETS:
        tags = render(suggestion, preset, {}, settings)
        assert [tags.title, tags.artist] == case[preset], preset


@pytest.mark.parametrize(("case", "toggle"), TOGGLES, ids=[case_id(case) for case, _ in TOGGLES])
def test_corpus_toggle_renders_as_expected(case: dict[str, Any], toggle: dict[str, Any]) -> None:
    source, settings = source_for(case), settings_for(case)
    suggestion = suggest(source, settings)
    flips = dict.fromkeys(toggle.get("off", []), False) | dict.fromkeys(toggle.get("on", []), True)
    assert set(flips) <= {change.id for change in suggestion.changes}

    rebuilt = apply_changes(switched(suggestion, flips), source, settings)

    for preset in PRESETS:
        tags = render(rebuilt, preset, {}, settings)
        assert [tags.title, tags.artist] == toggle[preset], preset


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_turning_each_change_off_puts_its_exact_text_back(case: dict[str, Any]) -> None:
    source, settings = source_for(case), settings_for(case)
    suggestion = suggest(source, settings)
    default = render(suggestion, "artist_field", {}, settings)

    for change in suggestion.changes:
        if not change.enabled:
            continue
        rebuilt = apply_changes(switched(suggestion, {change.id: False}), source, settings)
        tags = render(rebuilt, "artist_field", {}, settings)
        if change.span in tags.title:
            # Taking the text out again gives the default title, so it went back where it was.
            expected = balanced(default.title)
            restored = _without_one(tags.title, change.span)
            # A version inside the credit or next to the artist, as in "(feat. Lou V (VIP Mix) &
            # Mara)" or "AEREA (live) | Set", goes back with it, so it's compared without.
            moved = _moved_version(suggestion, rebuilt)
            beside = re.search(rf"{re.escape(change.span)}\s*{re.escape(moved)}", source.title)
            if moved and (moved in change.span or beside):
                expected = balanced(default.title.replace(moved, "", 1))
                restored = {balanced(title.replace(moved, "", 1)) for title in restored}
            assert expected in restored, change.id
        else:
            # A feat. part or emoji of an artist name goes back into the name.
            assert change.span in tags.artist, change.id


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_turning_each_change_off_shows_its_text_once_in_every_preset(case: dict[str, Any]) -> None:
    source, settings = source_for(case), settings_for(case)
    suggestion = suggest(source, settings)

    for change in suggestion.changes:
        if not change.enabled:
            continue
        rebuilt = apply_changes(switched(suggestion, {change.id: False}), source, settings)
        for preset in PRESETS:
            default = render(suggestion, preset, {}, settings)
            tags = render(rebuilt, preset, {}, settings)
            shown = tags.title.count(change.span) + tags.artist.count(change.span)
            assert shown >= 1, (change.id, preset)
            # all_in_title repeats the artist in the title, so only the other presets count.
            was_shown = default.title.count(change.span) + default.artist.count(change.span)
            if preset != "all_in_title" and not was_shown:
                assert shown == 1, (change.id, preset)


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_with_every_change_off_the_title_is_the_original(case: dict[str, Any]) -> None:
    source, settings = source_for(case), settings_for(case)
    suggestion = suggest(source, settings)
    all_off = {change.id: False for change in suggestion.changes}

    tags = render(
        apply_changes(switched(suggestion, all_off), source, settings), None, {}, settings
    )

    # A messy title like "Kaia -" is still tidied, so its case says what's left.
    assert tags.title == case.get("all_off", normalize(case["title"]))


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_suggesting_again_from_the_result_records_no_change_that_starts_on(
    case: dict[str, Any],
) -> None:
    source, settings = source_for(case), settings_for(case)
    first = suggest(source, settings)
    tags = render(first, "artist_field", {}, settings)

    # As when a saved file is read back: its title, with its artists as the publisher's.
    again = suggest(replace(source, title=tags.title, artists=first.main_artists), settings)
    again_tags = render(again, "artist_field", {}, settings)

    # Featured names aren't compared: they came from title text that's gone now.
    assert [change.id for change in again.changes if change.enabled] == []
    assert (again_tags.title, again.main_artists) == (tags.title, first.main_artists)
