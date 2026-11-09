"""Tests for AccountState closing with sc-mp3, cancelling during a read, and Sign out when nothing
shows as signed in. The sign-in calls themselves are in test_bridge_account.py."""

import importlib.util
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.app import account_state
from scmp3.app.account_state import AccountState, run_in_thread
from scmp3.app.signin import SignInWindow, WindowRequests
from scmp3.app.signin_browsers import BROWSER_KINDS, Browser, BrowserOptions
from scmp3.app.signin_cookies import SignInCookies
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
BRAVE = Browser(next(kind for kind in BROWSER_KINDS if kind.id == "brave"), Path("brave.exe"))
WaitingWindow = Callable[[WindowRequests, Browser], SignInCookies | None]


class Parts:
    """The account's window parts, recording what the account asks of them."""

    def __init__(self, open_window: WaitingWindow) -> None:
        self.stopped: list[WindowRequests] = []
        self.forgotten = 0
        self.profile_kept = True
        self.window = SignInWindow(
            open=open_window,
            find=lambda setting: BrowserOptions((BRAVE,), BRAVE, BRAVE),
            clean_up=lambda: None,
            forget=self.forget,
            has_profile=lambda: self.profile_kept,
            stop=self.stopped.append,
            closes_by_itself=True,
        )

    def forget(self) -> bool:
        self.forgotten += 1
        self.profile_kept = False
        return True


def account_with(
    events: list[dict[str, Any]],
    parts: Parts,
    ydl: Any = None,
    run: Callable[[Callable[[], None]], None] = lambda work: work(),
) -> AccountState:
    return AccountState(
        events.append,
        make_ydl=ydl or fakes.FakeYdl(),
        make_store=lambda: TokenStore(clock=fakes.clock),
        window=parts.window,
        clock=fakes.clock,
        run=run,
    )


def last(events: list[dict[str, Any]]) -> dict[str, Any]:
    return events[-1]["account"]


def waits_for_cancel(opened: threading.Event) -> WaitingWindow:
    """A window that stays open until it's cancelled, as a person who hasn't signed in yet."""

    def window(requests: WindowRequests, browser: Browser) -> None:
        opened.set()
        assert requests.cancel.wait(10)

    return window


def test_closing_sc_mp3_cancels_an_open_window_and_waits_for_it() -> None:
    events: list[dict[str, Any]] = []
    opened = threading.Event()
    parts = Parts(waits_for_cancel(opened))
    account = account_with(events, parts, run=run_in_thread)
    account.sign_in_window()
    assert opened.wait(5)
    account.shut_down()
    assert account.view()["running"] is None
    assert [requests.cancel.is_set() for requests in parts.stopped] == [True]


def test_a_window_that_wont_stop_in_time_has_what_it_started_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, Any]] = []
    release = threading.Event()
    opened = threading.Event()

    def stuck_window(requests: WindowRequests, browser: Browser) -> None:
        opened.set()
        release.wait(10)

    monkeypatch.setattr(account_state, "SHUTDOWN_WAIT", 0.1)
    parts = Parts(stuck_window)
    account = account_with(events, parts, run=run_in_thread)
    account.sign_in_window()
    assert opened.wait(5)
    try:
        account.shut_down()
        assert len(parts.stopped) == 1
    finally:
        release.set()


def test_closing_sc_mp3_with_no_window_open_stops_nothing() -> None:
    parts = Parts(lambda requests, browser: None)
    account_with([], parts).shut_down()
    assert parts.stopped == []


def test_cancel_just_after_the_read_signs_nothing_in() -> None:
    events: list[dict[str, Any]] = []
    tokens = fakes.make_tokens()

    def cancelled_as_it_returns(requests: WindowRequests, browser: Browser) -> SignInCookies:
        requests.cancel.set()
        return SignInCookies(tokens.access, tokens.refresh)

    account = account_with(events, Parts(cancelled_as_it_returns))
    account.sign_in_window()
    assert TokenStore().load() is None
    assert last(events)["status"] == "signed_out"
    assert last(events)["problem"] is None


def test_cookies_from_the_window_with_the_wrong_shape_are_the_windows_problem() -> None:
    events: list[dict[str, Any]] = []

    def odd_cookies(requests: WindowRequests, browser: Browser) -> SignInCookies:
        return SignInCookies("not a token", "r" * 32)

    account_with(events, Parts(odd_cookies)).sign_in_window()
    problem = last(events)["problem"]
    # The UI then says SoundCloud's sign-in looked different, not which box to paste in.
    assert problem["task"] == "window" and problem["kind"] == "refused"


def test_the_view_says_when_a_window_profile_is_kept() -> None:
    parts = Parts(lambda requests, browser: None)
    account = account_with([], parts)
    assert account.view()["window_profile"] is True
    parts.profile_kept = False
    assert account.view()["window_profile"] is False


def test_signing_out_while_signed_out_deletes_a_kept_profile() -> None:
    events: list[dict[str, Any]] = []
    parts = Parts(lambda requests, browser: None)
    account_with(events, parts).sign_out()
    assert parts.forgotten == 1
    assert last(events)["window_profile"] is False
    assert last(events)["problem"] is None


def test_signing_out_after_the_sign_in_ended_deletes_the_kept_tokens(fake_keyring: Any) -> None:
    events: list[dict[str, Any]] = []
    fakes.signed_in(TokenStore(clock=fakes.clock))
    parts = Parts(lambda requests, browser: None)
    ydl = fakes.FakeYdl(fakes.me_answer())
    account = account_with(events, parts, ydl)
    account.load_once()
    account.sign_in_ended()
    assert last(events)["status"] == "ended"
    account.sign_out()
    assert fake_keyring.entries == {}
    assert parts.forgotten == 1
    assert last(events)["status"] == "signed_out"


def test_cancel_while_soundcloud_checks_the_sign_in_signs_nothing_in(fake_keyring: Any) -> None:
    events: list[dict[str, Any]] = []
    tokens = fakes.make_tokens()
    kept: list[WindowRequests] = []
    signed_in: list[bool] = []

    def window(requests: WindowRequests, browser: Browser) -> SignInCookies:
        kept.append(requests)
        return SignInCookies(tokens.access, tokens.refresh)

    def person_cancels_while_me_answers(request: Any) -> bytes:
        kept[0].cancel.set()
        return fakes.me_answer()

    ydl = fakes.FakeYdl(person_cancels_while_me_answers)
    account = AccountState(
        events.append,
        make_ydl=ydl,
        make_store=lambda: TokenStore(clock=fakes.clock),
        window=Parts(window).window,
        clock=fakes.clock,
        run=lambda work: work(),
        on_signed_in=lambda: signed_in.append(True),
    )
    account.sign_in_window()
    assert TokenStore().load() is None
    assert fake_keyring.entries == {}
    assert last(events)["status"] == "signed_out"
    assert last(events)["problem"] is None
    assert signed_in == []
