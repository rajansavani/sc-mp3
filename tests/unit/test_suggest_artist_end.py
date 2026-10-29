"""Tests for "Title - Artist" order, symbols-only artists, a channel's own events after a pipe and
versions next to a title's artist. The title corpus has the whole-title cases."""

from dataclasses import replace

import pytest

from scmp3.app.job_record import rebuilt_suggestion
from scmp3.core.models import Settings, SourceInfo, SourceKind
from scmp3.core.suggest import apply_changes, render, suggest
from scmp3.core.suggest.names import same_artist

# A name in small capitals, as a SoundCloud publisher field gave it, built from code points so
# the letters can't be mistaken for plain ones.
SMALL_CAPS_NAME = " ".join(
    chr(code) for code in (0x1D0A, 0x78, 0xA731, 0x1D1B, 0x1D22, 0x1D07, 0x274)
)
FULL_WIDTH_KOI = "".join(chr(code) for code in (0xFF2B, 0xFF2F, 0xFF29))


def source(
    title: str, uploader: str, artists: tuple[str, ...] = (), kind: SourceKind = "soundcloud"
) -> SourceInfo:
    return SourceInfo(
        kind=kind,
        id=f"{kind}:1",
        url="https://example.com",
        title=title,
        uploader=uploader,
        artists=list(artists),
    )


def test_same_artist_reads_small_capitals_and_full_width_letters_as_plain_ones() -> None:
    assert same_artist(SMALL_CAPS_NAME, "✧ jxstzen ✧")
    assert same_artist(SMALL_CAPS_NAME, "JXSTZEN")
    assert same_artist(FULL_WIDTH_KOI, "Koi")
    assert not same_artist(SMALL_CAPS_NAME, "jxstzan")


@pytest.mark.parametrize("kind", ["soundcloud", "youtube"])
def test_the_uploaders_name_after_the_last_dash_leaves_the_title(kind: SourceKind) -> None:
    suggestion = suggest(source("Midnight Drive - Kaia", "Kaia", kind=kind), Settings())
    assert (suggestion.base_title, suggestion.main_artists) == ("Midnight Drive", ["Kaia"])


def test_a_name_in_letter_styles_after_the_last_dash_is_the_uploaders() -> None:
    title = "CHƠI #2 - JXSTZEN"
    suggestion = suggest(source(title, "✧ jxstzen ✧", (SMALL_CAPS_NAME,)), Settings())
    assert (suggestion.base_title, suggestion.main_artists) == ("CHƠI #2", [SMALL_CAPS_NAME])
    assert [change.id for change in suggestion.changes if change.enabled] == [
        "strip_redundant_artist:JXSTZEN"
    ]


def test_a_listed_artist_after_the_last_dash_is_main_over_the_uploader() -> None:
    suggestion = suggest(source("dư âm - Obito", "Kaia", ("Kaia, Obito",)), Settings())
    assert suggestion.base_title == "dư âm"
    assert (suggestion.main_artists, suggestion.featured) == (["Obito"], ["Kaia"])
    assert "artist_from_title:Obito" in [change.id for change in suggestion.changes]
    labels = [change.label for change in suggestion.changes]
    assert "Took the artist “Obito” from the title" in labels


def test_the_end_claim_takes_the_dash_but_keeps_the_id_it_had() -> None:
    suggestion = suggest(source("Song - Kaia", "Kaia"), Settings())
    [change] = suggestion.changes
    assert (change.id, change.span) == ("strip_redundant_artist:Kaia", "- Kaia")


def test_a_job_saved_with_mains_ids_keeps_its_end_claim_switched_off() -> None:
    # As queue.json kept it on main: the YouTube end claim switched off by hand.
    title = "Song - Kaia (Official Video)"
    youtube = source(title, "Kaia", kind="youtube")
    saved = suggest(youtube, Settings())
    toggles = {"strip_redundant_artist:Kaia": False, "remove_promo:(Official Video)": False}

    rebuilt = rebuilt_suggestion(saved, youtube, toggles, Settings())

    assert rebuilt.base_title == title
    assert not next(c for c in rebuilt.changes if c.id == "strip_redundant_artist:Kaia").enabled


def test_an_end_claim_switched_off_keeps_the_version_before_it_in_place() -> None:
    youtube = source("Numb (Live in Munich) - Linkin Park", "Linkin Park", kind="youtube")
    suggestion = suggest(youtube, Settings())
    off = replace(suggestion, changes=[replace(c, enabled=False) for c in suggestion.changes])

    rebuilt = apply_changes(off, youtube, Settings())

    assert (rebuilt.base_title, rebuilt.version) == ("Numb (Live in Munich) - Linkin Park", None)
    assert render(rebuilt, "all_in_title", {}, Settings()).title == (
        "Numb (Live in Munich) - Linkin Park (Linkin Park)"
    )


@pytest.mark.parametrize(
    ("title", "settings"),
    [
        ("Song - Kaia 🔥", Settings()),
        ("Song - Kaia [FREE DL]", Settings(remove_promo_text=False)),
        ("Song - Kaia (prod. Hazy)", Settings(remove_producer_credits=False)),
    ],
)
def test_text_kept_after_the_end_name_never_follows_a_bare_dash(
    title: str, settings: Settings
) -> None:
    suggestion = suggest(source(title, "Kaia"), settings)
    assert " - " not in suggestion.base_title
    assert suggestion.base_title.startswith("Song ")


def test_a_title_that_starts_with_an_artist_keeps_its_end() -> None:
    suggestion = suggest(source("Kaia - Lou V", "Kaia", ("Kaia, Lou V",)), Settings())
    assert suggestion.base_title == "Lou V"


def test_another_name_after_the_last_dash_stays_on_soundcloud() -> None:
    suggestion = suggest(source("Midnight Drive - Lou V", "Kaia"), Settings())
    assert suggestion.base_title == "Midnight Drive - Lou V"


def test_a_symbols_only_artist_matches_the_uploader() -> None:
    suggestion = suggest(source("✝✝✝ - This Is a Trick", "✝✝✝"), Settings())
    assert (suggestion.base_title, suggestion.main_artists) == ("This Is a Trick", ["✝✝✝"])


def test_a_symbols_only_artist_matches_a_listed_name() -> None:
    title = "✝✝✝ - This Is a Trick"
    suggestion = suggest(source(title, "Some Label Records", ("✝✝✝",)), Settings())
    assert (suggestion.base_title, suggestion.main_artists) == ("This Is a Trick", ["✝✝✝"])


def test_a_symbols_only_part_that_names_nobody_stays_in_the_title() -> None:
    title = "✝✝✝ - This Is a Trick"
    suggestion = suggest(source(title, "Some Label Records"), Settings())
    assert suggestion.base_title == title
    assert suggestion.changes == []


@pytest.mark.parametrize("kind", ["soundcloud", "youtube"])
def test_a_channels_own_event_after_a_pipe_isnt_a_label_tag(kind: SourceKind) -> None:
    title = "Laidlaw | Boiler Room London: Beeyou Records"
    suggestion = suggest(source(title, "Boiler Room", kind=kind), Settings())
    assert suggestion.base_title == "Boiler Room London: Beeyou Records"
    assert suggestion.main_artists == ["Laidlaw"]


def test_a_label_tag_after_a_pipe_is_still_removed() -> None:
    suggestion = suggest(source("Kaia | Lou V Records", "Boiler Room"), Settings())
    assert (suggestion.base_title, suggestion.main_artists) == ("Kaia", ["Boiler Room"])


@pytest.mark.parametrize("kind", ["soundcloud", "youtube"])
def test_a_version_next_to_the_artist_goes_with_the_title(kind: SourceKind) -> None:
    title = "AEREA (live) | Boiler Room: Maastricht"
    suggestion = suggest(source(title, "Boiler Room", kind=kind), Settings())
    assert suggestion.base_title == "Boiler Room: Maastricht"
    assert (suggestion.version, suggestion.main_artists) == (" (live)", ["AEREA"])
