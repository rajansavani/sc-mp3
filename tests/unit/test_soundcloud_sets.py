"""Tests for listing SoundCloud sets, replayed from recorded fixtures and changed copies of them."""

import importlib.util
import logging
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import AlbumMode, PlaylistEntry, PlaylistInfo
from scmp3.core.sources import soundcloud_sets


def load_record_module() -> ModuleType:
    """tests/fixtures/record.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_record"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "record.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


record = load_record_module()

ALBUM_URL = "https://soundcloud.com/leviryan/sets/out-of-spite"
ALBUM_IDS = [
    "1378985008",
    "1378983277",
    "1378981744",
    "1378980004",
    "1378978405",
    "1378976746",
    "1378973569",
    "1378971022",
]
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJl"
AVATAR = "https://i1.sndcdn.com/avatars-000031664596-r06vh2-original.jpg"


def load_set(name: str) -> dict[str, Any]:
    return record.load(name, record.SETS_DIR)


def replay(name: str, **changes: Any) -> Any:
    """A ReplayYdl for a set fixture, with top-level fixture keys replaced by `changes`."""
    return record.ReplayYdl({**load_set(name), **changes})


def with_api(name: str, **fields: Any) -> Any:
    """A ReplayYdl for a set fixture whose api-v2 answer has `fields` changed."""
    fixture = load_set(name)
    fixture["api"].update(fields)
    return record.ReplayYdl(fixture)


def with_info(name: str, **fields: Any) -> Any:
    """A ReplayYdl for a set fixture whose yt-dlp listing has `fields` changed."""
    fixture = load_set(name)
    fixture["info"].update(fields)
    return record.ReplayYdl(fixture)


def list_set(ydl: Any, token: str | None = None) -> PlaylistInfo:
    return soundcloud_sets.list_set(ydl.fixture["url"], ydl, token)


def test_an_album_lists_its_details_and_tracks_in_order() -> None:
    playlist = list_set(replay("album"))
    assert playlist.title == "out of spite"
    assert playlist.owner == "Levi Ryan"
    assert (playlist.set_type, playlist.is_album) == ("album", True)
    assert (playlist.release_date, playlist.genre) == (date(2022, 11, 8), "Hip-hop & Rap")
    assert playlist.artwork_url == (
        "https://i1.sndcdn.com/artworks-2hmuDCrcvCzzCaXZ-1rztZA-original.jpg"
    )
    assert [entry.id for entry in playlist.entries] == [f"soundcloud:tracks:{n}" for n in ALBUM_IDS]
    assert playlist.entries[0] == PlaylistEntry(
        url="https://soundcloud.com/leviryan/crackshot-wgl0wrm",
        id="soundcloud:tracks:1378985008",
        title="crackshot w/gl0wrm",
    )


def test_tracks_api_v2_doesnt_describe_are_listed_by_api_link_without_a_title() -> None:
    entries = list_set(replay("album")).entries
    # api-v2 describes the first five tracks in full.
    assert all(entry.title for entry in entries[:5])
    assert entries[5:] == [
        PlaylistEntry(url=f"https://api.soundcloud.com/tracks/{n}", id=f"soundcloud:tracks:{n}")
        for n in ALBUM_IDS[5:]
    ]


def test_a_plain_playlist_has_no_type() -> None:
    playlist = list_set(replay("playlist"))
    assert playlist.title == "TILT Brass - Bowery Poetry Club, August '03 [Non-Site SCR 02]"
    assert playlist.owner == "Non-Site Records"
    assert (playlist.set_type, playlist.is_album) == (None, False)
    assert (playlist.release_date, playlist.genre) == (date(2013, 3, 16), "Downtown")
    assert len(playlist.entries) == 6


@pytest.mark.parametrize(("name", "mode"), [("album", "album"), ("playlist", "singles")])
def test_the_recorded_sets_default_to_their_mode(name: str, mode: AlbumMode) -> None:
    assert soundcloud_sets.default_mode(list_set(replay(name))) == mode


@pytest.mark.parametrize(
    ("set_type", "is_album", "mode"),
    [
        ("album", True, "album"),
        ("ep", True, "album"),
        ("single", True, "album"),
        # PLAN §4.4 names albums, EPs and singles only, so a compilation saves as singles.
        ("compilation", True, "singles"),
        ("", False, "singles"),
        # A set marked as an album without a type still saves as one.
        ("", True, "album"),
    ],
)
def test_the_set_type_decides_the_default_mode(
    set_type: str, is_album: bool, mode: AlbumMode
) -> None:
    ydl = with_api("playlist", set_type=set_type, is_album=is_album)
    assert soundcloud_sets.default_mode(list_set(ydl)) == mode


def test_listing_reads_the_set_once_from_yt_dlp_and_once_from_api_v2() -> None:
    ydl = replay("album")
    list_set(ydl)
    assert ydl.extracted == [ALBUM_URL]
    [request] = ydl.requests
    query = parse_qs(urlsplit(request.url).query)
    assert query == {"url": [ALBUM_URL], "client_id": [record.FAKE_CLIENT_ID]}


def test_the_set_link_is_normalized_before_yt_dlp_sees_it() -> None:
    ydl = replay("album")
    soundcloud_sets.list_set("https://m.soundcloud.com/leviryan/sets/out-of-spite/?si=x", ydl)
    assert ydl.extracted == [ALBUM_URL]


def test_a_token_goes_on_the_api_request() -> None:
    ydl = replay("album")
    list_set(ydl, token=TOKEN)
    [request] = ydl.requests
    assert request.headers["Authorization"] == f"OAuth {TOKEN}"


def test_without_api_v2_the_listing_comes_from_yt_dlp_alone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ydl = replay("album", api=None, api_error={"kind": "offline", "status": None})
    with caplog.at_level(logging.WARNING, logger=soundcloud_sets.__name__):
        playlist = list_set(ydl)
    assert "without api-v2" in caplog.text
    assert (playlist.title, playlist.owner) == ("out of spite", "Levi Ryan")
    # yt-dlp carries the type too, so the default mode stays the same.
    assert (playlist.set_type, playlist.is_album) == ("album", False)
    assert soundcloud_sets.default_mode(playlist) == "album"
    assert (playlist.release_date, playlist.genre) == (date(2022, 11, 8), "Hip-hop & Rap")
    assert playlist.artwork_url is not None
    assert len(playlist.entries) == 8
    assert all(entry.title is None for entry in playlist.entries)


def without_own_cover(first_track_cover: str | None) -> Any:
    """The album, as a set without its own cover whose first track has `first_track_cover`."""
    fixture = load_set("album")
    # yt-dlp falls back to the owner's avatar for a set without artwork.
    fixture["info"]["thumbnails"] = [{"id": "original", "url": AVATAR, "preference": 10}]
    fixture["api"]["artwork_url"] = None
    fixture["api"]["tracks"][0]["artwork_url"] = first_track_cover
    return record.ReplayYdl(fixture)


def test_a_set_without_its_own_cover_shows_its_first_tracks() -> None:
    ydl = without_own_cover("https://i1.sndcdn.com/artworks-firsttrack-c0ver-large.jpg")
    assert list_set(ydl).artwork_url == (
        "https://i1.sndcdn.com/artworks-firsttrack-c0ver-t500x500.jpg"
    )


def test_a_set_with_no_cover_at_all_has_none() -> None:
    assert list_set(without_own_cover(None)).artwork_url is None


def test_an_entry_without_a_track_link_is_left_out_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = load_set("album")
    fixture["info"]["entries"][0]["url"] = "https://soundcloud.com/leviryan"
    with caplog.at_level(logging.WARNING, logger=soundcloud_sets.__name__):
        playlist = list_set(record.ReplayYdl(fixture))
    assert [entry.id for entry in playlist.entries] == [
        f"soundcloud:tracks:{n}" for n in ALBUM_IDS[1:]
    ]
    assert "Leaving out a set entry" in caplog.text


def test_a_set_that_cant_be_read_raises_its_error_kind() -> None:
    missing = record.load("not_found")
    ydl = replay("album", info=None, info_error=missing["info_error"])
    with pytest.raises(ScmpError) as raised:
        list_set(ydl)
    assert raised.value.kind == ErrorKind.unavailable


def test_list_set_takes_only_set_links() -> None:
    with pytest.raises(ValueError, match="track"):
        soundcloud_sets.list_set("https://soundcloud.com/the80m/the-following", replay("album"))


def test_api_set_refuses_an_answer_that_isnt_a_set() -> None:
    track = record.load("aac_typical")
    with pytest.raises(ScmpError) as raised:
        soundcloud_sets.api_set(replay("album", api=track["api"]), ALBUM_URL)
    assert raised.value.kind == ErrorKind.engine_outdated
