"""Tests for the YouTube additions to the suggestion engine: channel names as artist names, the
channel's own name at the end of a title, and video labels crediting another artist's song. The
title corpus has the whole-title cases."""

import pytest

from scmp3.core.models import Settings, SourceInfo, SourceKind
from scmp3.core.suggest import suggest
from scmp3.core.suggest.youtube import uploader_name


def source(title: str, uploader: str, kind: SourceKind = "youtube") -> SourceInfo:
    return SourceInfo(
        kind=kind, id=f"{kind}:1", url="https://example.com", title=title, uploader=uploader
    )


@pytest.mark.parametrize(
    ("uploader", "title", "name"),
    [
        ("Future - Topic", "WAIT FOR U", "Future"),
        ("AdeleVEVO", "Adele - Hello", "Adele"),
        ("ArianaGrandeVevo", "7 rings", "ArianaGrande"),
        ("TaylorSwiftVEVO", "Taylor Swift - Shake It Off", "Taylor Swift"),
        ("TaylorSwiftVEVO", "Taylor Swift ft. Kendrick Lamar - Bad Blood", "TaylorSwift"),
        ("shakiraVEVO", "Shakira - Waka Waka", "Shakira"),
        ("beyonceVEVO", "Beyoncé - Halo", "Beyoncé"),
        ("VEVO", "Hello", "VEVO"),
        ("Kaia", "Kaia - Midnight Drive", "Kaia"),
        # Only a squashed name takes the title's spelling, so a channel's own casing stays.
        ("Kaia", "KAIA - Midnight Drive", "Kaia"),
        ("LouV", "Lou V - Midnight Drive", "Lou V"),
    ],
)
def test_youtube_channel_names_become_the_artists_name(
    uploader: str, title: str, name: str
) -> None:
    assert uploader_name(source(title, uploader)) == name


def test_soundcloud_uploaders_are_left_as_they_are() -> None:
    assert uploader_name(source("Hello", "AdeleVEVO", kind="soundcloud")) == "AdeleVEVO"


@pytest.mark.parametrize(
    "title",
    [
        "Kaia - Midnight Drive (Lyrics)",
        "Kaia - Midnight Drive (Official Video)",
        "Kaia - Midnight Drive (Letra/Lyrics)",
        "Kaia - Midnight Drive | Official Audio",
    ],
)
def test_a_video_label_from_a_channel_credits_the_artist_in_the_title(title: str) -> None:
    suggestion = suggest(source(title, "Late Night Lyrics"), Settings())
    assert suggestion.main_artists == ["Kaia"]
    assert suggestion.base_title == "Midnight Drive"


@pytest.mark.parametrize(
    ("title", "uploader"),
    [
        ("Kaia - Midnight Drive", "Late Night Uploads"),
        ("Interstellar - Main Theme [HD]", "Hans Zimmer"),
        ("Clair de Lune - Debussy", "Rousseau"),
        ("Midnight Drive - Lou V", "Kaia"),
    ],
)
def test_without_a_video_label_the_split_is_only_offered_as_on_soundcloud(
    title: str, uploader: str
) -> None:
    suggestion = suggest(source(title, uploader), Settings())
    assert suggestion.main_artists == [uploader]
    assert [change.kind for change in suggestion.changes if not change.enabled] == [
        "artist_from_title"
    ]


def test_known_channels_count_as_channels_on_youtube_as_on_soundcloud() -> None:
    for kind in ("youtube", "soundcloud"):
        sets = suggest(source("Okoye | Boiler Room London", "Boiler Room", kind), Settings())
        assert (sets.base_title, sets.main_artists) == ("Boiler Room London", ["Okoye"]), kind
        show = suggest(source("Ryan Beatty - Delancey | A COLORS SHOW", "COLORS", kind), Settings())
        assert (show.base_title, show.main_artists) == ("Delancey", ["Ryan Beatty"]), kind


def test_the_channels_name_after_the_last_dash_leaves_the_title() -> None:
    suggestion = suggest(source("Numb (Live in Munich) - Linkin Park", "Linkin Park"), Settings())
    assert suggestion.main_artists == ["Linkin Park"]
    assert suggestion.base_title == "Numb (Live in Munich)"


def test_on_soundcloud_the_name_after_the_last_dash_stays() -> None:
    suggestion = suggest(source("Midnight Drive - Kaia", "Kaia", kind="soundcloud"), Settings())
    assert suggestion.base_title == "Midnight Drive - Kaia"


def test_a_name_inside_a_bracket_isnt_the_end_of_the_title() -> None:
    suggestion = suggest(source("Numeron - My Eternity (Live on KEXP)", "KEXP"), Settings())
    assert suggestion.main_artists == ["Numeron"]


def test_youtube_music_artists_decide_as_a_publishers_do() -> None:
    topic = SourceInfo(
        kind="youtube",
        id="youtube:1",
        url="https://example.com",
        title="Song - From the Film (Official Audio)",
        uploader="Kaia - Topic",
        artists=["Kaia"],
    )
    suggestion = suggest(topic, Settings())
    # "Song" isn't one of the Music artists, so it stays in the title, even with a video label.
    assert suggestion.main_artists == ["Kaia"]
    assert suggestion.base_title == "Song - From the Film"
