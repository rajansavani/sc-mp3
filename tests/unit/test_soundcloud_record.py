"""Tests for the fixture recorder: scrubbing secrets, and the fixtures it has saved."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from yt_dlp.networking.common import Request
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import DownloadError, ExtractorError, GeoRestrictedError

from scmp3.core.log import MASK, redact


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

JWT = (
    "eyJhbGciOiJSUzI1NiIsInR5cCI6ImF0K0pXVCJ9.eyJzdWIiOiJzb3VuZGNsb3VkOnVzZXJzOjEifQ.c2ln-bmF0_dXJl"
)
SIGNED = (
    "https://cf-hls-media.sndcdn.com/playlist/abc.128.mp3/playlist.m3u8"
    "?Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiKiJ9XX0_&Signature=AbC~dEf-123_&Key-Pair-Id=APKAXYZ"
)
# Words the published fixtures must never contain, in any case.
FORBIDDEN = ("client_id", "signature", "oauth", "key-pair-id", "policy=")


def test_signed_url_parameters_are_dropped() -> None:
    assert (
        record.scrub(SIGNED) == "https://cf-hls-media.sndcdn.com/playlist/abc.128.mp3/playlist.m3u8"
    )


def test_other_query_parameters_are_kept() -> None:
    url = "https://playback.media-streaming.soundcloud.cloud/x/aac_160k/y/playlist.m3u8?expires=1792381573"
    assert record.scrub(url) == url
    assert record.scrub(f"{url}&Signature=abc") == url


def test_the_client_id_and_tokens_are_dropped_from_urls() -> None:
    url = "https://api-v2.soundcloud.com/resolve?url=x&client_id=AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert record.scrub(url) == "https://api-v2.soundcloud.com/resolve?url=x"
    assert record.scrub("https://api.soundcloud.com/tracks/1?secret_token=s-AbCdE") == (
        "https://api.soundcloud.com/tracks/1"
    )


def test_s3_signature_parameters_are_dropped() -> None:
    url = (
        "https://example-bucket.s3.amazonaws.com/original.wav?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=AKIAEXAMPLE%2F20261019%2Fus-east-1%2Fs3%2Faws4_request"
        "&X-Amz-Date=20261019T000000Z&X-Amz-Expires=600&X-Amz-Signature=0123456789abcdef"
    )
    assert record.scrub(url) == (
        "https://example-bucket.s3.amazonaws.com/original.wav?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Date=20261019T000000Z&X-Amz-Expires=600"
    )


@pytest.mark.parametrize(
    ("text", "scrubbed"),
    [
        ("request failed: client_id=AbCdEfGh0123", f"request failed: client_id={MASK}"),
        ("client_id: AbCdEfGh0123, retrying", f"client_id: {MASK}, retrying"),
        ('{"client_id": "AbCdEfGh0123"}', f'{{"client_id": "{MASK}"}}'),
    ],
)
def test_a_client_id_outside_a_url_is_masked(text: str, scrubbed: str) -> None:
    assert record.scrub(text) == scrubbed


def test_urls_inside_text_are_scrubbed_too() -> None:
    text = f"Unable to download {SIGNED}: HTTP Error 403"
    assert record.scrub(text) == (
        "Unable to download https://cf-hls-media.sndcdn.com/playlist/abc.128.mp3/playlist.m3u8"
        ": HTTP Error 403"
    )


@pytest.mark.parametrize(
    "key",
    [
        "track_authorization",
        "secret_token",
        "oauth_token",
        "Authorization",
        "Cookie",
        "cookies",
        "client_id",
        "password",
    ],
)
def test_secret_keys_are_dropped_with_their_values(key: str) -> None:
    assert record.scrub({"title": "x", key: "value"}) == {"title": "x"}


@pytest.mark.parametrize(
    "key", ["first_name", "last_name", "full_name", "date_of_birth", "city", "country_code"]
)
def test_uploaders_personal_details_are_dropped(key: str) -> None:
    assert record.scrub({"user": {"username": "80M", key: "x"}}) == {"user": {"username": "80M"}}


def test_token_shapes_in_any_text_are_masked() -> None:
    assert record.scrub(f"token {JWT} here") == f"token {MASK} here"
    assert record.scrub("OAuth 2-123456-789012-AbCdEfGh") == f"OAuth {MASK}"
    assert (
        record.scrub("https://soundcloud.com/a/b/s-AbCdEfGh")
        == f"https://soundcloud.com/a/b/s-{MASK}"
    )


def test_nested_data_is_scrubbed_everywhere() -> None:
    data = {"formats": [{"url": SIGNED, "http_headers": {"Cookie": "datadome=abc"}}], "n": 3}
    assert record.scrub(data) == {
        "formats": [
            {
                "url": "https://cf-hls-media.sndcdn.com/playlist/abc.128.mp3/playlist.m3u8",
                "http_headers": {},
            }
        ],
        "n": 3,
    }


def test_ordinary_data_is_left_alone() -> None:
    data = {"policy": "SNIP", "title": "Burn Down - Demo 2008", "duration": 276793, "x": None}
    assert record.scrub(data) == data


def test_scrubbing_twice_changes_nothing() -> None:
    data = {"url": SIGNED, "text": f"OAuth {JWT}", "secret_token": "s-AbCdE"}
    once = record.scrub(data)
    assert record.scrub(once) == once


def test_save_scrubs_what_it_writes(tmp_path: Path) -> None:
    fixture = {
        "message": "request failed: client_id=AbCdEfGh0123",
        "url": SIGNED,
        "track_authorization": JWT,
    }
    record.save("hand_built", fixture, tmp_path)
    assert record.load("hand_built", tmp_path) == {
        "message": f"request failed: client_id={MASK}",
        "url": "https://cf-hls-media.sndcdn.com/playlist/abc.128.mp3/playlist.m3u8",
    }


def test_a_saved_fixture_loads_back_unchanged(tmp_path: Path) -> None:
    fixture = {"url": "https://soundcloud.com/a/b", "title": "Café Tacvba"}
    record.save("sample", fixture, tmp_path)
    assert record.load("sample", tmp_path) == fixture
    assert record.names(tmp_path) == ["sample"]
    assert b"\r\n" not in (tmp_path / "sample.json").read_bytes()


def wrapped(inner: Exception, message: str) -> DownloadError:
    """A DownloadError wrapping `inner`, as YoutubeDL.extract_info raises it."""
    return DownloadError(message, exc_info=(type(inner), inner, None))


DRM = "[soundcloud] 1: This video is DRM protected"
GEO = "This video is not available from your location due to geo restriction"
NOT_FOUND = "[soundcloud] a/b: Unable to download JSON metadata: HTTP Error 404: Not Found"


@pytest.mark.parametrize(
    "error",
    [
        wrapped(ExtractorError(DRM, expected=True), f"ERROR: {DRM}"),
        wrapped(GeoRestrictedError(GEO), f"ERROR: {GEO}"),
        wrapped(record.http_error(404), f"ERROR: {NOT_FOUND}"),
    ],
)
def test_a_recorded_yt_dlp_error_replays_as_the_same_error(error: DownloadError) -> None:
    recorded = record.describe_download_error(error)
    rebuilt = record.rebuild_download_error(json.loads(json.dumps(recorded)))
    assert str(rebuilt) == str(error)
    original_inner, rebuilt_inner = error.exc_info[1], rebuilt.exc_info[1]
    assert type(rebuilt_inner) is type(original_inner)
    if isinstance(original_inner, HTTPError):
        assert rebuilt_inner.status == original_inner.status


def test_replay_answers_from_the_fixture() -> None:
    fixture = {"info": {"id": "1"}, "info_error": None, "api": {"kind": "track"}, "api_error": None}
    ydl = record.ReplayYdl(fixture)
    assert ydl.extract_info("https://soundcloud.com/a/b", download=False) == {"id": "1"}
    response = ydl.urlopen(Request("https://api-v2.soundcloud.com/resolve?url=x"))
    assert json.loads(response.read()) == {"kind": "track"}
    assert ydl.cache.load("soundcloud", "client_id") == record.FAKE_CLIENT_ID


COMMITTED = [(record.FIXTURES_DIR, name) for name in record.names()] + [
    (record.SETS_DIR, name) for name in record.names(record.SETS_DIR)
]


@pytest.mark.parametrize(
    ("folder", "name"), COMMITTED, ids=[f"{folder.name}/{name}" for folder, name in COMMITTED]
)
def test_every_committed_fixture_is_already_scrubbed(folder: Path, name: str) -> None:
    fixture = record.load(name, folder)
    assert record.scrub(fixture) == fixture
    text = (folder / f"{name}.json").read_text(encoding="utf-8")
    assert redact(text) == text
    # A JWT, and CloudFront's Policy, both start with base64 for '{"'.
    assert "eyJ" not in text
    for word in FORBIDDEN:
        assert word not in text.lower()


def test_the_fixture_set_covers_every_case_the_ticket_lists() -> None:
    expected = {
        "aac_typical",
        "legacy_mp3",
        "go_plus",
        "original_wav",
        "original_mp3",
        "preview_only",
        "drm_only",
        "avatar_artwork",
        "publisher",
        "not_found",
    }
    assert expected <= set(record.names())


def test_the_set_fixtures_are_an_album_and_a_plain_playlist() -> None:
    assert record.names(record.SETS_DIR) == ["album", "playlist"]
    assert record.load("album", record.SETS_DIR)["api"]["set_type"] == "album"
    assert record.load("playlist", record.SETS_DIR)["api"]["set_type"] == ""
