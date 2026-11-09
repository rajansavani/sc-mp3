"""Tests for the sign-in window, its DevTools client and the browser's processes, all with fakes."""

import json
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from scmp3.app import browser_processes, browser_windows, chromium, signin, signin_read
from scmp3.app.devtools import BrowserDevTools
from scmp3.app.signin import WindowProblem, WindowRequests, WindowSignInFailed
from scmp3.app.signin_browsers import BROWSER_KINDS, Browser
from scmp3.app.signin_cookies import SignInCookies, is_soundcloud
from scmp3.core.errors import ScmpError

ACCESS = "eyJhbGciOiJSUzI1NiJ9.eyJtYWRlIjoidXAifQ.made-up-signature"
REFRESH = "MadeUpRefreshTokenValue123456789"
BRAVE = Browser(next(kind for kind in BROWSER_KINDS if kind.id == "brave"), Path("brave.exe"))
# The real checks, kept before any test swaps them for fakes.
REAL_PROFILE_IN_USE = chromium.profile_in_use
REAL_BROWSERS_ON = chromium.browsers_on
REAL_STOP_PROCESSES_ON = chromium.stop_processes_on
SIGN_IN_PAGE = "SoundCloud \N{EM DASH} Register, sign-in or access our Homepage - Brave"
SIGNED_IN_PAGE = "Settings on SoundCloud - Brave"


def cookie(name: str, value: str, domain: str = "soundcloud.com") -> dict[str, Any]:
    return {"name": name, "value": value, "domain": domain, "path": "/", "httpOnly": True}


SIGNED_IN = [
    cookie("sc_anonymous_id", "something else"),
    cookie("oauth_refresh_token", REFRESH),
    cookie("oauth_token", ACCESS),
    cookie("session", "the sign-in page's own", "secure.soundcloud.com"),
    cookie("SID", "a Google session", ".google.com"),
]
SIGNED_OUT = [cookie("sc_anonymous_id", "x"), cookie("SID", "a Google session", ".google.com")]


class FakeProcess:
    """A browser process that exits once `closes` is set, or when it's stopped."""

    next_pid = 1000

    def __init__(self, command: list[str]) -> None:
        FakeProcess.next_pid += 1
        self.pid = FakeProcess.next_pid
        self.command = command
        self.closes = threading.Event()
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.closes.is_set():
            self.returncode = 0
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self.closes.wait(timeout):
            raise subprocess.TimeoutExpired(self.command, timeout or 0)
        self.returncode = 0
        return 0


class FakeSocket:
    """The browser target: its cookies, which an expired copy deletes, as Chromium's do."""

    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self.cookies = list(cookies)
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.deletes = True
        self._answers: list[str] = []

    def send(self, message: str) -> None:
        request = json.loads(message)
        self.sent.append(request)
        if request["method"] == "Storage.getCookies":
            self._answers.append(json.dumps({"method": "Target.targetCreated", "params": {}}))
            self._answer(request, {"cookies": self.cookies})
        if request["method"] == "Storage.setCookies":
            if self.deletes:
                self._delete(request["params"]["cookies"])
            self._answer(request, {})

    def _answer(self, request: dict[str, Any], result: dict[str, Any]) -> None:
        self._answers.append(json.dumps({"id": request["id"], "result": result}))

    def _delete(self, copies: list[dict[str, Any]]) -> None:
        for copy in copies:
            assert copy["expires"] == 1 and copy["value"] == ""
            host = copy.get("domain") or copy["url"].split("/")[2]
            self.cookies = [
                item
                for item in self.cookies
                if (item["name"], item["domain"]) != (copy["name"], host)
            ]

    def recv(self, timeout: float | None = None) -> str:
        return self._answers.pop(0)

    def close(self) -> None:
        self.closed = True

    def soundcloud_names(self) -> list[str]:
        return [item["name"] for item in self.cookies if is_soundcloud(item)]


class FakeBrowser:
    """Stands in for the browser's processes, windows and profile, and records what was done."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cookies: Any) -> None:
        self.started: list[FakeProcess] = []
        self.stopped: list[int] = []
        self.profile = tmp_path / "brave"
        self.forgotten: list[Path] = []
        self.socket = FakeSocket(cookies)
        self.close_window_at_once = True
        self.port_failures = 0
        # The titles the window shows, one list per check, with the last one kept after that.
        self.titles: list[list[str]] = [[SIGN_IN_PAGE]]
        self.asked_to_close: list[int] = []
        # What the person does in each new window, before it's read.
        self.on_window: Callable[[], None] = lambda: None
        monkeypatch.setattr(chromium, "kept_profile", self.kept_profile)
        monkeypatch.setattr(chromium, "start", self.start)
        monkeypatch.setattr(chromium, "wait_for_port", self.wait_for_port)
        monkeypatch.setattr(chromium, "stop", self.stop)
        monkeypatch.setattr(chromium, "stop_processes_on", self.stop_processes_on)
        monkeypatch.setattr(chromium, "forget_profile", self.forget_profile)
        # False makes every listing of the profile's processes fail.
        self.can_list = True
        # The browsers still on the profile once the window's own process has exited.
        self.other_browsers: list[int] = []
        monkeypatch.setattr(chromium, "browsers_on", self.browsers_on)
        # A browser that a window was handed to holds the profile's lockfile.
        monkeypatch.setattr(chromium, "profile_in_use", lambda profile: bool(self.other_browsers))
        monkeypatch.setattr(browser_windows, "window_titles", self.window_titles)
        monkeypatch.setattr(browser_windows, "close_windows", self.close_windows)
        monkeypatch.setattr(signin_read, "open_socket", lambda port, path: self.socket)
        monkeypatch.setattr(signin, "CANCEL_POLL", 0)
        monkeypatch.setattr(signin, "LIST_EVERY", 0)

    def browsers_on(self, profile: Path) -> list[int] | None:
        return list(self.other_browsers) if self.can_list else None

    def stop_processes_on(self, profile: Path) -> bool:
        self.stopped.append(-1)
        return self.can_list

    def forget_profile(self, profile: Path) -> bool:
        self.forgotten.append(profile)
        return True

    def kept_profile(self, browser_id: str) -> Path:
        assert browser_id == "brave"
        self.profile.mkdir(exist_ok=True)
        return self.profile

    def start(self, browser: Path, profile: Path, url: str, flags: tuple[str, ...]) -> FakeProcess:
        process = FakeProcess([str(browser), f"--user-data-dir={profile}", *flags, url])
        self.started.append(process)
        if url == signin.START_URL:
            self.on_window()
            if self.close_window_at_once:
                process.closes.set()
        return process

    def windows(self) -> list[FakeProcess]:
        return [process for process in self.started if process.command[-1] == signin.START_URL]

    def window_titles(self, pids: list[int]) -> list[str]:
        return self.titles.pop(0) if len(self.titles) > 1 else self.titles[0]

    def close_windows(self, pid: int) -> int:
        # The browser closes as after its X, and saves its cookies on the way.
        self.asked_to_close.append(pid)
        for process in self.started:
            if process.pid == pid:
                process.closes.set()
        if pid in self.other_browsers:
            self.other_browsers.remove(pid)
        return 1

    def wait_for_port(self, process: FakeProcess, profile: Path) -> chromium.DevToolsPort:
        if self.port_failures:
            self.port_failures -= 1
            process.closes.set()
            raise ScmpError("internal", "the browser exited with 0 early")
        # Browser.close makes the headless browser exit.
        process.closes.set()
        return chromium.DevToolsPort(54321, "/devtools/browser/made-up")

    def stop(self, process: FakeProcess) -> None:
        self.stopped.append(process.pid)
        process.closes.set()

    def methods(self) -> list[str]:
        return [request["method"] for request in self.socket.sent]


# The two steps


def test_a_closed_window_with_a_sign_in_gives_both_cookies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    assert signin.open_sign_in_window(WindowRequests(), BRAVE) == SignInCookies(ACCESS, REFRESH)
    window, reader = fake.started
    # The window has no DevTools port and nothing connects to it, so Google allows the sign-in.
    assert window.command[2:] == [*chromium.BASE_FLAGS, "https://soundcloud.com/settings"]
    assert not any("remote-debugging" in part for part in window.command)
    assert reader.command[2:] == [*chromium.READ_FLAGS, "about:blank"]
    assert fake.methods() == [
        "Storage.getCookies",
        "Storage.setCookies",
        "Storage.getCookies",
        "Browser.close",
    ]
    assert fake.socket.closed


def test_the_read_deletes_soundclouds_cookies_and_keeps_googles_and_the_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    signin.open_sign_in_window(WindowRequests(), BRAVE)
    assert fake.socket.soundcloud_names() == []
    assert [item["name"] for item in fake.socket.cookies] == ["SID"]
    assert fake.profile.exists()
    assert fake.forgotten == []


def test_the_deleting_copies_hold_no_cookie_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    signin.open_sign_in_window(WindowRequests(), BRAVE)
    [deleting] = [
        request for request in fake.socket.sent if request["method"] == "Storage.setCookies"
    ]
    assert ACCESS not in json.dumps(deleting) and REFRESH not in json.dumps(deleting)
    assert len(deleting["params"]["cookies"]) == 4


def test_a_window_closed_before_signing_in_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_OUT)
    with pytest.raises(WindowSignInFailed) as error_info:
        signin.open_sign_in_window(WindowRequests(), BRAVE)
    assert error_info.value.problem is WindowProblem.no_sign_in
    # Closed by the person, so it isn't opened again.
    assert len(fake.windows()) == 1
    assert fake.socket.soundcloud_names() == []
    assert fake.profile.exists()


def test_soundcloud_cookies_that_stay_make_the_profile_go(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.socket.deletes = False
    assert signin.open_sign_in_window(WindowRequests(), BRAVE) == SignInCookies(ACCESS, REFRESH)
    assert fake.forgotten == [fake.profile]


def test_a_delete_the_browser_refuses_makes_the_profile_go(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    send = fake.socket.send

    def refuse_set_cookies(message: str) -> None:
        request = json.loads(message)
        if request["method"] != "Storage.setCookies":
            send(message)
            return
        fake.socket._answers.append(json.dumps({"id": request["id"], "error": {"message": "no"}}))

    monkeypatch.setattr(fake.socket, "send", refuse_set_cookies)
    assert signin.open_sign_in_window(WindowRequests(), BRAVE) == SignInCookies(ACCESS, REFRESH)
    assert fake.forgotten == [fake.profile]


def test_cancel_stops_the_window_by_its_process_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.close_window_at_once = False
    requests = WindowRequests()
    requests.cancel.set()
    assert signin.open_sign_in_window(requests, BRAVE) is None
    [window] = fake.started
    assert fake.stopped[0] == window.pid
    # Nothing is read, and the profile stays for the next sign-in.
    assert fake.socket.sent == []
    assert fake.profile.exists()


def test_ive_signed_in_closes_the_window_normally_then_reads_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.close_window_at_once = False
    requests = WindowRequests()
    requests.finish.set()
    assert signin.open_sign_in_window(requests, BRAVE) == SignInCookies(ACCESS, REFRESH)
    window = fake.started[0]
    assert fake.asked_to_close == [window.pid]
    # Closed, not stopped, since a stopped browser saves no cookies.
    assert window.pid not in fake.stopped


def test_the_window_closes_by_itself_once_soundcloud_shows_a_signed_in_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.close_window_at_once = False
    fake.titles = [
        [SIGN_IN_PAGE],
        ["Sign in - Google Accounts - Brave", SIGN_IN_PAGE],
        [SIGNED_IN_PAGE],
    ]
    reading: list[bool] = []
    requests = WindowRequests(on_reading=reading.append)
    assert signin.open_sign_in_window(requests, BRAVE) == SignInCookies(ACCESS, REFRESH)
    assert fake.asked_to_close == [fake.started[0].pid]
    assert reading == [True]


@pytest.mark.parametrize(
    "title",
    [
        "Your feed on SoundCloud - Personal - Microsoft\N{ZERO WIDTH SPACE} Edge",
        "Settings on SoundCloud and 1 more page - Profile 1 - Microsoft\N{ZERO WIDTH SPACE} Edge",
    ],
)
def test_signed_in_pages_count_in_every_browsers_title(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, title: str
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.close_window_at_once = False
    fake.titles = [[title]]
    assert signin.open_sign_in_window(WindowRequests(), BRAVE) is not None
    assert len(fake.asked_to_close) == 1


@pytest.mark.parametrize(
    "title",
    [
        SIGN_IN_PAGE,
        "SoundCloud \N{EN DASH} Hear the world\N{RIGHT SINGLE QUOTATION MARK}s sounds - Brave",
        "Discover the top streamed music and songs online on Soundcloud - Brave",
        "soundcloud.com is asking you to",
        "Brave",
    ],
)
def test_pages_a_signed_out_person_sees_dont_close_the_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, title: str
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_OUT)
    fake.close_window_at_once = False
    checks: list[str] = []

    def titles(pids: list[int]) -> list[str]:
        checks.append(title)
        if len(checks) == 5:
            requests.cancel.set()
        return [title]

    requests = WindowRequests()
    monkeypatch.setattr(browser_windows, "window_titles", titles)
    assert signin.open_sign_in_window(requests, BRAVE) is None
    assert fake.asked_to_close == []


def test_a_window_that_closed_by_itself_without_a_sign_in_opens_once_more(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_OUT)
    fake.close_window_at_once = False
    fake.titles = [[SIGNED_IN_PAGE]]
    openings: list[int] = []

    def person_signs_in_the_second_time() -> None:
        openings.append(len(openings) + 1)
        if len(openings) == 2:
            fake.socket.cookies = list(SIGNED_IN)

    fake.on_window = person_signs_in_the_second_time
    reading: list[bool] = []
    requests = WindowRequests(on_reading=reading.append)
    assert signin.open_sign_in_window(requests, BRAVE) == SignInCookies(ACCESS, REFRESH)
    assert openings == [1, 2]
    assert reading == [True, False, True]


def test_a_window_that_closes_by_itself_twice_without_a_sign_in_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_OUT)
    fake.close_window_at_once = False
    fake.titles = [[SIGNED_IN_PAGE]]
    with pytest.raises(WindowSignInFailed) as error_info:
        signin.open_sign_in_window(WindowRequests(), BRAVE)
    assert error_info.value.problem is WindowProblem.no_sign_in
    assert len(fake.windows()) == 1 + signin.REOPENS


def test_every_window_opens_without_the_tabs_the_profile_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_OUT)
    fake.close_window_at_once = False
    fake.titles = [[SIGNED_IN_PAGE]]
    sessions = fake.profile / "Default" / "Sessions"
    sessions.mkdir(parents=True)
    saved_tabs_at_open: list[bool] = []
    start = fake.start

    def each_browser_saves_its_tabs(
        browser: Path, profile: Path, url: str, flags: tuple[str, ...]
    ) -> FakeProcess:
        if url == signin.START_URL:
            saved_tabs_at_open.append(sessions.exists())
        process = start(browser, profile, url, flags)
        # The headless reader saves its about:blank tab too, which Brave would restore.
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "Tabs_1").write_text(url, encoding="utf-8")
        return process

    monkeypatch.setattr(chromium, "start", each_browser_saves_its_tabs)
    with pytest.raises(WindowSignInFailed):
        signin.open_sign_in_window(WindowRequests(), BRAVE)
    assert saved_tabs_at_open == [False, False]


def test_ive_signed_in_with_no_sign_in_doesnt_open_the_window_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_OUT)
    fake.close_window_at_once = False
    requests = WindowRequests()
    requests.finish.set()
    with pytest.raises(WindowSignInFailed):
        signin.open_sign_in_window(requests, BRAVE)
    assert len(fake.windows()) == 1


def test_a_window_left_open_too_long_is_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.close_window_at_once = False
    monkeypatch.setattr(signin, "SIGN_IN_TIMEOUT", 0)
    with pytest.raises(WindowSignInFailed) as error_info:
        signin.open_sign_in_window(WindowRequests(), BRAVE)
    assert error_info.value.problem is WindowProblem.timed_out
    assert fake.stopped == [fake.started[0].pid]
    assert fake.profile.exists()


def test_the_read_waits_while_another_browser_still_runs_on_the_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The started process hands the window to another browser and exits at once.
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.other_browsers = [4242]
    requests = WindowRequests()
    started_while_open: list[int] = []

    def browsers_on(profile: Path) -> list[int] | None:
        started_while_open.append(len(fake.started))
        if len(started_while_open) == 3:
            requests.finish.set()
        return list(fake.other_browsers)

    monkeypatch.setattr(chromium, "browsers_on", browsers_on)
    assert signin.open_sign_in_window(requests, BRAVE) == SignInCookies(ACCESS, REFRESH)
    # Only the window had started each time the profile was listed, so no read handed off to it.
    # Once the handed-off browser closed, its free lockfile ended the wait with no listing.
    assert started_while_open == [1, 1, 1]
    assert fake.asked_to_close == [4242]
    assert len(fake.started) == 2
    assert fake.stopped == []


def test_cancel_stops_another_browser_left_on_the_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.other_browsers = [4242]
    requests = WindowRequests()
    requests.cancel.set()
    assert signin.open_sign_in_window(requests, BRAVE) is None
    # Stopped by the profile it names, since it isn't the process sc-mp3 started.
    assert fake.stopped == [-1]
    assert len(fake.started) == 1


def test_a_headless_browser_that_handed_off_is_started_once_more(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.port_failures = 1
    assert signin.open_sign_in_window(WindowRequests(), BRAVE) is not None
    assert len(fake.started) == 3
    # What still held the profile was stopped first.
    assert -1 in fake.stopped


# The DevTools client


def test_a_call_skips_events_and_returns_its_result() -> None:
    socket = FakeSocket(SIGNED_IN)
    assert BrowserDevTools(socket).call("Storage.getCookies") == {"cookies": SIGNED_IN}


def test_a_call_sends_its_parameters() -> None:
    socket = FakeSocket([])
    BrowserDevTools(socket).call("Storage.setCookies", {"cookies": []})
    assert socket.sent == [{"id": 1, "method": "Storage.setCookies", "params": {"cookies": []}}]


def test_an_error_answer_raises_without_any_cookie() -> None:
    class Refusing(FakeSocket):
        def send(self, message: str) -> None:
            request = json.loads(message)
            self._answers = [json.dumps({"id": request["id"], "error": {"message": "nope"}})]

    with pytest.raises(ScmpError):
        BrowserDevTools(Refusing([])).call("Storage.getCookies")


def test_closing_the_browser_closes_the_socket_even_when_the_browser_went_first() -> None:
    class Gone(FakeSocket):
        def send(self, message: str) -> None:
            raise ConnectionClosed(Close(1000, ""), None)

    socket = Gone([])
    BrowserDevTools(socket).close_browser()
    assert socket.closed


# The browser's processes and windows


def test_the_port_comes_from_the_file_the_browser_writes(tmp_path: Path) -> None:
    (tmp_path / chromium.PORT_FILE).write_text("54321\n/devtools/browser/abc\n", encoding="utf-8")
    process = FakeProcess([])
    assert chromium.wait_for_port(process, tmp_path) == chromium.DevToolsPort(
        54321, "/devtools/browser/abc"
    )


def test_a_browser_that_exits_before_its_port_is_an_error(tmp_path: Path) -> None:
    process = FakeProcess([])
    process.closes.set()
    with pytest.raises(ScmpError):
        chromium.wait_for_port(process, tmp_path)


def test_a_process_with_no_windows_has_no_titles_and_nothing_to_close() -> None:
    # Process id 0 is the system's idle process, which owns no windows.
    assert browser_windows.window_titles([0]) == []
    assert browser_windows.close_windows(0) == 0


def test_without_titles_to_read_there_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_windows, "CAN_READ_TITLES", False)
    assert browser_windows.window_titles([1234]) == []
    assert browser_windows.close_windows(1234) == 0


# Cancel during the read, a read that fails or had to be stopped, and listings that fail


def test_cancel_during_the_read_signs_nothing_in_and_still_deletes_soundclouds_cookies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    requests = WindowRequests()
    send = fake.socket.send

    def person_cancels_while_it_reads(message: str) -> None:
        requests.cancel.set()
        send(message)

    monkeypatch.setattr(fake.socket, "send", person_cancels_while_it_reads)
    assert signin.open_sign_in_window(requests, BRAVE) is None
    assert fake.socket.soundcloud_names() == []
    assert fake.forgotten == []


def test_a_reader_that_had_to_be_stopped_makes_the_profile_go(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    monkeypatch.setattr(chromium, "wait_for_exit", lambda process, timeout=0.0: False)
    assert signin.open_sign_in_window(WindowRequests(), BRAVE) == SignInCookies(ACCESS, REFRESH)
    reader = fake.started[1]
    assert reader.pid in fake.stopped
    # A stopped browser may not have written the deletes to disk, so the profile goes.
    assert fake.forgotten == [fake.profile]


def test_a_read_that_fails_deletes_the_profile_that_may_hold_the_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    send = fake.socket.send

    def refuse_get_cookies(message: str) -> None:
        request = json.loads(message)
        if request["method"] != "Storage.getCookies":
            send(message)
            return
        fake.socket._answers.append(json.dumps({"id": request["id"], "error": {"message": "no"}}))

    monkeypatch.setattr(fake.socket, "send", refuse_get_cookies)
    with pytest.raises(ScmpError):
        signin.open_sign_in_window(WindowRequests(), BRAVE)
    assert fake.forgotten == [fake.profile]


def test_the_read_starts_without_the_tabs_the_profile_saved_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    sessions = fake.profile / "Default" / "Sessions"
    seen: list[tuple[str, bool]] = []
    start = fake.start

    def each_browser_saves_its_tabs(
        browser: Path, profile: Path, url: str, flags: tuple[str, ...]
    ) -> FakeProcess:
        seen.append((url, sessions.exists()))
        process = start(browser, profile, url, flags)
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "Tabs_1").write_text(url, encoding="utf-8")
        return process

    monkeypatch.setattr(chromium, "start", each_browser_saves_its_tabs)
    signin.open_sign_in_window(WindowRequests(), BRAVE)
    assert seen == [(signin.START_URL, False), ("about:blank", False)]


def test_an_ordinary_window_is_read_with_powershell_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    # The real checks, with PowerShell's path pointing nowhere, as AppLocker or WDAC would leave it.
    monkeypatch.setattr(chromium, "profile_in_use", REAL_PROFILE_IN_USE)
    monkeypatch.setattr(chromium, "browsers_on", REAL_BROWSERS_ON)
    monkeypatch.setattr(chromium, "stop_processes_on", REAL_STOP_PROCESSES_ON)
    blocked = tmp_path / "blocked" / "powershell.exe"
    monkeypatch.setattr(browser_processes, "powershell_exe", lambda: blocked)
    assert signin.open_sign_in_window(WindowRequests(), BRAVE) == SignInCookies(ACCESS, REFRESH)
    assert len(fake.started) == 2


def test_a_handed_off_window_that_cant_be_listed_is_waited_for_until_it_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.can_list = False
    checks: list[bool] = []

    def in_use_for_a_while(profile: Path) -> bool:
        # The handed-off browser holds the lockfile for three checks, then the person closes it.
        checks.append(len(checks) < 3)
        return checks[-1]

    monkeypatch.setattr(chromium, "profile_in_use", in_use_for_a_while)
    assert signin.open_sign_in_window(WindowRequests(), BRAVE) == SignInCookies(ACCESS, REFRESH)
    assert checks == [True, True, True, False]


def test_a_listing_that_fails_keeps_a_handed_off_window_waiting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    listings: list[list[int] | None] = [None, [4242]]
    monkeypatch.setattr(chromium, "browsers_on", lambda profile: listings.pop(0))
    in_use = [True, True, False]
    monkeypatch.setattr(chromium, "profile_in_use", lambda profile: in_use.pop(0))
    assert signin.open_sign_in_window(WindowRequests(), BRAVE) == SignInCookies(ACCESS, REFRESH)
    assert listings == [] and in_use == []
    assert len(fake.started) == 2


def test_a_cancel_that_cant_check_the_profile_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    fake.other_browsers = [4242]
    fake.can_list = False
    requests = WindowRequests()
    requests.cancel.set()
    with pytest.raises(WindowSignInFailed) as error_info:
        signin.open_sign_in_window(requests, BRAVE)
    assert error_info.value.problem is WindowProblem.browser_unknown


def test_a_handed_off_window_is_listed_every_two_seconds_at_most(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    listings: list[Path] = []

    def browsers_on(profile: Path) -> list[int]:
        listings.append(profile)
        return [4242]

    monkeypatch.setattr(chromium, "browsers_on", browsers_on)
    monkeypatch.setattr(chromium, "profile_in_use", lambda profile: True)
    exited = FakeProcess([])
    exited.closes.set()
    watch = signin.WindowWatch(exited, tmp_path)  # type: ignore[arg-type]
    for _ in range(5):
        assert watch.browsers() == [4242]
    assert listings == [tmp_path]


# sc-mp3 closing during a sign-in


def test_a_sign_in_keeps_each_browser_it_starts_and_the_profile_it_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    requests = WindowRequests()
    unread_during_read: list[Path | None] = []
    send = fake.socket.send

    def note_the_unread_profile(message: str) -> None:
        unread_during_read.append(requests.started.unread)
        send(message)

    monkeypatch.setattr(fake.socket, "send", note_the_unread_profile)
    signin.open_sign_in_window(requests, BRAVE)
    window, reader = fake.started
    # While the read runs, the profile may hold a session no read has deleted yet.
    assert set(unread_during_read) == {fake.profile}
    assert requests.started.unread is None
    assert {process.pid for process in requests.started._processes} == {window.pid, reader.pid}


def started_in(fake: FakeBrowser, requests: WindowRequests, count: int) -> list[FakeProcess]:
    """Browsers started for the sign-in that keep running, as a reader still waiting would."""
    return [
        requests.started.start(Path("brave.exe"), fake.profile, "about:blank", ())  # type: ignore[misc]
        for _ in range(count)
    ]


def test_closing_sc_mp3_stops_what_a_sign_in_started_and_sets_aside_an_unread_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    set_aside: list[Path] = []

    def note_set_aside(profile: Path) -> Path:
        set_aside.append(profile)
        return profile

    monkeypatch.setattr(chromium, "set_aside", note_set_aside)
    requests = WindowRequests()
    running, done = started_in(fake, requests, 2)
    done.closes.set()
    requests.started.reading(fake.profile)
    signin.stop_started(requests)
    assert fake.stopped == [running.pid]
    assert set_aside == [fake.profile]


def test_after_closing_the_sign_in_starts_no_browser_and_keeps_its_unread_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    set_aside: list[Path] = []

    def note_set_aside(profile: Path) -> Path:
        set_aside.append(profile)
        return profile

    monkeypatch.setattr(chromium, "set_aside", note_set_aside)
    requests = WindowRequests()
    [reader] = started_in(fake, requests, 1)
    requests.started.reading(fake.profile)
    stop = fake.stop

    def stop_wakes_the_sign_in_thread(process: FakeProcess) -> None:
        stop(process)
        # The killed reader wakes the thread, whose read ends and would clear the profile.
        requests.started.reading(None)

    monkeypatch.setattr(chromium, "stop", stop_wakes_the_sign_in_thread)
    signin.stop_started(requests)
    assert fake.stopped == [reader.pid]
    assert set_aside == [fake.profile]
    # A retry of the read, after the kill, starts nothing.
    with pytest.raises(ScmpError):
        started_in(fake, requests, 1)
    assert len(fake.started) == 1


def test_closing_sc_mp3_after_a_sign_in_ended_stops_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeBrowser(monkeypatch, tmp_path, SIGNED_IN)
    requests = WindowRequests()
    signin.open_sign_in_window(requests, BRAVE)
    signin.stop_started(requests)
    assert fake.stopped == []
    assert fake.profile.exists()
