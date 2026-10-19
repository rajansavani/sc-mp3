"""Tests for asking GitHub for a repo's latest release: the cache, the rate limit and errors."""

import io
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from scmp3.core import github
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.github import Asset, Release

REPO = "denoland/deno"
LATEST = "https://api.github.com/repos/denoland/deno/releases/latest"
NOW = 1_800_000_000.0
DOWNLOADS = "https://github.com/denoland/deno/releases/download"
# A rate limit reset ten minutes from NOW, as GitHub sends it.
RESET = str(int(NOW) + 600)


def release_json(tag: str = "v2.9.7", names: tuple[str, ...] = ("deno.zip",)) -> bytes:
    """GitHub's JSON for a release, cut down to the fields that matter here."""
    assets = [
        {"name": name, "size": 10, "browser_download_url": f"{DOWNLOADS}/{tag}/{name}"}
        for name in names
    ]
    return json.dumps({"tag_name": tag, "draft": False, "assets": assets}).encode()


class FakeResponse(io.BytesIO):
    """A response shaped like urllib's: a status, email-style headers and a readable body."""

    def __init__(
        self, body: bytes = b"", status: int = 200, headers: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value


class FakeGitHub:
    """Answers each request with the next canned response, and keeps every request's headers."""

    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, str]] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> FakeResponse:
        assert url == LATEST
        self.requests.append(dict(headers))
        if not self.responses:
            raise AssertionError("GitHub was asked once more than the test expected")
        return self.responses.pop(0)


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def ok(tag: str = "v2.9.7", etag: str = '"etag-1"', **headers: str) -> FakeResponse:
    return FakeResponse(release_json(tag), headers={"ETag": etag, **headers})


def limited(status: int = 403, **headers: str) -> FakeResponse:
    return FakeResponse(b'{"message": "API rate limit exceeded"}', status, headers)


def used_up(reset: str = RESET) -> dict[str, str]:
    """The headers of an answer that used the last request the rate limit allows."""
    return {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}


def latest(web: FakeGitHub, clock: Clock | None = None) -> Release:
    return github.github_latest(REPO, opener=web, clock=clock or Clock())


def raised_kind(web: FakeGitHub, clock: Clock | None = None) -> ErrorKind:
    with pytest.raises(ScmpError) as raised:
        latest(web, clock)
    return raised.value.kind


# Releases


def test_the_latest_release_has_its_tag_version_and_files() -> None:
    release = latest(FakeGitHub(ok()))
    assert release == Release(
        repo=REPO,
        tag="v2.9.7",
        assets={"deno.zip": Asset("deno.zip", f"{DOWNLOADS}/v2.9.7/deno.zip")},
    )
    assert release.version == "2.9.7"


def test_a_tag_without_a_v_is_its_own_version() -> None:
    web = FakeGitHub(FakeResponse(release_json("2026.08.19")))
    assert latest(web).version == "2026.08.19"


def test_requests_ask_for_githubs_json_at_a_fixed_api_version() -> None:
    web = FakeGitHub(ok())
    latest(web)
    assert web.requests[0]["Accept"] == "application/vnd.github+json"
    assert web.requests[0]["X-GitHub-Api-Version"] == "2022-11-28"
    assert "If-None-Match" not in web.requests[0]


# The ETag cache


def test_an_unchanged_release_comes_from_the_cache_after_a_304() -> None:
    web = FakeGitHub(ok(etag='"abc"'), FakeResponse(status=304))
    first = latest(web)
    assert latest(web) == first
    assert web.requests[1]["If-None-Match"] == '"abc"'


def test_a_new_release_replaces_the_cached_one() -> None:
    web = FakeGitHub(ok("v2.9.7", '"one"'), ok("v2.9.8", '"two"'), FakeResponse(status=304))
    latest(web)
    assert latest(web).tag == "v2.9.8"
    assert latest(web).tag == "v2.9.8"
    assert web.requests[2]["If-None-Match"] == '"two"'


def test_the_cache_is_kept_per_repo_in_the_cache_folder(scmp3_home: Path) -> None:
    latest(FakeGitHub(ok()))
    cached = json.loads((scmp3_home / "cache" / "github" / "denoland" / "deno.json").read_text())
    assert cached["etag"] == '"etag-1"'
    assert cached["release"]["tag_name"] == "v2.9.7"


@pytest.mark.parametrize(
    "text",
    ["not json", "[]", '{"etag": 5}', '{"release": {"tag_name": 7, "assets": []}}'],
    ids=["not json", "a list", "a mistyped etag", "a mistyped release"],
)
def test_a_damaged_cache_is_ignored(scmp3_home: Path, text: str) -> None:
    cache = scmp3_home / "cache" / "github" / "denoland" / "deno.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(text)
    web = FakeGitHub(ok())
    assert latest(web).tag == "v2.9.7"
    assert "If-None-Match" not in web.requests[0]


def test_a_cache_that_isnt_utf8_is_ignored(scmp3_home: Path) -> None:
    cache = scmp3_home / "cache" / "github" / "denoland" / "deno.json"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'{"etag": "\xff"}')
    assert latest(FakeGitHub(ok())).tag == "v2.9.7"


def test_a_cache_that_cant_be_written_still_returns_the_release(scmp3_home: Path) -> None:
    # A file where the cache folder should be.
    (scmp3_home / "cache").mkdir()
    (scmp3_home / "cache" / "github").write_text("")
    assert latest(FakeGitHub(ok())).tag == "v2.9.7"


# The rate limit


def test_the_last_request_allowed_is_followed_by_none_until_the_reset() -> None:
    clock = Clock()
    web = FakeGitHub(ok(**used_up()), ok())
    first = latest(web, clock)
    clock.now = NOW + 599
    assert latest(web, clock) == first
    assert len(web.requests) == 1
    clock.now = NOW + 600
    latest(web, clock)
    assert len(web.requests) == 2


def test_a_rate_limited_answer_falls_back_to_the_cached_release() -> None:
    clock = Clock()
    web = FakeGitHub(ok(), limited(**used_up()))
    first = latest(web, clock)
    assert latest(web, clock) == first
    # And GitHub isn't asked again before the reset.
    clock.now = NOW + 300
    assert latest(web, clock) == first
    assert len(web.requests) == 2


def test_rate_limited_with_nothing_cached_raises_rate_limited() -> None:
    clock = Clock()
    web = FakeGitHub(limited(**used_up()))
    assert raised_kind(web, clock) is ErrorKind.rate_limited
    assert raised_kind(web, clock) is ErrorKind.rate_limited
    assert len(web.requests) == 1


@pytest.mark.parametrize(
    ("response", "wait"),
    [
        (limited(403, **{"Retry-After": "30"}), 30),
        (limited(429, **{"Retry-After": "30"}), 30),
        (limited(429), 60),
        (limited(429, **{"Retry-After": "nan"}), 60),
        (limited(403, **{"X-RateLimit-Remaining": "0"}), 60),
        # GitHub's limit resets hourly, so a later reset means our clock is behind GitHub's.
        (limited(403, **used_up(str(int(NOW + 1e6)))), 3600),
        (limited(403, **{"Retry-After": "86400"}), 3600),
        # A secondary rate limit can come with no headers at all, only its message.
        (FakeResponse(b'{"message": "You have exceeded a secondary rate limit."}', 403), 60),
    ],
    ids=[
        "retry-after on 403",
        "retry-after on 429",
        "bare 429",
        "retry-after not a number",
        "no reset time",
        "far reset",
        "a day's retry-after",
        "secondary limit message",
    ],
)
def test_each_kind_of_rate_limit_is_waited_out(response: FakeResponse, wait: int) -> None:
    clock = Clock()
    web = FakeGitHub(response, ok())
    assert raised_kind(web, clock) is ErrorKind.rate_limited
    clock.now = NOW + wait - 1
    assert raised_kind(web, clock) is ErrorKind.rate_limited
    clock.now = NOW + wait
    assert latest(web, clock).tag == "v2.9.7"
    assert len(web.requests) == 2


def test_a_wait_saved_while_the_clock_was_fast_ends_once_it_is_set_right() -> None:
    clock = Clock(NOW + 30 * 86400)
    web = FakeGitHub(limited(429, **{"Retry-After": "60"}), ok())
    assert raised_kind(web, clock) is ErrorKind.rate_limited
    clock.now = NOW
    assert latest(web, clock).tag == "v2.9.7"


@pytest.mark.parametrize("limited_until", ["Infinity", "NaN", str(NOW + 7200)])
def test_a_cached_wait_that_cant_be_real_is_ignored(scmp3_home: Path, limited_until: str) -> None:
    cache = scmp3_home / "cache" / "github" / "denoland" / "deno.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(f'{{"etag": null, "release": null, "limited_until": {limited_until}}}')
    assert latest(FakeGitHub(ok())).tag == "v2.9.7"


def test_a_reset_already_past_by_our_clock_doesnt_stop_the_next_request() -> None:
    web = FakeGitHub(limited(**used_up(str(int(NOW) - 60))), ok())
    assert raised_kind(web) is ErrorKind.rate_limited
    assert latest(web).tag == "v2.9.7"


# Errors


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (403, ErrorKind.unavailable),
        (404, ErrorKind.unavailable),
        (500, ErrorKind.service_down),
        (503, ErrorKind.service_down),
    ],
)
def test_error_statuses_become_error_kinds(status: int, kind: ErrorKind) -> None:
    assert raised_kind(FakeGitHub(FakeResponse(b"{}", status))) is kind


def test_an_error_status_doesnt_stop_the_next_request() -> None:
    web = FakeGitHub(FakeResponse(status=502), ok())
    assert raised_kind(web) is ErrorKind.service_down
    assert latest(web).tag == "v2.9.7"


def test_no_connection_is_offline() -> None:
    def no_connection(url: str, headers: Mapping[str, str]) -> Any:
        raise urllib.error.URLError("getaddrinfo failed")

    with pytest.raises(ScmpError) as raised:
        github.github_latest(REPO, opener=no_connection)
    assert raised.value.kind is ErrorKind.offline


@pytest.mark.parametrize(
    "body",
    [
        b"<html>Unicorn!</html>",
        b"[]",
        b'{"assets": []}',
        b'{"tag_name": "v1", "assets": [{"name": "deno.zip"}]}',
        b'{"tag_name": "v1", "assets": [{"name": 5, "browser_download_url": "x"}]}',
        b'{"tag_name": "v1/../..", "assets": []}',
        b'{"tag_name": "..", "assets": []}',
    ],
    ids=["html", "a list", "no tag", "no url", "a number", "a path tag", "a dot tag"],
)
def test_an_answer_that_isnt_a_usable_release_is_service_down(body: bytes) -> None:
    assert raised_kind(FakeGitHub(FakeResponse(body))) is ErrorKind.service_down


@pytest.mark.parametrize("repo", ["deno", "../deno/x", "denoland/deno/extra", "./deno"])
def test_only_owner_slash_name_repos_are_asked_for(repo: str) -> None:
    web = FakeGitHub()
    with pytest.raises(ScmpError) as raised:
        github.github_latest(repo, opener=web)
    assert raised.value.kind is ErrorKind.internal
    assert web.requests == []


# Release files


def test_download_urls_point_at_the_release_file() -> None:
    url = github.download_url(REPO, "v2.9.7", "deno-x86_64-pc-windows-msvc.zip")
    assert url == f"{DOWNLOADS}/v2.9.7/deno-x86_64-pc-windows-msvc.zip"
