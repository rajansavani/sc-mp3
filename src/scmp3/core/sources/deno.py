"""Finding the Deno that yt-dlp runs YouTube's scripts on (TECH_SPEC §7).

The copy sc-mp3 downloads comes first. A uv install also counts a Deno of its own, from the
yt-dlp[deno] extra or on PATH. A frozen app counts only the downloaded copy, since an app opened
from Finder gets a minimal PATH, and yt-dlp is always handed the path rather than left to search.
"""

import functools
import logging
import re
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from scmp3.core.components import DENO, Components
from scmp3.core.manifests import write_manifest

# yt-dlp refuses an older Deno and then quietly loses formats, so an older one doesn't count.
MIN_VERSION = (2, 3, 0)
# Seconds `deno --version` may take. A first run can wait on an antivirus scan.
VERSION_TIMEOUT = 30
# The first line of `deno --version`, as in "deno 2.9.8 (stable, release, x86_64-pc-windows-msvc)".
_VERSION_LINE = re.compile(r"^deno (\d+(?:\.\d+)*)", re.MULTILINE)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Deno:
    """A Deno that YouTube can use."""

    path: Path
    version: str
    managed: bool  # downloaded by sc-mp3, rather than found on this computer


# Finds the Deno on this computer outside sc-mp3's folders, or None.
Locator = Callable[[], Path | None]
# Reads a Deno binary's version, or None when it doesn't run.
VersionReader = Callable[[Path], str | None]


def find_deno(
    components: Components | None = None,
    *,
    frozen: bool | None = None,
    locate: Locator | None = None,
    read_version: VersionReader | None = None,
) -> Deno | None:
    """The Deno for YouTube, or None when YouTube support isn't there yet.

    Every Deno must run `deno --version`, since yt-dlp quietly does without one that won't.
    `frozen` defaults to whether this is the packaged app, which counts only its own copy.
    """
    components = components or Components()
    read = read_version or read_deno_version
    managed = downloaded_deno(components, read)
    if managed is not None:
        return managed
    if is_frozen() if frozen is None else frozen:
        return None
    binary = (locate or locate_system_deno)()
    if binary is None:
        return None
    version = read(binary)
    if version is None or not is_supported(version):
        # Full paths go to DEBUG only.
        log.info("The Deno on this computer is %s, so it doesn't count", version or "unusable")
        log.debug("That Deno is at %s", binary)
        return None
    return Deno(binary, version, managed=False)


def downloaded_deno(components: Components, read_version: VersionReader) -> Deno | None:
    """The Deno sc-mp3 downloaded, if it's there and runs."""
    manifest = components.active(DENO)
    if manifest is None:
        return None
    binary = components.file_path(manifest)
    if read_version(binary) is None:
        log.warning(
            "The downloaded Deno %s doesn't run, so YouTube support is missing", manifest.version
        )
        return None
    return Deno(binary, manifest.version, managed=True)


def forget_broken_copy(components: Components, read_version: VersionReader | None = None) -> None:
    """Mark a downloaded Deno that doesn't run as bad, so installing YouTube support replaces it."""
    manifest = components.active(DENO)
    if manifest is None or (read_version or read_deno_version)(components.file_path(manifest)):
        return
    version_dir = components.file_path(manifest).parent
    write_manifest(version_dir, replace(manifest, status="bad"))


def is_frozen() -> bool:
    """Whether this is the packaged app rather than a uv install."""
    return bool(getattr(sys, "frozen", False))


def locate_system_deno() -> Path | None:
    """Deno where yt-dlp looks too: Python's scripts folder, where the extra puts it, then PATH."""
    scripts = Path(sysconfig.get_path("scripts"))
    beside_python = scripts / f"deno{sysconfig.get_config_var('EXE') or ''}"
    if beside_python.is_file():
        return beside_python
    found = shutil.which("deno")
    return Path(found) if found else None


def is_supported(version: str) -> bool:
    """Whether a Deno version is one yt-dlp accepts."""
    numbers = tuple(int(part) for part in version.split("."))
    return numbers >= MIN_VERSION


def read_deno_version(binary: Path) -> str | None:
    """The version `deno --version` prints, remembered while the file stays the same."""
    try:
        stat = binary.stat()
    except OSError as error:
        log.info("Can't read the Deno on this computer: %s", error.strerror or type(error).__name__)
        return None
    return _version_of(str(binary), stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=8)
def _version_of(binary: str, mtime_ns: int, size: int) -> str | None:
    """Run `deno --version` once per binary and modification, since each run takes a moment."""
    # No console window flashes up on Windows.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=VERSION_TIMEOUT,
            creationflags=flags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log.info("The Deno on this computer didn't run: %s", type(error).__name__)
        return None
    found = _VERSION_LINE.search(done.stdout.decode("utf-8", errors="replace"))
    if done.returncode != 0 or found is None:
        log.info("The Deno on this computer didn't say its version")
        return None
    return found[1]
