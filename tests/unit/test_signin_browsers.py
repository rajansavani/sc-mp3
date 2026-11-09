"""Tests for finding the browsers the sign-in window can use, and picking one."""

from pathlib import Path, PureWindowsPath
from typing import get_args

import pytest

from scmp3.app import signin_browsers
from scmp3.app.signin_browsers import (
    BROWSER_KINDS,
    Browser,
    BrowserKind,
    BrowserOptions,
    choose,
    command_program,
    find_browsers,
    recognize,
)
from scmp3.core.models import SignInBrowser


def kind(browser_id: str) -> BrowserKind:
    return next(kind for kind in BROWSER_KINDS if kind.id == browser_id)


EDGE = Browser(kind("edge"), Path("msedge.exe"))
BRAVE = Browser(kind("brave"), Path("brave.exe"))
CHROME = Browser(kind("chrome"), Path("chrome.exe"))


def test_every_browser_can_be_picked_in_settings() -> None:
    assert get_args(SignInBrowser) == ("default", *(kind.id for kind in BROWSER_KINDS))


@pytest.mark.parametrize(
    ("path", "browser_id"),
    [
        (r"C:\Users\x\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe", "brave"),
        (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "edge"),
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome"),
        (r"C:\Users\x\AppData\Local\Chromium\Application\chrome.exe", "chromium"),
        (r"C:\Users\x\AppData\Local\Vivaldi\Application\VIVALDI.EXE", "vivaldi"),
        # Chrome somewhere unusual is still Chrome.
        (r"D:\Apps\chrome.exe", "chrome"),
    ],
)
def test_a_browser_is_recognized_by_its_program(path: str, browser_id: str) -> None:
    found = recognize(PureWindowsPath(path))
    assert found is not None
    assert found.id == browser_id


@pytest.mark.parametrize(
    "path",
    [r"C:\Program Files\Mozilla Firefox\firefox.exe", r"C:\Users\x\AppData\Local\Opera\opera.exe"],
)
def test_other_browsers_arent_recognized(path: str) -> None:
    assert recognize(PureWindowsPath(path)) is None


@pytest.mark.parametrize(
    ("command", "program"),
    [
        (r'"C:\Brave\brave.exe" --single-argument %1', r"C:\Brave\brave.exe"),
        (r"C:\Edge\msedge.exe --single-argument %1", r"C:\Edge\msedge.exe"),
        # Unquoted, with spaces in the folder's name.
        (
            r"C:\Program Files\Brave\brave.exe --single-argument %1",
            r"C:\Program Files\Brave\brave.exe",
        ),
        ("  ", None),
    ],
)
def test_the_program_comes_from_an_open_command(command: str, program: str | None) -> None:
    assert command_program(command) == (PureWindowsPath(program) if program else None)


def test_a_command_that_names_its_folder_by_variable_is_expanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCMP3_TEST_APPS", r"C:\Apps")
    command = r'"%SCMP3_TEST_APPS%\Vivaldi\vivaldi.exe" -- "%1"'
    assert command_program(command) == PureWindowsPath(r"C:\Apps\Vivaldi\vivaldi.exe")


def test_the_browser_picked_in_settings_comes_first() -> None:
    options = BrowserOptions((EDGE, BRAVE, CHROME), BRAVE)
    assert choose("chrome", options) == CHROME
    assert choose("default", options) == BRAVE


def test_a_picked_browser_that_isnt_installed_falls_back_to_the_default() -> None:
    assert choose("vivaldi", BrowserOptions((EDGE, BRAVE), BRAVE)) == BRAVE


def test_without_a_usable_default_edge_is_used_then_any_browser() -> None:
    assert choose("default", BrowserOptions((BRAVE, EDGE), None)) == EDGE
    assert choose("default", BrowserOptions((CHROME,), None)) == CHROME
    assert choose("default", BrowserOptions()) is None


def test_the_view_names_the_browsers() -> None:
    assert BrowserOptions((EDGE, BRAVE), BRAVE, BRAVE).to_dict() == {
        "window_browser": {"id": "brave", "name": "Brave"},
        "default_browser": {"id": "brave", "name": "Brave"},
        "browsers": [{"id": "edge", "name": "Edge"}, {"id": "brave", "name": "Brave"}],
    }


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A pretend Windows whose install folders are all `tmp_path`, with nothing registered."""
    monkeypatch.setattr(signin_browsers.sys, "platform", "win32")
    for name in signin_browsers.INSTALL_ROOTS:
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setattr(signin_browsers, "_app_paths", lambda exe: iter([]))
    monkeypatch.setattr(signin_browsers, "default_browser_path", lambda: None)
    return tmp_path


def install(root: Path, browser_id: str) -> Path:
    browser = kind(browser_id)
    path = Path(root, *browser.folder, browser.exe)
    path.parent.mkdir(parents=True)
    path.touch()
    return path


def test_installed_browsers_are_found_in_their_install_folders(windows: Path) -> None:
    edge = install(windows, "edge")
    brave = install(windows, "brave")
    options = find_browsers("default")
    assert [browser.path for browser in options.installed] == [edge, brave]
    # With no default browser that can be used, the window opens in Edge.
    assert options.default is None
    assert options.chosen == Browser(kind("edge"), edge)


def test_a_registered_browser_is_found_first(
    windows: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(windows, "brave")
    registered = Path(windows, "elsewhere", "brave.exe")
    registered.parent.mkdir()
    registered.touch()
    monkeypatch.setattr(signin_browsers, "_app_paths", lambda exe: iter([registered]))
    [brave] = find_browsers("default").installed
    assert brave.path == registered


def test_the_default_browser_is_used_when_its_one_the_window_can_use(
    windows: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(windows, "edge")
    brave = install(windows, "brave")
    monkeypatch.setattr(signin_browsers, "default_browser_path", lambda: brave)
    options = find_browsers("default")
    assert options.default == Browser(kind("brave"), brave)
    assert options.chosen == options.default


def test_a_default_browser_the_window_cant_use_means_edge(
    windows: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edge = install(windows, "edge")
    firefox = windows / "Mozilla Firefox" / "firefox.exe"
    monkeypatch.setattr(signin_browsers, "default_browser_path", lambda: firefox)
    options = find_browsers("default")
    assert options.default is None
    assert options.chosen == Browser(kind("edge"), edge)


def test_no_browsers_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signin_browsers.sys, "platform", "darwin")
    assert find_browsers("default") == BrowserOptions()
