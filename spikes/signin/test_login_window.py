"""Tests for the sign-in watcher's cookie handling, with fake jars shaped like pywebview's."""

import sys
from http.cookies import SimpleCookie

import pytest

if sys.platform != "win32":
    pytest.skip("the watcher loads WebView2 through pythonnet", allow_module_level=True)

from login_window import (
    SigninWatcher,
    describe_cookie,
    describe_error_body,
    find_cookie,
    is_flow_url,
    is_signin_failure,
)
from webview.util import create_cookie

FAKE_TOKEN = "2-123456-987654321-AbCdEfGhIjKlMn"
EXPIRES = "Mon, 18 Oct 2027 12:00:00 GMT"


def cookie(name: str, value: str, domain: str = ".soundcloud.com", httponly: bool = False):
    """Build one cookie the way pywebview's WebView2 backend does."""
    return create_cookie(
        {
            "name": name,
            "value": value,
            "path": "/",
            "domain": domain,
            "expires": EXPIRES,
            "secure": True,
            "httponly": httponly,
            "samesite": None,
        }
    )


class FakeWindow:
    """Stands in for a pywebview window that already holds some cookies."""

    def __init__(self, cookies: list[SimpleCookie]) -> None:
        self.cookies = cookies

    def get_cookies(self) -> list[SimpleCookie]:
        return self.cookies


def test_find_cookie_returns_the_soundcloud_oauth_token() -> None:
    jar = [cookie("sc_anonymous_id", "x"), cookie("oauth_token", FAKE_TOKEN)]

    found = find_cookie(jar, "oauth_token")

    assert found is not None
    assert found.value == FAKE_TOKEN


def test_find_cookie_ignores_the_same_name_on_other_domains() -> None:
    assert find_cookie([cookie("oauth_token", FAKE_TOKEN, ".example.com")], "oauth_token") is None


def test_describe_cookie_keeps_attributes_but_not_the_value() -> None:
    facts = describe_cookie(
        cookie("oauth_refresh_token", FAKE_TOKEN, httponly=True)["oauth_refresh_token"]
    )

    assert facts.domain == ".soundcloud.com"
    assert facts.httponly is True
    assert facts.expires == EXPIRES
    assert FAKE_TOKEN not in repr(facts)


def test_watcher_records_the_token_and_keeps_it_out_of_repr() -> None:
    window = FakeWindow([cookie("sc_anonymous_id", "x"), cookie("oauth_token", FAKE_TOKEN)])
    watcher = SigninWatcher(window, timeout=10)

    watcher.check_cookies("poll")

    assert watcher.outcome.token == FAKE_TOKEN
    assert watcher.outcome.token_seen_by == "poll"
    assert [c.name for c in watcher.outcome.cookies_at_token] == ["sc_anonymous_id", "oauth_token"]
    assert FAKE_TOKEN not in repr(watcher.outcome)
    assert not any(FAKE_TOKEN in entry for entry in watcher.outcome.timeline)


def test_watcher_also_records_the_refresh_cookie_without_its_value() -> None:
    jar = [
        cookie("oauth_refresh_token", "refresh-value-32-characters-long", httponly=True),
        cookie("oauth_token", FAKE_TOKEN),
    ]
    watcher = SigninWatcher(FakeWindow(jar), timeout=10)

    watcher.check_cookies("poll")

    assert watcher.outcome.refresh_token == "refresh-value-32-characters-long"
    assert watcher.outcome.refresh_cookie is not None
    assert watcher.outcome.refresh_cookie.httponly is True
    assert "refresh-value-32-characters-long" not in repr(watcher.outcome)


def test_watcher_keeps_polling_while_there_is_no_token() -> None:
    watcher = SigninWatcher(FakeWindow([cookie("sc_anonymous_id", "x")]), timeout=10)

    watcher.check_cookies("poll")
    watcher.check_cookies("loaded")

    assert watcher.outcome.token is None
    assert watcher.outcome.polls == 2


def test_is_flow_url_keeps_sign_in_requests_and_drops_assets_and_ads() -> None:
    assert is_flow_url("https://api-auth.soundcloud.com/sign-in?client_id=x")
    assert not is_flow_url("https://soundcloud.com/versions.json")
    assert not is_flow_url("https://www.googletagmanager.com/gtm.js")


def test_is_flow_url_keeps_captcha_challenges() -> None:
    assert is_flow_url("https://www.google.com/recaptcha/api2/bframe?k=x")
    assert is_flow_url("https://geo.captcha-delivery.com/captcha/")


def test_is_signin_failure_only_picks_rejected_sign_ins() -> None:
    assert is_signin_failure("https://api-auth.soundcloud.com/sign-in?client_id=x", 401)
    # A successful sign-in's body carries the token, so it must never be read.
    assert not is_signin_failure("https://api-auth.soundcloud.com/sign-in?client_id=x", 200)
    assert not is_signin_failure("https://api-auth.soundcloud.com/oauth/authorize", 400)


def test_describe_error_body_reports_error_fields() -> None:
    summary = describe_error_body('{"error": "invalid_credentials", "user_id": 12345}')

    assert summary == '{"error": "invalid_credentials"}'


def test_describe_error_body_masks_emails_in_html_bodies() -> None:
    summary = describe_error_body("<html>blocked: someone@example.com</html>")

    assert "someone@example.com" not in summary
    assert summary.startswith("non-JSON body")


def test_describe_error_body_redacts_before_cutting_the_text() -> None:
    # Put an email right across the 160-character cut.
    body = "x" * 150 + " some.person@example.com and more"

    summary = describe_error_body(body)

    assert "some.person" not in summary


def test_describe_error_body_handles_an_empty_body() -> None:
    assert describe_error_body(None) == "empty body"
