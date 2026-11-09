"""Tests for the account calls and AccountState, with fake HTTP and the in-memory keyring."""

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from yt_dlp.extractor.soundcloud import SoundcloudBaseIE
from yt_dlp.networking.exceptions import TransportError

from scmp3.app.account_cache import AccountCache
from scmp3.app.account_check import CHECK_EVERY
from scmp3.app.account_state import AccountState
from scmp3.app.bridge import Api
from scmp3.app.context import AppContext
from scmp3.app.events import EventBus
from scmp3.app.signin import SignInWindow, WindowProblem, WindowRequests, WindowSignInFailed
from scmp3.app.signin_browsers import BROWSER_KINDS, Browser, BrowserKind, BrowserOptions, choose
from scmp3.app.signin_cookies import SignInCookies
from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.models import Account
from scmp3.core.settings import SettingsStore
from scmp3.core.token_store import TokenStore


def load_signin_fakes() -> ModuleType:
    """tests/fixtures/signin_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_signin_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "signin_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


fakes = load_signin_fakes()
NOW = fakes.NOW
HOUR = fakes.HOUR
KAIA = {
    "username": "Kaia",
    "permalink": "kaia-music",
    "avatar_url": "https://i1.sndcdn.com/avatars-000-abc-large.jpg",
    "tier": "go_plus",
}


@pytest.fixture(autouse=True)
def ytdlp_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A fresh dict for the headers yt-dlp's SoundCloud extractors share, put back afterwards."""
    headers: dict[str, str] = {}
    monkeypatch.setattr(SoundcloudBaseIE, "_HEADERS", headers)
    return headers


class Later:
    """Runs the account's work only when told to, as its own thread would, later."""

    def __init__(self) -> None:
        self.waiting: list[Callable[[], None]] = []

    def __call__(self, work: Callable[[], None]) -> None:
        self.waiting.append(work)

    def run_all(self) -> None:
        while self.waiting:
            self.waiting.pop(0)()


class Window:
    """A sign-in window that returns what it's given, or raises it."""

    def __init__(self, outcome: SignInCookies | Exception | None) -> None:
        self.outcome = outcome
        self.requests: WindowRequests | None = None
        self.browser: Browser | None = None

    def __call__(self, requests: WindowRequests, browser: Browser) -> SignInCookies | None:
        self.requests = requests
        self.browser = browser
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def kind(browser_id: str) -> BrowserKind:
    return next(kind for kind in BROWSER_KINDS if kind.id == browser_id)


EDGE = Browser(kind("edge"), Path("msedge.exe"))
BRAVE = Browser(kind("brave"), Path("brave.exe"))
# What the view says about the browsers that found() finds.
FOUND_VIEW = {
    "window_browser": {"id": "brave", "name": "Brave"},
    "default_browser": {"id": "brave", "name": "Brave"},
    "browsers": [{"id": "edge", "name": "Edge"}, {"id": "brave", "name": "Brave"}],
    "window_closes_itself": True,
    "window_profile": False,
}


def found(setting: str) -> BrowserOptions:
    """Edge and Brave installed, with Brave the default browser."""
    options = BrowserOptions((EDGE, BRAVE), BRAVE)
    return BrowserOptions(options.installed, options.default, choose(setting, options))


@pytest.fixture
def events() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def ydl() -> Any:
    return fakes.FakeYdl()


@pytest.fixture
def cache() -> AccountCache:
    return AccountCache()


def account_with(
    events: list[dict[str, Any]],
    ydl: Any,
    *,
    run: Callable[[Callable[[], None]], None] = lambda work: work(),
    window: Window | None = None,
    clock: Callable[[], float] = fakes.clock,
    find: Callable[[str], BrowserOptions] | None = None,
    clean_up: Callable[[], None] = lambda: None,
    forget: Callable[[], bool] = lambda: True,
    has_profile: Callable[[], bool] = lambda: False,
    on_signed_in: Callable[[], None] = lambda: None,
    browser_setting: Callable[[], str] = lambda: "default",
) -> AccountState:
    sign_in_window = SignInWindow(
        open=window or Window(None),
        find=find or found,
        clean_up=clean_up,
        forget=forget,
        has_profile=has_profile,
        closes_by_itself=True,
    )
    return AccountState(
        events.append,
        make_ydl=ydl,
        make_store=lambda: TokenStore(clock=clock),
        window=sign_in_window,
        clock=clock,
        run=run,
        on_signed_in=on_signed_in,
        browser_setting=browser_setting,
    )


def api_with(account: AccountState) -> Api:
    context = AppContext(
        settings=SettingsStore(paths.config_dir() / "settings.json"),
        history=History(paths.data_dir() / "history.db"),
        events=EventBus(),
        make_account=lambda context: account,
    )
    return Api(context)


def signed_in_store() -> Any:
    return fakes.signed_in(TokenStore(clock=fakes.clock))


def last(events: list[dict[str, Any]]) -> dict[str, Any]:
    assert events[-1]["type"] == "account"
    return events[-1]["account"]


# The snapshot and the startup check


def test_the_snapshot_says_signed_out_when_no_sign_in_is_stored(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    api = api_with(account_with(events, ydl))
    assert api.snapshot()["account"] == {
        "status": "signed_out",
        **FOUND_VIEW,
        "user": None,
        "stored_in": None,
        "running": None,
        "finishing": False,
        "problem": None,
    }
    assert ydl.requests == []


def test_a_sign_in_checked_today_is_shown_without_asking_soundcloud(
    events: list[dict[str, Any]], ydl: Any, cache: AccountCache
) -> None:
    signed_in_store()
    cache.save(Account(**KAIA), NOW - HOUR)
    view = api_with(account_with(events, ydl)).snapshot()["account"]
    assert view["status"] == "signed_in"
    assert view["user"] == KAIA
    assert view["stored_in"] == "keyring"
    assert ydl.requests == []


def test_a_sign_in_last_checked_yesterday_is_checked_with_soundcloud(
    events: list[dict[str, Any]], ydl: Any, cache: AccountCache
) -> None:
    signed_in_store()
    cache.save(Account(**{**KAIA, "tier": "free"}), NOW - CHECK_EVERY)
    ydl.answer(fakes.me_answer("consumer-high-tier"))
    api_with(account_with(events, ydl)).snapshot()
    assert [request.url for request in ydl.requests] == [fakes.ME_URL]
    assert last(events)["user"] == KAIA
    saved = cache.load()
    assert saved is not None and saved.checked_at == NOW


def test_the_startup_check_runs_once_however_often_the_snapshot_is_asked_for(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    signed_in_store()
    ydl.answer(fakes.me_answer())
    api = api_with(account_with(events, ydl))
    api.snapshot()
    api.snapshot()
    assert len(ydl.requests) == 1


def test_a_stored_token_that_me_refuses_is_renewed_once(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    tokens = signed_in_store()
    new_access = fakes.make_access()
    ydl.answer(
        fakes.status_error(401),
        fakes.token_answer(new_access, fakes.make_refresh()),
        fakes.me_answer(),
    )
    api_with(account_with(events, ydl)).snapshot()
    assert last(events)["status"] == "signed_in"
    stored = TokenStore().load()
    assert stored is not None and stored.access == new_access != tokens.access


def test_an_expired_token_is_renewed_at_startup_even_when_checked_today(
    events: list[dict[str, Any]], ydl: Any, cache: AccountCache
) -> None:
    fakes.signed_in(TokenStore(clock=fakes.clock), exp=NOW - HOUR)
    cache.save(Account(**KAIA), NOW - HOUR)
    ydl.answer(fakes.oauth_error("invalid_grant"))
    api_with(account_with(events, ydl)).snapshot()
    # So a sign-in that ended shows before any job finds out.
    assert last(events)["status"] == "ended"


def test_a_sign_in_soundcloud_ended_shows_as_ended(events: list[dict[str, Any]], ydl: Any) -> None:
    fakes.signed_in(TokenStore(clock=fakes.clock), exp=NOW - HOUR)
    ydl.answer(fakes.oauth_error("invalid_grant"))
    api_with(account_with(events, ydl)).snapshot()
    assert last(events)["status"] == "ended"
    assert TokenStore().load() is None


def test_a_check_that_cant_reach_soundcloud_leaves_the_account_as_it_was(
    events: list[dict[str, Any]], ydl: Any, cache: AccountCache
) -> None:
    signed_in_store()
    cache.save(Account(**KAIA), NOW - 2 * CHECK_EVERY)
    ydl.answer(TransportError(msg="timed out"))
    view = api_with(account_with(events, ydl)).snapshot()["account"]
    assert view["status"] == "signed_in" and view["user"] == KAIA
    assert events == []


def test_a_check_that_ends_after_a_sign_out_is_dropped(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    signed_in_store()
    later = Later()
    account = account_with(events, ydl, run=later)
    account.load_once()
    check = later.waiting.pop(0)
    account.sign_out()
    later.run_all()
    ydl.answer(fakes.me_answer())
    check()
    assert account.view()["status"] == "signed_out"


def test_an_account_file_left_without_a_sign_in_is_deleted(
    events: list[dict[str, Any]], ydl: Any, cache: AccountCache
) -> None:
    cache.save(Account(**KAIA), NOW)
    api_with(account_with(events, ydl)).snapshot()
    assert cache.load() is None


# Copy from your browser


def test_signing_in_with_copied_values_stores_them_and_shows_the_account(
    events: list[dict[str, Any]], ydl: Any, cache: AccountCache, fake_keyring: Any
) -> None:
    tokens = fakes.make_tokens()
    ydl.answer(fakes.me_answer("consumer-high-tier"))
    api = api_with(account_with(events, ydl))
    assert api.sign_in_from_browser(f" {tokens.access} ", tokens.refresh) == {}
    assert [event["account"]["running"] for event in events] == ["browser", None]
    view = last(events)
    assert view["status"] == "signed_in"
    assert view["user"] == KAIA
    assert view["stored_in"] == "keyring"
    assert len(fake_keyring.entries) == 2
    assert cache.load() is not None


@pytest.mark.parametrize(
    ("access", "refresh", "field", "reason"),
    [
        ("", "r" * 32, "access", "access_empty"),
        ("a" * 32, "r" * 32, "access", "access_is_refresh"),
        ("not a token", "r" * 32, "access", "access_not_token"),
        (None, "", "refresh", "refresh_empty"),
        (None, None, "refresh", "refresh_is_access"),
        (None, "has space; and more", "refresh", "refresh_not_token"),
    ],
)
def test_a_value_in_the_wrong_field_says_which_field_and_why(
    events: list[dict[str, Any]],
    ydl: Any,
    access: str | None,
    refresh: str | None,
    field: str,
    reason: str,
) -> None:
    jwt = fakes.make_access()
    api = api_with(account_with(events, ydl))
    api.sign_in_from_browser(jwt if access is None else access, jwt if refresh is None else refresh)
    problem = last(events)["problem"]
    assert problem == {
        "id": 1,
        "task": "browser",
        "kind": "refused",
        "field": field,
        "reason": reason,
    }
    assert last(events)["status"] == "signed_out"
    assert ydl.requests == []


def test_values_soundcloud_refuses_are_an_auth_problem(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    tokens = fakes.make_tokens()
    ydl.answer(fakes.status_error(401))
    api_with(account_with(events, ydl)).sign_in_from_browser(tokens.access, tokens.refresh)
    assert last(events)["problem"]["kind"] == "auth_expired"
    assert TokenStore().load() is None


def test_a_sign_in_that_cant_reach_soundcloud_is_an_offline_problem(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    tokens = fakes.make_tokens()
    ydl.answer(TransportError(msg="timed out"))
    api_with(account_with(events, ydl)).sign_in_from_browser(tokens.access, tokens.refresh)
    assert last(events)["problem"]["kind"] == "offline"


def test_each_problem_has_a_new_id(events: list[dict[str, Any]], ydl: Any) -> None:
    api = api_with(account_with(events, ydl))
    api.sign_in_from_browser("", "")
    api.sign_in_from_browser("", "")
    assert last(events)["problem"]["id"] == 2


def test_a_call_without_two_strings_answers_internal(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    api = api_with(account_with(events, ydl))
    assert api.sign_in_from_browser(None, "x") == {"error": "internal"}  # type: ignore[arg-type]
    assert events == []


def test_no_event_holds_a_token(events: list[dict[str, Any]], ydl: Any) -> None:
    tokens = fakes.make_tokens()
    ydl.answer(fakes.me_answer())
    api_with(account_with(events, ydl)).sign_in_from_browser(tokens.access, tokens.refresh)
    text = json.dumps(events)
    for value in (tokens.access, tokens.refresh, tokens.client_id):
        assert value not in text


def test_a_second_sign_in_while_one_runs_is_ignored(events: list[dict[str, Any]], ydl: Any) -> None:
    later = Later()
    account = account_with(events, ydl, run=later)
    tokens = fakes.make_tokens()
    account.sign_in_from_browser(tokens.access, tokens.refresh)
    account.sign_out()
    assert len(later.waiting) == 1
    assert account.view()["running"] == "browser"


# The sign-in window


def test_the_window_signs_in_with_the_cookies_it_read(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    tokens = fakes.make_tokens()
    window = Window(SignInCookies(tokens.access, tokens.refresh))
    ydl.answer(fakes.me_answer())
    assert api_with(account_with(events, ydl, window=window)).sign_in_window() == {}
    assert [event["account"]["running"] for event in events] == ["window", None]
    assert last(events)["status"] == "signed_in"


def test_each_sign_in_that_works_lets_the_waiting_jobs_carry_on(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    tokens = fakes.make_tokens()
    signed_in: list[str] = []
    window = Window(SignInCookies(tokens.access, tokens.refresh))
    account = account_with(
        events, ydl, window=window, on_signed_in=lambda: signed_in.append(last(events)["status"])
    )
    ydl.answer(fakes.me_answer())
    account.sign_in_window()
    # The account shows as signed in before the jobs carry on.
    assert signed_in == ["signed_in"]
    ydl.answer(fakes.me_answer())
    account.sign_in_from_browser(tokens.access, tokens.refresh)
    assert signed_in == ["signed_in", "signed_in"]


def test_a_sign_in_that_fails_or_is_cancelled_retries_no_jobs(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    signed_in: list[str] = []
    account_with(
        events, ydl, window=Window(None), on_signed_in=lambda: signed_in.append("x")
    ).sign_in_window()
    account_with(events, ydl, on_signed_in=lambda: signed_in.append("x")).sign_in_from_browser(
        "not a token", "r" * 32
    )
    assert signed_in == []


def test_closing_the_window_changes_nothing(events: list[dict[str, Any]], ydl: Any) -> None:
    api_with(account_with(events, ydl, window=Window(None))).sign_in_window()
    assert last(events) == {
        "status": "signed_out",
        **FOUND_VIEW,
        "user": None,
        "stored_in": None,
        "running": None,
        "finishing": False,
        "problem": None,
    }


def test_a_window_that_fails_is_a_problem(events: list[dict[str, Any]], ydl: Any) -> None:
    window = Window(ScmpError(ErrorKind.offline, "the page didn't load"))
    api_with(account_with(events, ydl, window=window)).sign_in_window()
    assert last(events)["problem"]["task"] == "window"
    assert last(events)["problem"]["kind"] == "offline"


@pytest.mark.parametrize("problem", list(WindowProblem))
def test_a_window_that_gives_no_sign_in_says_why(
    events: list[dict[str, Any]], ydl: Any, problem: WindowProblem
) -> None:
    window = Window(WindowSignInFailed(problem))
    api_with(account_with(events, ydl, window=window)).sign_in_window()
    assert last(events)["problem"] == {
        "id": 1,
        "task": "window",
        "kind": problem.value,
        "field": None,
        "reason": None,
    }


def test_cancel_reaches_the_window_while_its_open(events: list[dict[str, Any]], ydl: Any) -> None:
    seen: list[bool] = []
    api: Api | None = None

    def window(requests: WindowRequests, browser: Browser) -> None:
        # The person selects Cancel while the Edge window is open.
        assert api is not None
        assert api.cancel_sign_in() == {}
        seen.append(requests.cancel.is_set())

    api = api_with(account_with(events, ydl, window=window))  # type: ignore[arg-type]
    api.sign_in_window()
    assert seen == [True]
    # A cancelled window is no problem, and nothing changes.
    assert last(events)["problem"] is None and last(events)["running"] is None


def test_ive_signed_in_reaches_the_window_and_shows_as_finishing(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    seen: list[bool] = []
    api: Api | None = None

    def window(requests: WindowRequests, browser: Browser) -> None:
        assert api is not None
        assert api.finish_sign_in() == {}
        # A second click while it closes changes nothing.
        api.finish_sign_in()
        seen.append(requests.finish.is_set())
        seen.append(last(events)["finishing"])

    api = api_with(account_with(events, ydl, window=window))  # type: ignore[arg-type]
    api.sign_in_window()
    assert seen == [True, True]
    assert last(events)["finishing"] is False
    assert sum(event["account"]["finishing"] for event in events) == 1


def test_a_window_closing_by_itself_shows_as_finishing_until_it_opens_again(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    seen: list[bool] = []

    def window(requests: WindowRequests, browser: Browser) -> None:
        requests.on_reading(True)
        seen.append(last(events)["finishing"])
        # The read found no sign-in, so the window opens again and waits as before.
        requests.on_reading(False)
        seen.append(last(events)["finishing"])

    account_with(events, ydl, window=window).sign_in_window()  # type: ignore[arg-type]
    assert seen == [True, False]
    assert last(events)["running"] is None


def test_a_window_reading_after_it_ended_changes_nothing(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    kept: list[WindowRequests] = []

    def window(requests: WindowRequests, browser: Browser) -> None:
        kept.append(requests)

    account = account_with(events, ydl, window=window)  # type: ignore[arg-type]
    account.sign_in_window()
    sent = len(events)
    kept[0].on_reading(True)
    assert len(events) == sent
    assert account.view()["finishing"] is False


def test_ive_signed_in_does_nothing_without_a_window(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    account_with(events, ydl).finish_sign_in()
    assert events == []


def test_without_a_browser_the_view_names_none_and_the_window_says_so(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    window = Window(None)
    account = account_with(events, ydl, window=window, find=lambda setting: BrowserOptions())
    view = api_with(account).snapshot()["account"]
    assert view["window_browser"] is None
    assert view["browsers"] == []
    account.sign_in_window()
    assert last(events)["problem"]["kind"] == "no_browser"
    assert window.requests is None


def test_the_window_opens_in_the_browser_picked_in_settings(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    window = Window(None)
    setting = ["default"]
    account = account_with(events, ydl, window=window, browser_setting=lambda: setting[0])
    account.sign_in_window()
    assert window.browser == BRAVE
    setting[0] = "edge"
    account.refresh_browsers()
    assert last(events)["window_browser"] == {"id": "edge", "name": "Edge"}
    account.sign_in_window()
    assert window.browser == EDGE


def test_looking_for_browsers_again_sends_nothing_when_nothing_changed(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    account = account_with(events, ydl)
    account.refresh_browsers()
    sent = len(events)
    account.refresh_browsers()
    assert len(events) == sent


def test_profiles_a_sign_out_left_behind_are_cleaned_up_at_startup(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    cleaned: list[bool] = []
    api = api_with(account_with(events, ydl, clean_up=lambda: cleaned.append(True)))
    api.snapshot()
    api.snapshot()
    assert cleaned == [True]


# Signing out and an ended sign-in


def test_signing_out_deletes_both_tokens_and_the_account_file(
    events: list[dict[str, Any]], ydl: Any, cache: AccountCache, fake_keyring: Any
) -> None:
    signed_in_store()
    cache.save(Account(**KAIA), NOW)
    api = api_with(account_with(events, ydl))
    api.snapshot()
    assert api.sign_out() == {}
    assert fake_keyring.entries == {}
    assert cache.load() is None
    assert last(events)["status"] == "signed_out"
    assert last(events)["user"] is None


def test_signing_out_deletes_the_windows_kept_profiles(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    signed_in_store()
    forgotten: list[bool] = []

    def forget() -> bool:
        forgotten.append(True)
        return True

    account_with(events, ydl, forget=forget).sign_out()
    assert forgotten == [True]


def test_a_sign_out_that_fails_keeps_the_windows_profiles(
    events: list[dict[str, Any]], ydl: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scmp3.core import auth

    def refuse(**options: Any) -> None:
        raise ScmpError(ErrorKind.disk, "the keyring refused")

    monkeypatch.setattr(auth, "sign_out", refuse)
    forgotten: list[bool] = []

    def forget() -> bool:
        forgotten.append(True)
        return True

    account_with(events, ydl, forget=forget).sign_out()
    assert forgotten == []
    assert last(events)["problem"]["task"] == "sign_out"


def test_a_profile_that_cant_be_deleted_signs_out_and_says_so(
    events: list[dict[str, Any]], ydl: Any, fake_keyring: Any
) -> None:
    signed_in_store()

    def fail() -> bool:
        raise OSError("the profile is in use")

    account_with(events, ydl, forget=fail).sign_out()
    assert fake_keyring.entries == {}
    assert last(events)["status"] == "signed_out"
    assert last(events)["problem"]["kind"] == "profile_left"


def test_a_profile_that_stays_under_its_kept_name_says_so(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    signed_in_store()
    account_with(events, ydl, forget=lambda: False, has_profile=lambda: True).sign_out()
    view = last(events)
    assert view["status"] == "signed_out"
    assert view["problem"]["task"] == "sign_out" and view["problem"]["kind"] == "profile_left"
    # So Sign out is still offered.
    assert view["window_profile"] is True


def test_a_job_that_finds_the_sign_in_ended_shows_it_ended(
    events: list[dict[str, Any]], ydl: Any, cache: AccountCache
) -> None:
    signed_in_store()
    cache.save(Account(**KAIA), NOW)
    account = account_with(events, ydl)
    account.load_once()
    account.sign_in_ended()
    assert last(events)["status"] == "ended"
    assert last(events)["user"] == KAIA


def test_signing_in_again_after_it_ended_works(events: list[dict[str, Any]], ydl: Any) -> None:
    signed_in_store()
    account = account_with(events, ydl)
    ydl.answer(fakes.me_answer())
    account.load_once()
    account.sign_in_ended()
    tokens = fakes.make_tokens()
    ydl.answer(fakes.me_answer())
    account.sign_in_from_browser(tokens.access, tokens.refresh)
    assert last(events)["status"] == "signed_in"


def test_an_ended_sign_in_isnt_reported_while_signed_out(
    events: list[dict[str, Any]], ydl: Any
) -> None:
    account_with(events, ydl).sign_in_ended()
    assert events == []


# How jobs reach the account


def test_a_job_that_finds_the_sign_in_ended_reaches_the_account(
    events: list[dict[str, Any]], ydl: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scmp3.app import context as context_module
    from scmp3.app import jobs as jobs_module

    handed: dict[str, Any] = {}

    class RecordingSignIn:
        def __init__(self, *, on_ended: Callable[[], None]) -> None:
            handed["on_ended"] = on_ended

    class RecordingSteps:
        def __init__(self, tools: Any, sign_in: Any) -> None:
            handed["steps_sign_in"] = sign_in

    monkeypatch.setattr(context_module, "JobSignIn", RecordingSignIn)
    monkeypatch.setattr(jobs_module, "PipelineSteps", RecordingSteps)
    signed_in_store()
    account = account_with(events, ydl)
    ydl.answer(fakes.me_answer())
    AppContext(
        settings=SettingsStore(paths.config_dir() / "settings.json"),
        history=History(paths.data_dir() / "history.db"),
        events=EventBus(),
        make_account=lambda context: account,
    )
    account.load_once()
    assert isinstance(handed["steps_sign_in"], RecordingSignIn)
    handed["on_ended"]()
    assert account.view()["status"] == "ended"
