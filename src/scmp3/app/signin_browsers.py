"""The Chromium-based browsers the sign-in window can use, and which one it uses.

The one picked in Settings comes first, then the person's default browser when it's one of these,
then Edge. Adding a browser means a row in BROWSER_KINDS and its id in `SignInBrowser` in
core/models/settings.py. Windows only for now, and its paths and commands are read as Windows
ones on every system, so the tests give the same answers everywhere.
"""

import logging
import ntpath
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_CHOICE = "default"
FALLBACK_ID = "edge"
# Where browsers install themselves, machine-wide first, then per user.
INSTALL_ROOTS = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
# The https handler the person picked. Newer Windows 11 builds keep it under UserChoiceLatest.
HTTPS_KEY = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https"
HTTPS_CHOICE_KEYS = (rf"{HTTPS_KEY}\UserChoiceLatest\ProgId", rf"{HTTPS_KEY}\UserChoice")
# The program at the start of an open command that isn't quoted, up to the first ".exe".
UNQUOTED_EXE = re.compile(r"(.+?\.exe)(?=\s|$)", re.IGNORECASE)


@dataclass(frozen=True)
class BrowserKind:
    """One browser the window can use: its id in settings, its name in the UI, where it lives."""

    id: str
    name: str
    exe: str
    folder: tuple[str, ...]  # its install folder under Program Files or the local app data


# Each takes the same flags and the same two steps. Chrome and Chromium share an exe name, so the
# install folder tells them apart.
BROWSER_KINDS = (
    BrowserKind("edge", "Edge", "msedge.exe", ("Microsoft", "Edge", "Application")),
    BrowserKind("brave", "Brave", "brave.exe", ("BraveSoftware", "Brave-Browser", "Application")),
    BrowserKind("chrome", "Chrome", "chrome.exe", ("Google", "Chrome", "Application")),
    BrowserKind("vivaldi", "Vivaldi", "vivaldi.exe", ("Vivaldi", "Application")),
    BrowserKind("chromium", "Chromium", "chrome.exe", ("Chromium", "Application")),
)


@dataclass(frozen=True)
class Browser:
    """An installed browser of a known kind."""

    kind: BrowserKind
    path: Path

    def to_dict(self) -> dict[str, str]:
        return {"id": self.kind.id, "name": self.kind.name}


@dataclass(frozen=True)
class BrowserOptions:
    """The browsers found, the person's default when it's one of them, and the one to use."""

    installed: tuple[Browser, ...] = ()
    default: Browser | None = None
    chosen: Browser | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_browser": self.chosen.to_dict() if self.chosen else None,
            "default_browser": self.default.to_dict() if self.default else None,
            "browsers": [browser.to_dict() for browser in self.installed],
        }


def recognize(path: PurePath) -> BrowserKind | None:
    """The kind of browser an executable is, by its name and, for a shared name, its folder."""
    windows = PureWindowsPath(str(path))
    matches = [kind for kind in BROWSER_KINDS if kind.exe == windows.name.lower()]
    folder = [part.lower() for part in windows.parts]
    for kind in matches:
        if _contains(folder, [part.lower() for part in kind.folder]):
            return kind
    return matches[0] if matches else None


def _contains(parts: list[str], run: list[str]) -> bool:
    return any(parts[at : at + len(run)] == run for at in range(len(parts) - len(run) + 1))


def choose(setting: str, options: BrowserOptions) -> Browser | None:
    """The browser the window uses: the one picked in Settings, the default, Edge, or any."""
    by_id = {browser.kind.id: browser for browser in options.installed}
    if setting != DEFAULT_CHOICE and setting in by_id:
        return by_id[setting]
    if options.default is not None:
        return options.default
    return by_id.get(FALLBACK_ID) or next(iter(options.installed), None)


def find_browsers(setting: str) -> BrowserOptions:
    """Look for each known browser and the default one, and pick the one the window uses."""
    if sys.platform != "win32":
        return BrowserOptions()
    installed = tuple(_installed())
    default = _default_browser(installed)
    options = BrowserOptions(installed, default)
    return BrowserOptions(installed, default, choose(setting, options))


def _installed() -> Iterator[Browser]:
    for kind in BROWSER_KINDS:
        path = next((path for path in _candidates(kind) if path.is_file()), None)
        if path is not None:
            yield Browser(kind, path)


def _candidates(kind: BrowserKind) -> Iterator[Path]:
    """Where a browser of this kind may be: its App Paths entry, then the usual folders."""
    for path in _app_paths(kind.exe):
        if recognize(path) == kind:
            yield path
    for name in INSTALL_ROOTS:
        root = os.environ.get(name)
        if root:
            yield Path(root, *kind.folder, kind.exe)


def _app_paths(exe: str) -> Iterator[Path]:
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        value = _registry_value(hive, rf"{APP_PATHS_KEY}\{exe}", "")
        if value:
            yield Path(PureWindowsPath(value.strip('"')))


def _default_browser(installed: tuple[Browser, ...]) -> Browser | None:
    """The person's default browser, when it's one the window can use."""
    path = default_browser_path()
    if path is None:
        return None
    kind = recognize(path)
    if kind is None or not path.is_file():
        log.info("The default browser, %s, can't be used for signing in", path.name)
        return None
    return Browser(kind, path)


def default_browser_path() -> Path | None:
    """The program Windows opens https links with, from the https handler's open command."""
    import winreg

    prog_id = next(
        (
            value
            for key in HTTPS_CHOICE_KEYS
            if (value := _registry_value(winreg.HKEY_CURRENT_USER, key, "ProgId"))
        ),
        None,
    )
    if prog_id is None:
        return None
    command = _registry_value(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command", "")
    program = command_program(command) if command else None
    return Path(program) if program else None


def command_program(command: str) -> PureWindowsPath | None:
    """The program a Windows open command runs: its first token, which may be quoted."""
    # A REG_EXPAND_SZ command can name its folder as %ProgramFiles% or the like.
    command = ntpath.expandvars(command).strip()
    if command.startswith('"'):
        program = command[1:].partition('"')[0]
    elif unquoted := UNQUOTED_EXE.match(command):
        # An unquoted path with spaces, such as C:\Program Files\...\brave.exe --flag, ends at .exe.
        program = unquoted[1]
    else:
        program = command.partition(" ")[0]
    return PureWindowsPath(program) if program else None


def _registry_value(hive: Any, key_path: str, name: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(hive, key_path) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value if isinstance(value, str) and value else None
