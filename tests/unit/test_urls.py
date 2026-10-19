"""Tests for recognizing SoundCloud and YouTube links and normalizing them."""

import re
import socket
from collections.abc import Callable

import pytest
from yt_dlp.extractor.soundcloud import SoundcloudIE, SoundcloudPlaylistIE, SoundcloudSetIE
from yt_dlp.extractor.youtube import YoutubeIE

from scmp3.core.inputs import ParsedInput, classify
from scmp3.core.urls import (
    NOT_SUPPORTED,
    SHORT_LINK_DEAD_END,
    SOUNDCLOUD_NOT_TRACK,
    YOUTUBE_NOT_VIDEO,
    YOUTUBE_PLAYLIST,
    UnsupportedInput,
    normalize_soundcloud,
    normalize_youtube,
    resolve_short_link,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test here that tries to open a connection."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("this test tried to use the network")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


# Each row: what was pasted, the kind it is, and its normalized link.
SOUNDCLOUD_LINKS = [
    pytest.param(
        "https://soundcloud.com/artist/track",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="plain track",
    ),
    pytest.param(
        "http://soundcloud.com/artist/track",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="http becomes https",
    ),
    pytest.param(
        "HTTPS://SoundCloud.COM/Artist/Track",
        "soundcloud_track",
        "https://soundcloud.com/Artist/Track",
        id="uppercase host is lowered, path case is kept",
    ),
    pytest.param(
        "https://www.soundcloud.com/artist/track",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="www host",
    ),
    pytest.param(
        "https://m.soundcloud.com/artist/track",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="m host",
    ),
    pytest.param(
        "https://soundcloud.com/artist/track/",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="trailing slash",
    ),
    pytest.param(
        "soundcloud.com/artist/track",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="no scheme",
    ),
    pytest.param(
        "https://soundcloud.com/artist/track?si=0f1e2d3c4b5a69788796a5b4c3d2e1f0",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="si dropped",
    ),
    pytest.param(
        "https://soundcloud.com/artist/track"
        "?utm_source=clipboard&utm_medium=text&utm_campaign=social_sharing",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="utm parameters dropped",
    ),
    pytest.param(
        "https://soundcloud.com/artist/track?in=artist/sets/album",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="in dropped",
    ),
    pytest.param(
        "https://soundcloud.com/artist/track?ref=clipboard",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="ref dropped",
    ),
    pytest.param(
        "https://m.soundcloud.com/artist/track"
        "?in=artist/sets/album&utm_source=mobi&utm_campaign=social_sharing&si=abc123",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="mobile share link",
    ),
    pytest.param(
        "https://soundcloud.com/artist/track?ref=clipboard&p=i&c=1&si=abc",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="app share parameters dropped",
    ),
    pytest.param(
        "https://soundcloud.com/artist/track?foo=1&bar=2#t=0:42",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="any other parameters and fragment dropped",
    ),
    pytest.param(
        "https://m.soundcloud.com/artist/sets/album/s-XyZ789aBc?foo=1&si=abc",
        "soundcloud_set",
        "https://soundcloud.com/artist/sets/album/s-XyZ789aBc",
        id="secret set keeps its path, loses its query",
    ),
    pytest.param(
        "https://soundcloud.com/artist/track#t=1:30",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="fragment dropped",
    ),
    pytest.param(
        "https://soundcloud.com/artist/track/s-AbCdE123fGh",
        "soundcloud_track",
        "https://soundcloud.com/artist/track/s-AbCdE123fGh",
        id="secret track",
    ),
    pytest.param(
        "https://soundcloud.com/artist/track/s-AbCdE123fGh/?si=abc&utm_source=clipboard",
        "soundcloud_track",
        "https://soundcloud.com/artist/track/s-AbCdE123fGh",
        id="secret track with tracking and slash",
    ),
    pytest.param(
        "https://soundcloud.com/artist/sets/album",
        "soundcloud_set",
        "https://soundcloud.com/artist/sets/album",
        id="set",
    ),
    pytest.param(
        "https://M.SoundCloud.com/artist/sets/album/?si=abc&utm_medium=text",
        "soundcloud_set",
        "https://soundcloud.com/artist/sets/album",
        id="set with tracking, m host and slash",
    ),
    pytest.param(
        "https://soundcloud.com/artist/sets/album/s-XyZ789aBc",
        "soundcloud_set",
        "https://soundcloud.com/artist/sets/album/s-XyZ789aBc",
        id="secret set",
    ),
    pytest.param(
        "https://w.soundcloud.com/player/?url=https%3A//api.soundcloud.com/tracks/293"
        "&color=%23ff5500&auto_play=false&hide_related=false&show_comments=true",
        "soundcloud_track",
        "https://api.soundcloud.com/tracks/293",
        id="embed of an api track",
    ),
    pytest.param(
        "https://w.soundcloud.com/player/"
        "?url=https%3A//api.soundcloud.com/tracks/soundcloud%253Atracks%253A293&visual=true",
        "soundcloud_track",
        "https://api.soundcloud.com/tracks/293",
        id="embed of a track URN",
    ),
    pytest.param(
        "https://w.soundcloud.com/player/"
        "?url=https%3A//api.soundcloud.com/tracks/123%3Fsecret_token%3Ds-AbC12",
        "soundcloud_track",
        "https://api.soundcloud.com/tracks/123?secret_token=s-AbC12",
        id="embed of a secret track",
    ),
    pytest.param(
        "https://w.soundcloud.com/player/?visual=true"
        "&url=https%3A%2F%2Fapi.soundcloud.com%2Fplaylists%2F922213810&show_artwork=true"
        "&maxwidth=640&maxheight=960&dnt=1&secret_token=s-ziYey",
        "soundcloud_set",
        "https://api.soundcloud.com/playlists/922213810?secret_token=s-ziYey",
        id="embed of a secret playlist, token beside the link",
    ),
    pytest.param(
        "https://w.soundcloud.com/player/?url=https%3A%2F%2Fsoundcloud.com%2Fartist%2Ftrack"
        "%3Fsi%3Dabc",
        "soundcloud_track",
        "https://soundcloud.com/artist/track",
        id="embed of a permalink",
    ),
    pytest.param(
        "w.soundcloud.com/player/?url=https://api.soundcloud.com/tracks/293&visual=true",
        "soundcloud_track",
        "https://api.soundcloud.com/tracks/293",
        id="embed without scheme, wrapped link not encoded",
    ),
    pytest.param(
        "https://api.soundcloud.com/tracks/123998367",
        "soundcloud_track",
        "https://api.soundcloud.com/tracks/123998367",
        id="api track",
    ),
    pytest.param(
        "https://api-v2.soundcloud.com/tracks/123998367/",
        "soundcloud_track",
        "https://api.soundcloud.com/tracks/123998367",
        id="api-v2 track",
    ),
    pytest.param(
        "https://api.soundcloud.com/tracks/soundcloud:tracks:123998367",
        "soundcloud_track",
        "https://api.soundcloud.com/tracks/123998367",
        id="api track URN",
    ),
    pytest.param(
        "https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A123998367",
        "soundcloud_track",
        "https://api.soundcloud.com/tracks/123998367",
        id="api track URN, encoded",
    ),
    pytest.param(
        "https://api.soundcloud.com/tracks/123998367?client_id=abc&secret_token=s-8Pjrp",
        "soundcloud_track",
        "https://api.soundcloud.com/tracks/123998367?secret_token=s-8Pjrp",
        id="api secret track keeps only the token",
    ),
    pytest.param(
        "https://api.soundcloud.com/playlists/soundcloud:playlists:2104769627"
        "?secret_token=s-wmpCLuExeYX",
        "soundcloud_set",
        "https://api.soundcloud.com/playlists/2104769627?secret_token=s-wmpCLuExeYX",
        id="api secret playlist URN",
    ),
    pytest.param(
        "https://on.soundcloud.com/AbC123xyZ",
        "soundcloud_short",
        "https://on.soundcloud.com/AbC123xyZ",
        id="short link",
    ),
    pytest.param(
        "HTTPS://ON.SOUNDCLOUD.COM/AbC123xyZ/?si=abc",
        "soundcloud_short",
        "https://on.soundcloud.com/AbC123xyZ",
        id="short link, uppercase host, slash and si",
    ),
    pytest.param(
        "https://on.soundcloud.com/AbC123xyZ?utm_source=clipboard&foo=1#top",
        "soundcloud_short",
        "https://on.soundcloud.com/AbC123xyZ",
        id="short link drops its whole query and fragment",
    ),
    pytest.param(
        "https://soundcloud.com/discover/sets/charts-top:all-music",
        "soundcloud_set",
        "https://soundcloud.com/discover/sets/charts-top:all-music",
        id="system set with a colon",
    ),
    # What chat text and web pages leave around a link.
    *(
        pytest.param(
            pasted, "soundcloud_track", "https://soundcloud.com/artist/track", id=f"link {name}"
        )
        for name, pasted in [
            ("then a full stop", "https://soundcloud.com/artist/track."),
            ("then a comma", "https://soundcloud.com/artist/track,"),
            ("then a closing bracket", "https://soundcloud.com/artist/track)"),
            ("then a bracket and full stop", "https://soundcloud.com/artist/track)."),
            ("in brackets", "(https://soundcloud.com/artist/track)"),
            ("in angle brackets", "<https://soundcloud.com/artist/track>"),
            ("then a question mark", "https://soundcloud.com/artist/track?"),
            ("after a byte order mark", "\ufeffhttps://soundcloud.com/artist/track"),
            ("after a zero-width space", "\u200bhttps://soundcloud.com/artist/track"),
            ("then a zero-width space", "https://soundcloud.com/artist/track\u200b"),
            ("then a soft hyphen", "https://soundcloud.com/artist/track\u00ad"),
            ("then NUL", "https://soundcloud.com/artist/track\x00"),
            ("then DEL", "https://soundcloud.com/artist/track\x7f"),
            ("in curly double quotes", "\u201chttps://soundcloud.com/artist/track\u201d"),
            ("in curly single quotes", "\u2018https://soundcloud.com/artist/track\u2019"),
            ("in guillemets", "\u00abhttps://soundcloud.com/artist/track\u00bb"),
            ("in fullwidth brackets", "\uff08https://soundcloud.com/artist/track\uff09"),
            ("in corner brackets", "\u300chttps://soundcloud.com/artist/track\u300d"),
            ("then an ideographic full stop", "https://soundcloud.com/artist/track\u3002"),
            ("then a fullwidth comma", "https://soundcloud.com/artist/track\uff0c"),
        ]
    ),
    pytest.param(
        "<https://on.soundcloud.com/AbC123xyZ>.",
        "soundcloud_short",
        "https://on.soundcloud.com/AbC123xyZ",
        id="short link in angle brackets and a full stop",
    ),
]

SOUNDCLOUD_NON_TRACKS = [
    pytest.param("https://soundcloud.com/", id="home page"),
    pytest.param("https://soundcloud.com/artist", id="profile"),
    pytest.param("https://soundcloud.com/artist/likes", id="likes tab"),
    pytest.param("https://soundcloud.com/artist/sets/", id="sets tab"),
    pytest.param("https://soundcloud.com/artist/tracks", id="tracks tab"),
    pytest.param("https://soundcloud.com/you/library", id="own library"),
    pytest.param("https://soundcloud.com/search/sounds?q=house", id="search"),
    pytest.param("https://soundcloud.com/stations/track/artist/track", id="station"),
    pytest.param("https://soundcloud.com/artist/track/recommended", id="related tracks"),
    pytest.param("https://soundcloud.com/artist/sets/album/extra", id="set with extra path"),
    pytest.param("https://w.soundcloud.com/player/?visual=true", id="embed without a link"),
    pytest.param("https://api.soundcloud.com/users/123", id="api user"),
    pytest.param("https://api.soundcloud.com/tracks/abc", id="api id not a number"),
    pytest.param("https://on.soundcloud.com/", id="short link without a code"),
    pytest.param("https://on.soundcloud.com/AbC!123", id="short code with a stray character"),
    pytest.param('"https://soundcloud.com/artist name/track"', id="space in a name"),
    pytest.param("https://soundcloud.com/artist/tr%C3%A4ck", id="percent-encoded name"),
    pytest.param("https://soundcloud.com/artist/track*", id="stray character in a name"),
    pytest.param("https://soundcloud.com/artist/track:remix", id="colon in a track name"),
]

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SHORT = "https://www.youtube.com/watch?v=aB3_dE-fGhI"

YOUTUBE_LINKS = [
    pytest.param(VIDEO, VIDEO, id="watch"),
    pytest.param("https://youtube.com/watch?v=dQw4w9WgXcQ", VIDEO, id="no www"),
    pytest.param("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", VIDEO, id="m host"),
    pytest.param("http://WWW.YouTube.com/watch?v=dQw4w9WgXcQ", VIDEO, id="http, uppercase host"),
    pytest.param(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG&index=2",
        VIDEO,
        id="list ignored",
    ),
    pytest.param("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", VIDEO, id="t dropped"),
    pytest.param(
        "https://www.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ", VIDEO, id="v not first"
    ),
    pytest.param("https://youtu.be/dQw4w9WgXcQ", VIDEO, id="youtu.be"),
    pytest.param("https://youtu.be/dQw4w9WgXcQ?t=42", VIDEO, id="youtu.be with t"),
    pytest.param("https://youtu.be/dQw4w9WgXcQ?si=AbCdEfGh1234", VIDEO, id="youtu.be with si"),
    pytest.param(
        "https://youtu.be/dQw4w9WgXcQ?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG",
        VIDEO,
        id="youtu.be with list",
    ),
    pytest.param("youtu.be/dQw4w9WgXcQ", VIDEO, id="youtu.be without scheme"),
    pytest.param(
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVMdQw4w9WgXcQ",
        VIDEO,
        id="music.youtube.com",
    ),
    pytest.param("https://www.youtube.com/shorts/aB3_dE-fGhI", SHORT, id="shorts"),
    pytest.param("https://youtube.com/shorts/aB3_dE-fGhI?feature=share", SHORT, id="shorts shared"),
    pytest.param("https://www.youtube.com/live/aB3_dE-fGhI?si=abc", SHORT, id="live"),
    pytest.param("https://youtu.be/dQw4w9WgXcQ.", VIDEO, id="youtu.be and a full stop"),
    pytest.param("https://www.youtube.com/watch?v=dQw4w9WgXcQ,", VIDEO, id="watch and a comma"),
    pytest.param("(https://youtu.be/dQw4w9WgXcQ)", VIDEO, id="youtu.be in brackets"),
    pytest.param("https://youtu.be/dQw4w9WgXcQ\u201d", VIDEO, id="youtu.be and a curly quote"),
]

YOUTUBE_PLAYLISTS = [
    pytest.param(
        "https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG", id="playlist"
    ),
    pytest.param(
        "https://www.youtube.com/watch?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG", id="watch, no v"
    ),
    pytest.param(
        "https://music.youtube.com/playlist?list=OLAK5uy_abcdefghijklmnopqrstuvwxyz012345",
        id="music album",
    ),
]

YOUTUBE_NON_VIDEOS = [
    pytest.param("https://www.youtube.com/", id="home page"),
    pytest.param("https://www.youtube.com/@artist", id="channel"),
    pytest.param("https://www.youtube.com/watch?v=tooshort", id="id too short"),
    pytest.param("https://www.youtube.com/shorts/", id="shorts without an id"),
    pytest.param("https://youtu.be/", id="youtu.be without an id"),
]


@pytest.mark.parametrize(("pasted", "kind", "expected"), SOUNDCLOUD_LINKS)
def test_soundcloud_links_normalize(pasted: str, kind: str, expected: str) -> None:
    assert normalize_soundcloud(pasted) == expected
    assert classify(pasted) == ParsedInput(kind, pasted, url=expected)


@pytest.mark.parametrize("pasted", SOUNDCLOUD_NON_TRACKS)
def test_soundcloud_links_that_arent_tracks_or_sets_are_unsupported(pasted: str) -> None:
    with pytest.raises(UnsupportedInput, match=re.escape(SOUNDCLOUD_NOT_TRACK)):
        normalize_soundcloud(pasted)
    assert classify(pasted) == ParsedInput("unsupported", pasted, reason=SOUNDCLOUD_NOT_TRACK)


@pytest.mark.parametrize("pasted", ["https://youtu.be/dQw4w9WgXcQ", "https://example.com/a/b"])
def test_normalize_soundcloud_refuses_other_sites(pasted: str) -> None:
    with pytest.raises(UnsupportedInput, match=re.escape(NOT_SUPPORTED)):
        normalize_soundcloud(pasted)


@pytest.mark.parametrize(("pasted", "expected"), YOUTUBE_LINKS)
def test_youtube_links_normalize_to_a_watch_link(pasted: str, expected: str) -> None:
    assert normalize_youtube(pasted) == expected
    assert classify(pasted) == ParsedInput("youtube_video", pasted, url=expected)


@pytest.mark.parametrize("pasted", YOUTUBE_PLAYLISTS)
def test_youtube_playlist_links_are_unsupported(pasted: str) -> None:
    with pytest.raises(UnsupportedInput, match=re.escape(YOUTUBE_PLAYLIST)):
        normalize_youtube(pasted)
    parsed = classify(pasted)
    assert parsed.kind == "unsupported"
    assert parsed.reason == "YouTube playlists aren't supported yet."


@pytest.mark.parametrize("pasted", YOUTUBE_NON_VIDEOS)
def test_youtube_links_that_arent_videos_are_unsupported(pasted: str) -> None:
    with pytest.raises(UnsupportedInput, match=re.escape(YOUTUBE_NOT_VIDEO)):
        normalize_youtube(pasted)
    assert classify(pasted) == ParsedInput("unsupported", pasted, reason=YOUTUBE_NOT_VIDEO)


def test_normalize_youtube_refuses_other_sites() -> None:
    with pytest.raises(UnsupportedInput, match=re.escape(NOT_SUPPORTED)):
        normalize_youtube("https://soundcloud.com/artist/track")


# yt-dlp picks the extractor from the URL, so every normalized link must still match one.
EXTRACTORS = {
    "soundcloud_track": [SoundcloudIE],
    "soundcloud_set": [SoundcloudSetIE, SoundcloudPlaylistIE],
}


@pytest.mark.parametrize(("pasted", "kind", "expected"), SOUNDCLOUD_LINKS)
def test_yt_dlp_accepts_each_normalized_soundcloud_link(
    pasted: str, kind: str, expected: str
) -> None:
    if kind == "soundcloud_short":
        # yt-dlp's generic extractor follows these, as the spec expects.
        assert not SoundcloudIE.suitable(expected)
        return
    assert any(extractor.suitable(expected) for extractor in EXTRACTORS[kind])


@pytest.mark.parametrize(("pasted", "expected"), YOUTUBE_LINKS)
def test_yt_dlp_accepts_each_normalized_youtube_link(pasted: str, expected: str) -> None:
    assert YoutubeIE.suitable(expected)


def test_an_embed_can_wrap_a_short_link_but_not_another_embed() -> None:
    short = "https://w.soundcloud.com/player/?url=https%3A//on.soundcloud.com/AbC123"
    assert classify(short).url == "https://on.soundcloud.com/AbC123"
    nested = (
        "https://w.soundcloud.com/player/"
        "?url=https://w.soundcloud.com/player/?url=https://soundcloud.com/artist/track"
    )
    assert classify(nested) == ParsedInput("unsupported", nested, reason=NOT_SUPPORTED)


def test_a_protocol_relative_link_to_a_known_host_is_a_link() -> None:
    assert classify("//soundcloud.com/artist/track").url == "https://soundcloud.com/artist/track"
    assert classify("//youtu.be/dQw4w9WgXcQ").url == VIDEO


class FakeResponse:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    """Stands in for yt-dlp's urlopen: records each request and answers with a fixed final URL."""

    def __init__(self, final_url: str) -> None:
        self.final_url = final_url
        self.requests: list[str] = []
        self.responses: list[FakeResponse] = []

    def __call__(self, url: str) -> FakeResponse:
        self.requests.append(url)
        response = FakeResponse(self.final_url)
        self.responses.append(response)
        return response


@pytest.mark.parametrize(
    ("final_url", "expected"),
    [
        pytest.param(
            "https://soundcloud.com/artist/track"
            "?in=artist/sets/album&utm_source=mobi&utm_campaign=social_sharing&si=abc123",
            "https://soundcloud.com/artist/track",
            id="track",
        ),
        pytest.param(
            "https://m.soundcloud.com/artist/sets/album?si=abc123",
            "https://soundcloud.com/artist/sets/album",
            id="set on the m host",
        ),
        pytest.param(
            "https://soundcloud.com/artist/track/s-AbCdE123fGh?si=abc123",
            "https://soundcloud.com/artist/track/s-AbCdE123fGh",
            id="secret track",
        ),
    ],
)
def test_resolve_short_link_makes_one_request_and_normalizes(final_url: str, expected: str) -> None:
    opener = FakeOpener(final_url)
    assert resolve_short_link("on.soundcloud.com/AbC123?si=xyz", opener) == expected
    assert opener.requests == ["https://on.soundcloud.com/AbC123"]
    assert opener.responses[0].closed


@pytest.mark.parametrize(
    "final_url",
    [
        pytest.param("https://soundcloud.com/", id="home page"),
        pytest.param("https://soundcloud.com/artist", id="profile"),
        pytest.param("https://on.soundcloud.com/AbC123", id="no redirect"),
        pytest.param("https://example.com/", id="another site"),
    ],
)
def test_a_short_link_that_leads_nowhere_useful_is_unsupported(final_url: str) -> None:
    opener = FakeOpener(final_url)
    with pytest.raises(UnsupportedInput, match=re.escape(SHORT_LINK_DEAD_END)):
        resolve_short_link("https://on.soundcloud.com/AbC123", opener)
    assert opener.responses[0].closed


@pytest.mark.parametrize(
    "short_link",
    [
        pytest.param("https://user:pw@on.soundcloud.com:8443/AbC123", id="userinfo and port"),
        pytest.param(
            "https://w.soundcloud.com/player/?url=https%3A//on.soundcloud.com/AbC123",
            id="inside an embed",
        ),
        pytest.param("<https://on.soundcloud.com/AbC123#top>.", id="in brackets with a fragment"),
    ],
)
def test_the_short_link_request_goes_only_to_on_soundcloud_com(short_link: str) -> None:
    opener = FakeOpener("https://soundcloud.com/artist/track")
    assert resolve_short_link(short_link, opener) == "https://soundcloud.com/artist/track"
    assert opener.requests == ["https://on.soundcloud.com/AbC123"]


def test_resolve_short_link_refuses_a_link_that_isnt_short() -> None:
    opener = FakeOpener("https://soundcloud.com/artist/track")
    secret_link = "https://soundcloud.com/artist/track/s-AbCdE123fGh"
    with pytest.raises(ValueError, match=re.escape("needs an on.soundcloud.com link")) as raised:
        resolve_short_link(secret_link, opener)
    assert "s-AbCdE123fGh" not in str(raised.value)
    assert opener.requests == []


def test_resolve_short_link_passes_opener_errors_on() -> None:
    def failing_opener(url: str) -> FakeResponse:
        raise OSError("offline")

    with pytest.raises(OSError, match="offline"):
        resolve_short_link("https://on.soundcloud.com/AbC123", failing_opener)


class FakeHTTPError(Exception):
    """Like yt-dlp's HTTPError, which keeps the response it failed on."""

    def __init__(self, response: FakeResponse) -> None:
        super().__init__("HTTP Error 403: Forbidden")
        self.response = response


def refusing_opener(final_url: str) -> Callable[[str], FakeResponse]:
    """An opener whose redirect lands on final_url, which then answers 403."""

    def opener(url: str) -> FakeResponse:
        raise FakeHTTPError(FakeResponse(final_url))

    return opener


def test_a_blocked_redirect_target_still_resolves() -> None:
    opener = refusing_opener("https://soundcloud.com/artist/track?si=abc123")
    assert resolve_short_link("https://on.soundcloud.com/AbC123", opener) == (
        "https://soundcloud.com/artist/track"
    )


@pytest.mark.parametrize(
    "final_url",
    [
        pytest.param("https://on.soundcloud.com/AbC123", id="the short link itself"),
        pytest.param("https://soundcloud.com/artist", id="a profile"),
        pytest.param("https://example.com/artist/track", id="another site"),
    ],
)
def test_a_blocked_request_that_got_nowhere_useful_raises_the_error(final_url: str) -> None:
    with pytest.raises(FakeHTTPError):
        resolve_short_link("https://on.soundcloud.com/AbC123", refusing_opener(final_url))


def raising(error: Exception) -> Callable[[str], FakeResponse]:
    def opener(url: str) -> FakeResponse:
        raise error

    return opener


def test_a_blocked_redirect_target_closes_the_response_it_read() -> None:
    response = FakeResponse("https://soundcloud.com/artist/track")
    resolve_short_link("https://on.soundcloud.com/AbC123", raising(FakeHTTPError(response)))
    assert response.closed


def test_an_error_raised_again_keeps_its_response_open_for_the_caller() -> None:
    response = FakeResponse("https://soundcloud.com/artist")
    with pytest.raises(FakeHTTPError) as raised:
        resolve_short_link("https://on.soundcloud.com/AbC123", raising(FakeHTTPError(response)))
    assert raised.value.response is response
    assert not response.closed
