"""Tests for picking the two sign-in cookies and for the copies that delete SoundCloud's cookies."""

from typing import Any

import pytest

from scmp3.app.signin_cookies import (
    SignInCookies,
    cookie_list,
    expired_copy,
    is_soundcloud,
    pick_cookies,
    soundcloud_cookies,
)
from scmp3.core.log import redact

ACCESS = "eyJhbGciOiJSUzI1NiJ9.eyJtYWRlIjoidXAifQ.made-up-signature"
REFRESH = "MadeUpRefreshTokenValue123456789"


def cookie(name: str, value: str, domain: str = "soundcloud.com", **more: Any) -> dict[str, Any]:
    return {"name": name, "value": value, "domain": domain, "path": "/", **more}


SIGNED_IN = [
    cookie("sc_anonymous_id", "something else"),
    cookie("oauth_refresh_token", REFRESH, httpOnly=True),
    cookie("oauth_token", ACCESS),
    cookie("SID", "a Google session", ".google.com"),
]


# Picking the cookies


def test_both_sign_in_cookies_are_picked_and_nothing_else() -> None:
    assert pick_cookies(SIGNED_IN) == SignInCookies(ACCESS, REFRESH)


def test_cookies_for_another_site_dont_count() -> None:
    cookies = [
        cookie("oauth_token", ACCESS, "evil.example"),
        cookie("oauth_refresh_token", REFRESH),
    ]
    assert pick_cookies(cookies) is None


def test_a_domain_cookie_for_soundcloud_counts_too() -> None:
    cookies = [
        cookie("oauth_token", ACCESS, ".soundcloud.com"),
        cookie("oauth_refresh_token", REFRESH),
    ]
    assert pick_cookies(cookies) == SignInCookies(ACCESS, REFRESH)


@pytest.mark.parametrize("answer", [[], None, "nonsense", [cookie("oauth_token", ACCESS)]])
def test_no_sign_in_when_a_cookie_is_missing(answer: Any) -> None:
    assert pick_cookies(cookie_list(answer)) is None


def test_entries_that_arent_cookies_are_skipped() -> None:
    assert cookie_list(["nonsense", 3, *SIGNED_IN]) == SIGNED_IN


def test_picked_values_are_masked_in_logs() -> None:
    pick_cookies(SIGNED_IN)
    assert REFRESH not in redact(f"left over: {REFRESH}")


def test_the_cookies_stay_out_of_repr() -> None:
    assert ACCESS not in repr(SignInCookies(ACCESS, REFRESH))


# SoundCloud's cookies


@pytest.mark.parametrize(
    "domain",
    ["soundcloud.com", ".soundcloud.com", "secure.soundcloud.com", "API-v2.SoundCloud.com"],
)
def test_soundcloud_and_its_subdomains_are_soundclouds(domain: str) -> None:
    assert is_soundcloud(cookie("x", "y", domain))


@pytest.mark.parametrize(
    "domain", [".google.com", "accounts.google.com", "notsoundcloud.com", "soundcloud.com.evil", ""]
)
def test_other_sites_arent_soundclouds(domain: str) -> None:
    assert not is_soundcloud(cookie("x", "y", domain))


def test_only_soundclouds_cookies_are_picked_for_deleting() -> None:
    assert [item["name"] for item in soundcloud_cookies(SIGNED_IN)] == [
        "sc_anonymous_id",
        "oauth_refresh_token",
        "oauth_token",
    ]


def test_a_host_only_cookie_is_deleted_through_its_url() -> None:
    copy = expired_copy(cookie("oauth_refresh_token", REFRESH, httpOnly=True, secure=True))
    assert copy == {
        "name": "oauth_refresh_token",
        "value": "",
        "expires": 1,
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "url": "https://soundcloud.com/",
    }


def test_a_domain_cookie_is_deleted_through_its_domain() -> None:
    copy = expired_copy(cookie("sc_anonymous_id", "x", ".soundcloud.com", sameSite="None"))
    assert copy["domain"] == ".soundcloud.com"
    assert "url" not in copy
    assert copy["sameSite"] == "None"


def test_a_partitioned_cookie_keeps_its_partition_and_no_value() -> None:
    partition = {"topLevelSite": "https://soundcloud.com", "hasCrossSiteAncestor": False}
    copy = expired_copy(cookie("datadome", "a secret", partitionKey=partition, size=40))
    assert copy["partitionKey"] == partition
    assert copy["value"] == ""
    assert "size" not in copy
