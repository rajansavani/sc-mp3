"""Tests for resolving SoundCloud tracks, replayed from recorded and hand-built fixtures."""

import functools
import importlib.util
import logging
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from yt_dlp.extractor.soundcloud import SoundcloudBaseIE, SoundcloudIE
from yt_dlp.networking.exceptions import TransportError

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import OriginalDownload, SourceInfo
from scmp3.core.sources import soundcloud
from scmp3.core.urls import UnsupportedInput


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

TYPICAL_URL = "https://soundcloud.com/the80m/the-following"
# The protocol_codec names yt-dlp checks each SoundCloud transcoding by.
SHORT_FORMAT_NAMES = ("hls_aac", "http_aac", "hls-aes_aac", "hls_mp3", "http_mp3", "hls_opus")
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJl"


def replay(name: str, **changes: Any) -> Any:
    """A ReplayYdl for a fixture, with top-level fixture keys replaced by `changes`."""
    return record.ReplayYdl({**record.load(name), **changes})


def resolve(ydl: Any, token: str | None = None) -> SourceInfo:
    return soundcloud.resolve(ydl.fixture["url"], ydl, token)


def test_a_typical_track_maps_every_field() -> None:
    source = resolve(replay("aac_typical"))
    assert source.kind == "soundcloud"
    assert source.id == "soundcloud:tracks:343609555"
    assert source.url == TYPICAL_URL
    assert source.title == "The Following"
    assert source.uploader == "80M"
    assert source.artists == ["80M"]
    assert source.album is None
    assert source.release_date == date(2017, 9, 26)
    assert source.upload_date == date(2017, 9, 22)
    assert source.genre == "Dance & EDM"
    # An empty description counts as none.
    assert source.description is None
    assert source.duration == 397.175
    assert source.full_duration == 397.228
    assert source.artwork_url == "https://i1.sndcdn.com/artworks-000243916348-ktoo7d-original.jpg"
    assert source.artwork_is_avatar is False
    assert source.isrc == "QZH5E1912919"
    assert source.label is None
    assert source.explicit is None
    assert source.availability == "ok"
    assert source.playlist is None
    assert [fmt.format_id for fmt in source.formats] == ["hls_aac_96k", "hls_aac_160k"]


def test_formats_carry_codec_bitrate_and_flags() -> None:
    aac_96, aac_160 = resolve(replay("aac_typical")).formats
    assert (aac_160.ext, aac_160.codec, aac_160.bitrate) == ("m4a", "aac", 160)
    assert (aac_96.is_preview, aac_96.is_original) == (False, False)
    mp3 = resolve(replay("legacy_mp3")).formats
    assert [(fmt.format_id, fmt.codec, fmt.bitrate) for fmt in mp3] == [
        ("hls_mp3_0_0", "mp3", 128),
        ("http_mp3_0_0", "mp3", 128),
    ]


def test_resolve_keeps_yt_dlps_info_for_the_download() -> None:
    resolved = soundcloud.resolve_track(TYPICAL_URL, replay("aac_typical"))
    assert resolved.info is not None
    assert resolved.info["id"] == "343609555"


def test_a_source_survives_a_round_trip_through_json() -> None:
    source = resolve(replay("publisher"))
    assert SourceInfo.from_dict(source.to_dict()) == source


def test_the_link_is_normalized_before_yt_dlp_sees_it() -> None:
    ydl = replay("aac_typical")
    source = soundcloud.resolve("https://m.soundcloud.com/the80m/the-following/?si=abc#t=1", ydl)
    assert ydl.extracted == [TYPICAL_URL]
    assert source.url == TYPICAL_URL


def test_a_public_api_link_resolves_to_the_tracks_own_page() -> None:
    # Sets list some tracks by their api link, and the tags and history should hold the page.
    ydl = replay("aac_typical")
    source = soundcloud.resolve("https://api-v2.soundcloud.com/tracks/343609555", ydl)
    assert ydl.extracted == ["https://api.soundcloud.com/tracks/343609555"]
    assert source.url == TYPICAL_URL


def test_a_secret_api_link_is_kept_since_its_page_would_need_the_token() -> None:
    link = "https://api.soundcloud.com/tracks/343609555?secret_token=s-AbCdE"
    assert soundcloud.resolve(link, replay("aac_typical")).url == link


def test_one_api_call_asks_resolve_for_the_link_with_the_cached_client_id() -> None:
    ydl = replay("aac_typical")
    resolve(ydl)
    [request] = ydl.requests
    url = urlsplit(request.url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == soundcloud.API_RESOLVE
    assert parse_qs(url.query) == {"url": [TYPICAL_URL], "client_id": [record.FAKE_CLIENT_ID]}


def test_a_token_goes_on_the_api_request_and_nowhere_else() -> None:
    shared_headers = dict(SoundcloudBaseIE._HEADERS)
    ydl = replay("aac_typical")
    resolve(ydl, token=TOKEN)
    [request] = ydl.requests
    assert request.headers["Authorization"] == f"OAuth {TOKEN}"
    # yt-dlp shares this dict between every YoutubeDL, so resolve must never touch it.
    assert shared_headers == SoundcloudBaseIE._HEADERS


def test_without_a_token_the_api_request_has_no_authorization() -> None:
    ydl = replay("aac_typical")
    resolve(ydl)
    [request] = ydl.requests
    assert "Authorization" not in request.headers


def test_publisher_details_and_the_label_come_from_api_v2() -> None:
    source = resolve(replay("publisher"))
    # SoundCloud's own data has a question mark where the é should be.
    assert source.artists == ["Michael Bubl?"]
    assert source.album == "Crazy Love"
    assert source.isrc == "USRE10901473"
    # The label is beside publisher_metadata on the track, not inside it.
    assert source.label == "Warner Bros Records"
    assert source.artwork_url is not None
    assert source.artwork_url.endswith("-original.png")


def test_artists_come_from_artists_never_from_artist() -> None:
    fixture = record.load("publisher")
    # yt-dlp swaps commas in `artist` for FULLWIDTH COMMA (yt-dlp #16295).
    fixture["info"]["artist"] = "HUGEL\N{FULLWIDTH COMMA} Diplo"
    fixture["info"]["artists"] = ["HUGEL, Diplo"]
    fixture["api"]["publisher_metadata"]["artist"] = "HUGEL, Diplo"
    source = resolve(record.ReplayYdl(fixture))
    assert source.artists == ["HUGEL, Diplo"]


def test_artists_ignore_artist_even_when_artists_is_missing() -> None:
    fixture = record.load("publisher")
    fixture["info"]["artist"] = "HUGEL\N{FULLWIDTH COMMA} Diplo"
    del fixture["info"]["artists"]
    assert resolve(record.ReplayYdl(fixture)).artists == []


@pytest.mark.parametrize(
    "api_error",
    [
        {"kind": "engine_outdated", "status": 401},
        {"kind": "engine_outdated", "status": 403},
        {"kind": "unavailable", "status": 404},
        {"kind": "rate_limited", "status": 429},
        {"kind": "service_down", "status": 500},
        {"kind": "offline", "status": None},
    ],
)
def test_when_the_api_call_fails_the_yt_dlp_fields_are_used(
    api_error: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=soundcloud.__name__):
        source = resolve(replay("preview_only", api=None, api_error=api_error))
    assert source.id == "soundcloud:tracks:339400425"
    assert source.title == "Look What You Made Me Do"
    assert source.uploader == "Taylor Swift"
    assert source.artists == ["Taylor Swift"]
    assert source.release_date == date(2017, 11, 17)
    assert source.upload_date == date(2017, 8, 25)
    assert source.genre == "Pop"
    assert source.artwork_url == "https://i1.sndcdn.com/artworks-kjYVnsdGzHIQ-0-original.jpg"
    # Only api-v2 has these.
    assert (source.album, source.isrc, source.label, source.full_duration) == (
        None,
        None,
        None,
        None,
    )
    # The format names alone still give the preview away.
    assert source.availability == "preview_only"
    assert "Carrying on without api-v2's fields" in caplog.text


def test_without_a_cached_client_id_the_api_call_is_skipped() -> None:
    ydl = replay("aac_typical")
    ydl.cache.store("soundcloud", "client_id", None)
    source = resolve(ydl)
    assert ydl.requests == []
    assert source.title == "The Following"
    assert source.isrc is None


@pytest.mark.parametrize(
    ("status", "token", "kind"),
    [
        (401, TOKEN, ErrorKind.auth_expired),
        (401, None, ErrorKind.engine_outdated),
        (403, TOKEN, ErrorKind.engine_outdated),
        (403, None, ErrorKind.engine_outdated),
        (404, None, ErrorKind.unavailable),
    ],
)
def test_api_track_statuses_mean_what_they_mean_on_resolve(
    status: int, token: str | None, kind: ErrorKind
) -> None:
    ydl = replay("aac_typical", api=None, api_error={"kind": "", "status": status})
    with pytest.raises(ScmpError) as error_info:
        soundcloud.api_track(ydl, TYPICAL_URL, token)
    assert error_info.value.kind is kind


def test_api_track_refuses_an_answer_that_isnt_a_track() -> None:
    fixture = record.load("aac_typical")
    fixture["api"]["kind"] = "user"
    with pytest.raises(ScmpError) as error_info:
        soundcloud.api_track(record.ReplayYdl(fixture), TYPICAL_URL)
    assert error_info.value.kind is ErrorKind.engine_outdated


def test_the_release_date_falls_back_to_the_upload_date() -> None:
    # Neither api-v2 nor yt-dlp has a release date for this track.
    source = resolve(replay("avatar_artwork"))
    assert source.release_date == source.upload_date == date(2017, 2, 26)


def test_a_cover_that_is_the_uploaders_avatar_is_flagged() -> None:
    source = resolve(replay("avatar_artwork"))
    assert source.artwork_url == "https://i1.sndcdn.com/avatars-000298106657-ogrgmg-original.jpg"
    assert source.artwork_is_avatar is True


def test_soundclouds_default_avatar_means_no_cover() -> None:
    source = resolve(replay("legacy_mp3"))
    assert source.artwork_url is None
    assert source.artwork_is_avatar is False


def test_the_cover_falls_back_to_t500x500_without_an_original() -> None:
    fixture = record.load("aac_typical")
    thumbnails = fixture["info"]["thumbnails"]
    fixture["info"]["thumbnails"] = [thumb for thumb in thumbnails if thumb["id"] != "original"]
    source = resolve(record.ReplayYdl(fixture))
    assert source.artwork_url == "https://i1.sndcdn.com/artworks-000243916348-ktoo7d-t500x500.jpg"


def test_the_cover_is_never_an_upscaled_size() -> None:
    fixture = record.load("aac_typical")
    fixture["info"]["thumbnails"] = [
        {"id": "t3000x3000", "url": "https://i1.sndcdn.com/artworks-x-t3000x3000.jpg"},
        {"id": "large", "url": "https://i1.sndcdn.com/artworks-x-large.jpg"},
    ]
    assert resolve(record.ReplayYdl(fixture)).artwork_url is None


def test_a_downloadable_track_offers_its_original_after_sign_in() -> None:
    source = resolve(replay("aac_typical"))
    assert source.original == OriginalDownload(ext=None, needs_sign_in=True)


def test_a_signed_in_resolve_knows_the_originals_file_type() -> None:
    source = resolve(replay("original_wav"), token=TOKEN)
    assert source.original == OriginalDownload(ext="wav", needs_sign_in=False)


def test_a_track_without_downloads_has_no_original() -> None:
    assert resolve(replay("publisher")).original is None


def test_the_preview_only_fixture_is_preview_only() -> None:
    source = resolve(replay("preview_only"))
    assert source.availability == "preview_only"
    assert source.duration == 30.0
    assert source.full_duration == 211.905
    assert source.album == "reputation"
    assert source.explicit is False
    assert source.label == "Taylor Swift"


def full_length_copy_of_preview_only() -> dict[str, Any]:
    """The preview-only fixture with all three preview signals taken away."""
    fixture = record.load("preview_only")
    for fmt in fixture["info"]["formats"]:
        fmt["format_id"] = fmt["format_id"].removesuffix("_preview")
        fmt["preference"] = None
    fixture["info"]["duration"] = 211.905
    fixture["api"]["duration"] = 211905
    fixture["api"]["policy"] = "MONETIZE"
    return fixture


def test_without_preview_signals_the_track_is_ok() -> None:
    assert resolve(record.ReplayYdl(full_length_copy_of_preview_only())).availability == "ok"


def test_snip_policy_alone_means_preview_only() -> None:
    fixture = full_length_copy_of_preview_only()
    fixture["api"]["policy"] = "SNIP"
    assert resolve(record.ReplayYdl(fixture)).availability == "preview_only"


def test_preview_format_names_alone_mean_preview_only() -> None:
    fixture = full_length_copy_of_preview_only()
    for fmt in fixture["info"]["formats"]:
        fmt["format_id"] += "_preview"
    assert resolve(record.ReplayYdl(fixture)).availability == "preview_only"


def test_thirty_seconds_of_a_longer_track_alone_means_preview_only() -> None:
    fixture = full_length_copy_of_preview_only()
    fixture["info"]["duration"] = 30.0
    fixture["api"]["duration"] = 30000
    assert resolve(record.ReplayYdl(fixture)).availability == "preview_only"


def test_a_track_that_really_is_thirty_seconds_is_ok() -> None:
    fixture = full_length_copy_of_preview_only()
    fixture["info"]["duration"] = 30.0
    fixture["api"]["duration"] = 30000
    fixture["api"]["full_duration"] = 30000
    assert resolve(record.ReplayYdl(fixture)).availability == "ok"


def test_the_drm_only_fixture_resolves_as_drm_from_api_v2() -> None:
    ydl = replay("drm_only")
    resolved = soundcloud.resolve_track(ydl.fixture["url"], ydl)
    source = resolved.source
    assert resolved.info is None
    assert source.availability == "drm"
    assert source.id == "soundcloud:tracks:296372062"
    assert source.title == "Traffic (TCP Demo 2008)"
    assert source.uploader == "Ashbury Heights"
    assert source.artists == ["Ashbury Heights"]
    assert source.formats == []
    assert source.label == "Out of Line Music"
    assert source.explicit is False
    assert source.release_date == date(2012, 7, 20)
    assert source.upload_date == date(2016, 12, 5)
    # The original's file type takes a request to find, so the cover is t500x500.
    assert source.artwork_url == "https://i1.sndcdn.com/artworks-klY5iFNNxzXO-0-t500x500.jpg"
    assert source.artwork_is_avatar is False


def test_a_refused_track_without_artwork_falls_back_to_the_uploaders_avatar() -> None:
    fixture = record.load("drm_only")
    fixture["api"]["artwork_url"] = None
    fixture["api"]["user"]["avatar_url"] = "https://i1.sndcdn.com/avatars-000123-abcdef-large.jpg"
    source = resolve(record.ReplayYdl(fixture))
    assert source.artwork_url == "https://i1.sndcdn.com/avatars-000123-abcdef-t500x500.jpg"
    assert source.artwork_is_avatar is True


def test_a_refused_track_with_only_the_default_avatar_has_no_cover() -> None:
    fixture = record.load("drm_only")
    # This uploader has SoundCloud's default avatar, which has no sized versions.
    assert fixture["api"]["user"]["avatar_url"].endswith("/images/default_avatar_large.png")
    fixture["api"]["artwork_url"] = None
    source = resolve(record.ReplayYdl(fixture))
    assert source.artwork_url is None
    assert source.artwork_is_avatar is False


def test_drm_without_api_v2_raises_drm() -> None:
    ydl = replay("drm_only", api=None, api_error={"kind": "service_down", "status": 503})
    with pytest.raises(ScmpError) as error_info:
        resolve(ydl)
    assert error_info.value.kind is ErrorKind.drm


def geo_blocked_fixture() -> dict[str, Any]:
    """The DRM fixture as a track blocked in this country, as yt-dlp and api-v2 report it."""
    fixture = record.load("drm_only")
    fixture["info_error"] = {
        "type": "GeoRestrictedError",
        "message": "ERROR: This video is not available from your location due to geo restriction",
        "status": None,
    }
    fixture["api"]["policy"] = "BLOCK"
    return fixture


def test_a_geo_blocked_track_resolves_as_geo_blocked() -> None:
    source = resolve(record.ReplayYdl(geo_blocked_fixture()))
    assert source.availability == "geo_blocked"
    assert source.title == "Traffic (TCP Demo 2008)"


def test_a_block_policy_means_geo_blocked_even_with_formats() -> None:
    fixture = record.load("aac_typical")
    fixture["api"]["policy"] = "BLOCK"
    assert resolve(record.ReplayYdl(fixture)).availability == "geo_blocked"


def test_a_missing_track_raises_unavailable_without_an_api_call() -> None:
    ydl = replay("not_found")
    with pytest.raises(ScmpError) as error_info:
        resolve(ydl)
    assert error_info.value.kind is ErrorKind.unavailable
    assert ydl.requests == []


class ShortLinkYdl(record.ReplayYdl):
    """A replay that also answers the one request a short link takes."""

    def __init__(self, fixture: dict[str, Any], target: str | Exception) -> None:
        super().__init__(fixture)
        self.target = target

    def urlopen(self, request: Any) -> Any:
        if not isinstance(request, str):
            return super().urlopen(request)
        if isinstance(self.target, Exception):
            raise self.target
        return SimpleNamespace(url=self.target, close=lambda: None)


SHORT_LINK = "https://on.soundcloud.com/AbCd1234"


def test_a_short_link_to_a_track_gives_the_normalized_track_link() -> None:
    target = "https://soundcloud.com/the80m/the-following?si=abc&utm_source=mobi"
    ydl = ShortLinkYdl(record.load("aac_typical"), target)
    kind, link = soundcloud.follow_short_link(SHORT_LINK, ydl)
    assert (kind, link) == ("soundcloud_track", TYPICAL_URL)
    assert soundcloud.resolve(link, ydl).url == TYPICAL_URL


def test_a_short_link_to_a_set_gives_the_set_link_for_the_caller_to_handle() -> None:
    target = "https://soundcloud.com/the80m/sets/an-album?si=abc"
    ydl = ShortLinkYdl(record.load("aac_typical"), target)
    kind, link = soundcloud.follow_short_link(SHORT_LINK, ydl)
    assert (kind, link) == ("soundcloud_set", "https://soundcloud.com/the80m/sets/an-album")
    assert ydl.extracted == []


def test_a_short_link_that_leads_nowhere_gives_the_reason() -> None:
    ydl = ShortLinkYdl(record.load("aac_typical"), "https://soundcloud.com/discover")
    with pytest.raises(UnsupportedInput, match="short link"):
        soundcloud.follow_short_link(SHORT_LINK, ydl)


def test_a_short_link_that_cant_be_reached_is_offline() -> None:
    ydl = ShortLinkYdl(record.load("aac_typical"), TransportError(msg="Could not resolve host"))
    with pytest.raises(ScmpError) as error_info:
        soundcloud.follow_short_link(SHORT_LINK, ydl)
    assert error_info.value.kind is ErrorKind.offline


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        (SHORT_LINK, "soundcloud_short"),
        ("https://soundcloud.com/the80m/sets/an-album", "soundcloud_set"),
    ],
)
def test_resolve_refuses_anything_but_a_track_link_before_any_request(url: str, kind: str) -> None:
    ydl = ShortLinkYdl(record.load("aac_typical"), TYPICAL_URL)
    with pytest.raises(ValueError, match=kind):
        soundcloud.resolve(url, ydl)
    assert (ydl.extracted, ydl.requests) == ([], [])


def test_make_ydl_asks_only_for_aac_and_mp3_and_never_hls_aes() -> None:
    with soundcloud.make_ydl() as ydl:
        formats = ydl.params["extractor_args"]["soundcloud"]["formats"]
        # yt-dlp's own check of each transcoding against the list; a *_aac pattern lets hls-aes in.
        is_requested = SoundcloudIE(ydl)._is_requested
        requested = {name for name in SHORT_FORMAT_NAMES if is_requested(name)}
    assert formats == ["hls_aac", "http_aac", "hls_mp3", "http_mp3"]
    assert requested == {"hls_aac", "http_aac", "hls_mp3", "http_mp3"}


def test_each_youtubedl_gets_its_own_copy_of_the_options() -> None:
    with soundcloud.make_ydl() as first, soundcloud.make_ydl() as second:
        first.params["extractor_args"]["soundcloud"]["formats"].append("hls_opus")
        assert "hls_opus" not in second.params["extractor_args"]["soundcloud"]["formats"]


def test_make_ydl_fits_the_download_factory() -> None:
    def hook(status: dict[str, Any]) -> None:
        pass

    # download() calls its factory with these keywords; a signed-in caller binds the token.
    factory = functools.partial(soundcloud.make_ydl, TOKEN)
    with factory(progress_hook=hook, extra={"format": "hls_aac_160k"}) as ydl:
        params = ydl.params
    assert params["progress_hooks"] == [hook]
    assert params["format"] == "hls_aac_160k"
    assert params["username"] == "oauth"
    assert params["extractor_args"]["soundcloud"]["formats"] == list(soundcloud.STREAM_FORMATS)


def test_extra_options_override_the_soundcloud_ones() -> None:
    extra = {"extractor_args": {"soundcloud": {"formats": ["hls_aac"]}}}
    with soundcloud.make_ydl(extra=extra) as ydl:
        assert ydl.params["extractor_args"] == extra["extractor_args"]
