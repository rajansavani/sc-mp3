"""Live check: save a small public SoundCloud album into a temp library. Run with -m network."""

from pathlib import Path

import pytest
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

from scmp3.core import pipeline
from scmp3.core.history import History
from scmp3.core.inputs import classify
from scmp3.core.models import Settings

pytestmark = pytest.mark.network

# A public album that yt-dlp's own SoundCloud tests rely on, and the recorded album fixture. Its
# streams aren't encrypted. api-v2 describes only its first five tracks in full, so the last three
# resolve from their api links.
ALBUM_URL = "https://soundcloud.com/leviryan/sets/out-of-spite"
ALBUM_TITLE = "out of spite"
ALBUM_OWNER = "Levi Ryan"
ALBUM_TRACKS = 8
ALBUM_YEAR_AND_GENRE = ("2022", "Hip-hop & Rap")


def test_a_public_album_saves_with_shared_album_tags_and_one_cover(
    ffmpeg_exe: Path, tmp_path: Path
) -> None:
    settings = Settings(library_dir=tmp_path / "library", album_subfolders=True)
    tools = pipeline.Tools(history=History(tmp_path / "history.db"))
    prepared_set = pipeline.prepare_set(classify(ALBUM_URL), settings=settings, tools=tools)
    assert (prepared_set.playlist.set_type, prepared_set.default_mode) == ("album", "album")
    tracks = pipeline.prepare_set_tracks(prepared_set)
    assert [track.error for track in tracks] == [None] * ALBUM_TRACKS
    results = pipeline.finalize_set(prepared_set, tracks, settings)
    assert [result.outcome for result in results] == ["saved"] * ALBUM_TRACKS
    folder = settings.library_dir / ALBUM_OWNER / ALBUM_TITLE
    covers = set()
    for number, result in enumerate(results, start=1):
        assert result.saved is not None
        path = result.saved.path
        assert path.parent == folder
        assert path.name.startswith(f"{number:02} ")
        tags = ID3(path)
        assert tags.version == (2, 3, 0)
        assert (str(tags["TALB"]), str(tags["TPE2"])) == (ALBUM_TITLE, ALBUM_OWNER)
        assert str(tags["TRCK"]) == f"{number}/{ALBUM_TRACKS}"
        assert (result.track_no, result.track_total) == (number, ALBUM_TRACKS)
        # The tracks have several genres of their own, and the album shares the set's.
        assert (str(tags["TDRC"]), str(tags["TCON"])) == ALBUM_YEAR_AND_GENRE
        # Even the tracks listed by their api link keep their own page.
        assert str(tags["WOAS"]).startswith("https://soundcloud.com/leviryan/")
        [cover] = tags.getall("APIC")
        covers.add(cover.data)
        assert MP3(path).info.length > 60
    assert len(covers) == 1
    assert sorted(path.name for path in folder.iterdir()) == sorted(
        result.saved.path.name for result in results if result.saved is not None
    )
