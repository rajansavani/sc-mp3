"""Loading yt-dlp from a downloaded update, a zipapp put first on sys.path (TECH_SPEC §14).

yt-dlp patches requests and urllib3 when it's imported, so it can be imported only once per
process. An update is checked with find_spec before any of its code runs. One that fails after
that can only be dropped by starting a new process.
"""

import importlib
import importlib.util
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

ENGINE_MODULE = "yt_dlp"
VERSION_MODULE = "yt_dlp.version"
# Any value turns plugin loading off, whichever copy of yt-dlp loads.
NO_PLUGINS_ENV = "YTDLP_NO_PLUGINS"
# Looking up an extractor builds its class, which fails fast on a broken engine.
SMOKE_EXTRACTOR = "Soundcloud"

# The child side of an engine install: `scmp3 --check-engine <zip>`, which the app runs itself.
CHECK_FLAG = "--check-engine"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def turn_off_plugins() -> None:
    """Keep yt-dlp from loading plugins installed for someone's own yt-dlp."""
    # It isn't a YoutubeDL option, so it's set for the whole process, before the first YoutubeDL.
    os.environ[NO_PLUGINS_ENV] = "1"


def engine_imported() -> bool:
    """Whether some copy of yt-dlp is imported already, so no update can be put in front of it."""
    return ENGINE_MODULE in sys.modules


def put_first(zip_path: Path) -> str | None:
    """Put an update first on sys.path, or say why yt-dlp wouldn't load from it.

    None of the update's code runs. An update that can't be used is taken off sys.path again,
    so the built-in copy loads as if it had never been there.
    """
    entry = str(zip_path)
    sys.path.insert(0, entry)
    problem = _spec_problem(entry)
    if problem is not None:
        take_off(entry)
    return problem


def _spec_problem(entry: str) -> str | None:
    try:
        spec = importlib.util.find_spec(ENGINE_MODULE)
    except Exception as error:
        # A damaged zip can make the import system itself raise.
        return f"looking for yt_dlp in the update raised {type(error).__name__}"
    # A missing, unreadable or truncated zip is skipped by the import system, which then finds
    # the built-in copy instead.
    if spec is None or not is_inside(spec.origin, entry):
        return "the update has no yt_dlp that Python can load"
    return None


def take_off(entry: str) -> None:
    """Remove an update from sys.path and from the import system's cache of path entries."""
    while entry in sys.path:
        sys.path.remove(entry)
    sys.path_importer_cache.pop(entry, None)
    importlib.invalidate_caches()


def import_engine(zip_path: Path) -> str:
    """Import yt-dlp from an update that's first on sys.path, smoke-test it, return its version.

    Raises whatever the update raises. By then its code has run, so the caller can't fall back.
    """
    turn_off_plugins()
    module = importlib.import_module(ENGINE_MODULE)
    version = importlib.import_module(VERSION_MODULE).__version__
    # The last guard: find_spec agreed, but the import must have come from the update too.
    if not is_inside(module.__file__, str(zip_path)):
        raise ImportError("yt_dlp was imported from outside the update")
    smoke_test(module)
    return version


def smoke_test(module: ModuleType) -> None:
    """Build a YoutubeDL and look up one extractor, without any request."""
    params = {"quiet": True, "no_warnings": True, "logger": _SilentLogger(), "cachedir": False}
    with module.YoutubeDL(params) as ydl:
        ydl.get_info_extractor(SMOKE_EXTRACTOR)


def is_inside(path: str | None, folder: str) -> bool:
    """Whether a module's file is inside a zip or folder."""
    if not path:
        return False
    return os.path.normcase(path).startswith(os.path.normcase(folder) + os.sep)


def describe(error: BaseException, zip_path: Path) -> str:
    """An update's failure for the log, naming the zip without its folder."""
    text = str(error).replace(str(zip_path), zip_path.name)
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


def own_command() -> list[str]:
    """The command that starts sc-mp3 again: the frozen app's exe, or Python running the package."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "scmp3"]


def check_here(args: Sequence[str]) -> int:
    """Be the child of an engine install: import the update with no fallback, then exit.

    Exits 0 when it works, and 1 with the reason on stderr when it doesn't. The parent never
    imports an update before this has passed.
    """
    if len(args) != 1:
        print(f"usage: scmp3 {CHECK_FLAG} <zip>", file=sys.stderr)
        return EXIT_USAGE
    zip_path = Path(args[0])
    problem = put_first(zip_path)
    if problem is not None:
        print(problem, file=sys.stderr)
        return EXIT_FAILED
    try:
        version = import_engine(zip_path)
    except Exception as error:
        print(describe(error, zip_path), file=sys.stderr)
        return EXIT_FAILED
    print(version)
    return EXIT_OK


class _SilentLogger:
    """A yt-dlp logger that drops everything, for the smoke test."""

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass
