"""Where sc-mp3 keeps its files, where it finds ffmpeg, and how it writes a file safely.

The getters only build paths. Code that writes a file creates its folder first.
"""

import logging
import os
import platform
import secrets
import shutil
import sys
from datetime import datetime
from pathlib import Path

import platformdirs

from scmp3.core.errors import ErrorKind, ScmpError

APP_NAME = "sc-mp3"

# Tests set this to put every folder below under one root.
HOME_ENV = "SCMP3_HOME"
FFMPEG_ENV = "SCMP3_FFMPEG"

# How many random names a new temp file tries. Only a name that's taken is worth another try.
TEMP_NAME_TRIES = 5
# Binary on Windows, so line endings are written as given, as tempfile.mkstemp does.
TEMP_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)

# src/scmp3/core/paths.py sits three levels below the repo root in a dev checkout.
REPO_ROOT = Path(__file__).resolve().parents[3]

# The folder names packaging/fetch_binaries.py uses for each OS and CPU.
OS_NAMES = {"win32": "windows", "darwin": "macos", "linux": "linux"}
ARCH_NAMES = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}

log = logging.getLogger(__name__)


def _home() -> Path | None:
    """The root from SCMP3_HOME, or None when it isn't set."""
    value = os.environ.get(HOME_ENV)
    return Path(value).expanduser() if value else None


def _platform_dirs() -> platformdirs.PlatformDirs:
    # appauthor=False keeps Windows from nesting the folder twice, as in sc-mp3\sc-mp3.
    return platformdirs.PlatformDirs(APP_NAME, appauthor=False)


def config_dir() -> Path:
    """The folder for settings.json."""
    home = _home()
    return home / "config" if home else _platform_dirs().user_config_path


def data_dir() -> Path:
    """The folder for history.db, queue.json and installed components."""
    home = _home()
    return home / "data" if home else _platform_dirs().user_data_path


def cache_dir() -> Path:
    """The folder for yt-dlp's cache and the staging folders."""
    home = _home()
    return home / "cache" if home else _platform_dirs().user_cache_path


def log_dir() -> Path:
    """The folder for the rotating log files."""
    home = _home()
    return home / "logs" if home else _platform_dirs().user_log_path


def staging_dir() -> Path:
    """The folder that holds one staging folder per job."""
    return cache_dir() / "staging"


def components_dir() -> Path:
    """The folder for downloaded components such as Deno and newer yt-dlp versions."""
    return data_dir() / "components"


def default_library_dir() -> Path:
    """The default folder for saved songs: sc-mp3 inside the user's Music folder."""
    home = _home()
    # Under SCMP3_HOME too, so tests never write to the real Music folder.
    music = home / "music" if home else _platform_dirs().user_music_path
    return music / APP_NAME


def ffmpeg_exe() -> Path:
    """Find ffmpeg: SCMP3_FFMPEG, next to the frozen app, packaging/bin in a dev checkout, PATH."""
    for candidate in _ffmpeg_candidates():
        if candidate.is_file():
            return candidate
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    raise ScmpError(ErrorKind.convert_failed, "ffmpeg not found")


def _ffmpeg_candidates() -> list[Path]:
    """The places to look for ffmpeg before PATH, in order."""
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    candidates: list[Path] = []

    override = os.environ.get(FFMPEG_ENV)
    if override:
        override_path = Path(override).expanduser()
        if not override_path.is_file():
            log.warning("%s doesn't point to a file, so ffmpeg is looked for elsewhere", FFMPEG_ENV)
        candidates.append(override_path)

    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / name)
        # PyInstaller 6 puts bundled binaries in its contents folder, which can differ.
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            candidates.append(Path(bundle) / name)

    tag = platform_tag()
    if tag:
        candidates.append(REPO_ROOT / "packaging" / "bin" / tag / name)
    return candidates


def platform_tag() -> str | None:
    """This machine as <os>-<arch>, such as windows-x64. None for an OS or CPU we don't ship."""
    os_name = OS_NAMES.get(sys.platform)
    arch = ARCH_NAMES.get(platform.machine().lower())
    if os_name is None or arch is None:
        return None
    return f"{os_name}-{arch}"


def _temp_name(folder: Path, prefix: str, suffix: str) -> Path:
    return folder / f"{prefix}{secrets.token_hex(8)}{suffix}"


def create_temp_file(folder: Path, prefix: str, suffix: str = "") -> tuple[int, Path]:
    """Create a new file with a random name in `folder`. Returns its open handle and its path.

    Any error but a taken name raises at once. On Windows, tempfile.mkstemp takes a folder that
    refuses new files for a taken name, and tries again up to 2,147,483,647 times.
    """
    for _ in range(TEMP_NAME_TRIES - 1):
        temp = _temp_name(folder, prefix, suffix)
        try:
            return os.open(temp, TEMP_FILE_FLAGS, 0o600), temp
        except FileExistsError:
            continue
    # The last try lets FileExistsError through.
    temp = _temp_name(folder, prefix, suffix)
    return os.open(temp, TEMP_FILE_FLAGS, 0o600), temp


def write_text_atomically(path: Path, text: str) -> None:
    """Write UTF-8 text to a temp file next to `path`, then swap it in with os.replace.

    Readers see the old file or the new one, never a partial one.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = create_temp_file(path.parent, f".{path.name}.", ".tmp")
    except OSError as error:
        raise ScmpError(
            ErrorKind.disk, f"can't write {path.name}: {error.strerror or error}"
        ) from error
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    except OSError as error:
        raise ScmpError(
            ErrorKind.disk, f"can't write {path.name}: {error.strerror or error}"
        ) from error
    finally:
        # After a successful replace the temp name is gone, so this only cleans up failures.
        temp.unlink(missing_ok=True)


def backup_path(path: Path, when: datetime) -> Path:
    """A free name like settings.bad-20261018-153000.json next to `path`, for a damaged file."""
    stamp = when.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.bad-{stamp}{path.suffix}")
    counter = 2
    while backup.exists():
        backup = path.with_name(f"{path.stem}.bad-{stamp}-{counter}{path.suffix}")
        counter += 1
    return backup
