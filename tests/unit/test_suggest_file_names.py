"""Tests for the title a file with no title tag gets from its file name."""

import pytest

from scmp3.core.models import Settings, SourceInfo
from scmp3.core.suggest import suggest
from scmp3.core.suggest.file_names import name_as_title, title_to_read


def file_source(path: str, title: str = "", artist: str = "") -> SourceInfo:
    return SourceInfo(
        kind="file",
        id=path,
        url=path,
        title=title,
        uploader=artist,
        artists=[artist] if artist else [],
    )


@pytest.mark.parametrize(
    ("path", "title"),
    [
        (r"C:\Music\Kaia - Midnight Drive.mp3", "Kaia - Midnight Drive"),
        ("/home/me/Music/Kaia - Midnight Drive.m4a", "Kaia - Midnight Drive"),
        ("Music/03 - Kaia - Midnight Drive.mp3", "Kaia - Midnight Drive"),
        ("Music/03. Midnight Drive.mp3", "Midnight Drive"),
        ("Music/03_Midnight_Drive.mp3", "Midnight Drive"),
        ("Music/Kaia_-_Midnight_Drive.mp3", "Kaia - Midnight Drive"),
        ("Music/7 Rings.mp3", "7 Rings"),
        ("Music/2024.mp3", "2024"),
        ("Music/2.0.mp3", "2.0"),
        ("Music/1.5 Degrees.mp3", "1.5 Degrees"),
        ("Music/01_-_Kaia_-_Song.mp3", "Kaia - Song"),
        ("Music/01 Kaia - Song.mp3", "Kaia - Song"),
        ("Music/50 Cent - In Da Club.mp3", "50 Cent - In Da Club"),
        ("Music/3. 50 Cent - In Da Club.mp3", "50 Cent - In Da Club"),
        ("Music/Mr. Brightside.mp3", "Mr. Brightside"),
        ("Music/01.mp3", "01"),
    ],
)
def test_a_file_name_becomes_a_title_without_its_folder_extension_and_track_number(
    path: str, title: str
) -> None:
    assert name_as_title(path) == title


def test_only_a_file_without_a_title_tag_reads_its_name() -> None:
    assert title_to_read(file_source("Music/Kaia - Song.mp3")) == "Kaia - Song"
    assert title_to_read(file_source("Music/Kaia - Song.mp3", title="Other")) == "Other"


def test_a_tagless_files_name_gives_the_title_and_the_artist() -> None:
    suggestion = suggest(file_source("Music/Kaia - Midnight Drive.mp3"), Settings())
    assert (suggestion.base_title, suggestion.main_artists) == ("Midnight Drive", ["Kaia"])


def test_a_tagless_files_artist_tag_still_names_the_artist() -> None:
    source = file_source("Music/Lou V - Midnight Drive.mp3", artist="Kaia")
    suggestion = suggest(source, Settings())
    assert suggestion.base_title == "Lou V - Midnight Drive"
    assert suggestion.main_artists == ["Kaia"]
