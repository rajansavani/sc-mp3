"""Read a lock of pinned binaries, like packaging/binaries.lock, into one Pin per target.

packaging/fetch_binaries.py fetches what this reads. Standard library only.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# packaging/ is on sys.path when a script in it runs.
from binary_archives import MEMBER_OPENERS, archive_suffix

REPO_ROOT = Path(__file__).resolve().parents[1]

OS_CHOICES = ("windows", "macos", "linux")
ARCH_CHOICES = ("x64", "arm64")

REQUIRED_FIELDS = ("url", "sha256", "archive_member", "version", "license", "source_url")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMPONENT_PATTERN = re.compile(r"[a-z0-9_-]+")
# A plain file name, so no backslash, drive or ".." that Windows would read as a path.
BINARY_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class FetchError(Exception):
    """A pinned binary couldn't be read from the lock, downloaded, verified or extracted."""


@dataclass(frozen=True)
class Pin:
    """One binary pinned in a lock, for one OS and architecture."""

    component: str
    os_name: str
    arch: str
    url: str
    sha256: str
    archive_member: str
    version: str
    license: str
    source_url: str
    # Taken from the URL, like ".zip"; it picks how the archive is read.
    archive_suffix: str
    # The lock the pin came from, as lock_label gives it, for errors that say what to fix.
    lock: str

    @property
    def target(self) -> str:
        return f"{self.os_name}-{self.arch}"

    @property
    def label(self) -> str:
        return f"{self.component} {self.version} for {self.target}"

    @property
    def archive_name(self) -> str:
        # Named after the component, so a version bump overwrites the old archive.
        return self.component + self.archive_suffix

    @property
    def binary_name(self) -> str:
        return PurePosixPath(self.archive_member).name


def load_pins(lock_path: Path) -> list[Pin]:
    """Read every entry in the lock file, checking that each has the fields fetching needs."""
    try:
        with lock_path.open("rb") as lock_file:
            lock = tomllib.load(lock_file)
    except OSError as error:
        raise FetchError(f"Couldn't read {lock_path}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise FetchError(f"{lock_path} isn't valid TOML: {error}") from error

    pins = []
    for component, targets in lock.items():
        if not isinstance(targets, dict):
            raise FetchError(f"{component} in {lock_path.name} should be a table of targets.")
        for target, entry in targets.items():
            pins.append(parse_pin(component, target, entry, lock_path))
    return pins


def lock_label(lock_path: Path) -> str:
    """The lock's path from the repo's root, like packaging/binaries.lock, or in full outside it."""
    try:
        return lock_path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(lock_path)


def parse_pin(component: str, target: str, entry: object, lock_path: Path) -> Pin:
    """Turn one [<component>.<os>-<arch>] table into a Pin, or explain what's wrong with it."""
    where = f"[{component}.{target}] in {lock_path.name}"
    # The component names the archive file, so it mustn't be able to point outside the folder.
    if not COMPONENT_PATTERN.fullmatch(component):
        raise FetchError(f"{where}: use lowercase letters, digits, - and _ in component names.")
    os_name, _, arch = target.partition("-")
    if os_name not in OS_CHOICES or arch not in ARCH_CHOICES:
        raise FetchError(f"{where} should be named <os>-<arch>, like windows-x64.")
    if not isinstance(entry, dict):
        raise FetchError(f"{where} should be a table of fields.")

    missing = [name for name in REQUIRED_FIELDS if not is_text(entry.get(name))]
    if missing:
        raise FetchError(f"{where} needs a text value for {', '.join(missing)}.")
    if not SHA256_PATTERN.fullmatch(entry["sha256"]):
        raise FetchError(f"{where}: sha256 should be 64 lowercase hex characters.")
    if not BINARY_NAME_PATTERN.fullmatch(PurePosixPath(entry["archive_member"]).name):
        raise FetchError(
            f"{where}: archive_member should end in a plain file name, like bin/ffmpeg.exe."
        )
    suffix = archive_suffix(entry["url"])
    if suffix is None:
        known = ", ".join(MEMBER_OPENERS)
        raise FetchError(f"{where}: url should end in one of {known}.")

    fields = {name: entry[name] for name in REQUIRED_FIELDS}
    return Pin(
        component=component,
        os_name=os_name,
        arch=arch,
        archive_suffix=suffix,
        lock=lock_label(lock_path),
        **fields,
    )


def is_text(value: object) -> bool:
    return isinstance(value, str) and value != ""
