"""`scmp3 app-menu`: adds sc-mp3 to the Linux app menu, or removes it again.

`uv tool install` can't run anything once it has installed, so this writes the menu entry and its
icon under ~/.local/share, where desktops look for a user's own apps.
"""

import argparse
import os
import posixpath
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePath

from scmp3.cli_output import EXIT_ERROR, EXIT_OK, EXIT_USAGE
from scmp3.cli_review import say
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.paths import write_text_atomically

COMMAND = "scmp3"
ENTRY_NAME = "sc-mp3.desktop"
ICON_NAME = "sc-mp3.png"
# The size of the icon in the package, which is the size folder it goes in.
ICON_SIZE = "256x256"

# The menu entry. Exec and Icon are full paths, so neither PATH nor an icon cache can hide them.
# StartupWMClass is the name the window gets from the scmp3 command, so docks match the two.
DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=sc-mp3
Comment=Save SoundCloud and YouTube links as tagged MP3s for Spotify Local Files
Exec={command} %F
Icon={icon}
Terminal=false
Categories=AudioVideo;Audio;
Keywords=SoundCloud;YouTube;MP3;Spotify;
StartupWMClass=scmp3
"""

# Exec's quotes take these with a backslash, and the file's own escaping doubles each backslash.
# A % is a field code, so a literal one is doubled anywhere in Exec.
EXEC_ESCAPES = {'"': '\\\\"', "`": "\\\\`", "$": "\\\\$", "\\": "\\\\\\\\", "%": "%%"}

LINUX_ONLY = "scmp3 app-menu works on Linux only, where uv installs sc-mp3 without a menu entry."
NO_COMMAND = (
    "The scmp3 command wasn't found, so the menu entry would have nothing to open. Run "
    "uv tool update-shell, open a new terminal, and run scmp3 app-menu again."
)
ADDED = "Added sc-mp3 to your app menu. It opens {command}, and is made of these files:"
REMOVE_FIRST = "Before you uninstall sc-mp3, run scmp3 app-menu --remove to delete them."
REMOVED = "Removed sc-mp3 from your app menu. These files were deleted:"
NOTHING_TO_REMOVE = "sc-mp3 isn't in your app menu, so there was nothing to remove."
NOT_WRITTEN = (
    "Couldn't write {path}: {reason}. Check that you can write to that folder, then run "
    "scmp3 app-menu again."
)
NOT_REMOVED = (
    "Couldn't delete {path}: {reason}. Check that you can change that folder, then run "
    "scmp3 app-menu --remove again."
)
TEMPORARY_COMMAND = (
    "This scmp3 runs from a temporary folder, {folder}, so a menu entry would stop working once "
    "that's cleaned up. Install sc-mp3 with uv tool install, as the README says, then run "
    "scmp3 app-menu again."
)


@dataclass(frozen=True)
class MenuFiles:
    """The two files the menu entry is made of."""

    entry: Path
    icon: Path

    @classmethod
    def under(cls, data_home: Path) -> "MenuFiles":
        """Where the files go in a user's data folder, as the freedesktop.org specs place them."""
        return cls(
            entry=data_home / "applications" / ENTRY_NAME,
            icon=data_home / "icons" / "hicolor" / ICON_SIZE / "apps" / ICON_NAME,
        )

    def both(self) -> tuple[Path, Path]:
        return (self.entry, self.icon)


def data_home(environ: Mapping[str, str], home: Path) -> Path:
    """$XDG_DATA_HOME, or ~/.local/share when it's unset or not a full path, as the spec says."""
    value = environ.get("XDG_DATA_HOME", "")
    return Path(value) if os.path.isabs(value) else home / ".local" / "share"


def find_command(argv0: str, which: Callable[[str], str | None] = shutil.which) -> str | None:
    """The scmp3 command's full path: the one running now, or else the one on PATH."""
    if Path(argv0).name == COMMAND:
        # The menu is Linux's, so the path follows POSIX rules wherever the tests run.
        return posixpath.abspath(argv0)
    return which(COMMAND)


def temporary_folders(environ: Mapping[str, str], home: PurePath, temp_dir: str) -> list[str]:
    """Folders a command can vanish from: uv's cache, where uvx runs tools, and the temp folder."""
    # Found the way uv finds its cache on Linux, with POSIX paths wherever the tests run.
    uv_cache = environ.get("UV_CACHE_DIR", "")
    if not posixpath.isabs(uv_cache):
        cache_home = environ.get("XDG_CACHE_HOME", "")
        base = cache_home if posixpath.isabs(cache_home) else posixpath.join(str(home), ".cache")
        uv_cache = posixpath.join(base, "uv")
    return [uv_cache, temp_dir]


def is_inside(path: str, folder: str) -> bool:
    """Whether a POSIX path lies inside a folder."""
    return posixpath.normpath(path).startswith(posixpath.normpath(folder) + "/")


def exec_quoted(path: str) -> str:
    """A path quoted for the Exec key, whatever characters it holds."""
    return '"' + "".join(EXEC_ESCAPES.get(char, char) for char in path) + '"'


def desktop_entry(command: str, icon: Path) -> str:
    """The text of the menu entry, which opens `command`."""
    # Icon has no quoting rules, but a string value escapes its backslashes.
    icon_value = str(icon).replace("\\", "\\\\")
    return DESKTOP_ENTRY.format(command=exec_quoted(command), icon=icon_value)


def packaged_icon() -> bytes:
    """The app's icon, which the package carries for the menu."""
    return resources.files("scmp3").joinpath("icons", ICON_NAME).read_bytes()


def _reason(error: Exception) -> str:
    """The OS's words for why a file couldn't be written or deleted."""
    # write_text_atomically wraps the OSError in a ScmpError of its own.
    cause = error.__cause__ if isinstance(error, ScmpError) and error.__cause__ else error
    return getattr(cause, "strerror", None) or str(cause)


def add_to_menu(command: str, files: MenuFiles, icon: bytes) -> None:
    """Write the icon, then the entry that shows it. Raises ScmpError(disk) saying what to do."""
    try:
        files.icon.parent.mkdir(parents=True, exist_ok=True)
        files.icon.write_bytes(icon)
    except OSError as error:
        detail = NOT_WRITTEN.format(path=files.icon, reason=_reason(error))
        raise ScmpError(ErrorKind.disk, detail) from error
    try:
        # Swapped in whole, so a desktop never reads a cut-off entry.
        write_text_atomically(files.entry, desktop_entry(command, files.icon))
    except ScmpError as error:
        detail = NOT_WRITTEN.format(path=files.entry, reason=_reason(error))
        raise ScmpError(ErrorKind.disk, detail) from error


def remove_from_menu(files: MenuFiles) -> list[Path]:
    """Delete the entry and its icon, and return the ones that were there."""
    removed = []
    for path in files.both():
        if not path.exists():
            continue
        try:
            path.unlink()
        except OSError as error:
            detail = NOT_REMOVED.format(path=path, reason=_reason(error))
            raise ScmpError(ErrorKind.disk, detail) from error
        removed.append(path)
    return removed


def add_menu_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the arguments for `scmp3 app-menu`."""
    parser.add_argument(
        "--remove", action="store_true", help="remove sc-mp3 from the app menu again"
    )


@dataclass(frozen=True)
class MenuPlace:
    """What `scmp3 app-menu` reads from the computer, so tests can pass their own."""

    platform: str
    environ: Mapping[str, str]
    home: Path
    argv0: str
    temp_dir: str
    which: Callable[[str], str | None] = shutil.which

    @classmethod
    def here(cls) -> "MenuPlace":
        """This computer, and the command that's running."""
        return cls(sys.platform, os.environ, Path.home(), sys.argv[0], tempfile.gettempdir())


def run_menu(args: argparse.Namespace, place: MenuPlace | None = None) -> int:
    """Add the menu entry, or remove it with --remove. Returns the exit code."""
    place = place or MenuPlace.here()
    if not place.platform.startswith("linux"):
        say(LINUX_ONLY)
        return EXIT_USAGE
    files = MenuFiles.under(data_home(place.environ, place.home))
    try:
        if args.remove:
            return _remove(files)
        return _add(files, place)
    except ScmpError as error:
        say(error.detail or str(error))
        return EXIT_ERROR


def _add(files: MenuFiles, place: MenuPlace) -> int:
    command = find_command(place.argv0, place.which)
    if command is None:
        say(NO_COMMAND)
        return EXIT_ERROR
    temporary = temporary_folders(place.environ, place.home, place.temp_dir)
    if any(is_inside(command, folder) for folder in temporary):
        say(TEMPORARY_COMMAND.format(folder=posixpath.dirname(command)))
        return EXIT_ERROR
    add_to_menu(command, files, packaged_icon())
    say(ADDED.format(command=command))
    # The paths are the result, so they go to stdout like a saved file's path, in step with stderr.
    for path in files.both():
        print(path, flush=True)
    say(REMOVE_FIRST)
    return EXIT_OK


def _remove(files: MenuFiles) -> int:
    removed = remove_from_menu(files)
    if not removed:
        say(NOTHING_TO_REMOVE)
        return EXIT_OK
    say(REMOVED)
    for path in removed:
        print(path, flush=True)
    return EXIT_OK
