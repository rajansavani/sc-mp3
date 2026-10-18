"""Download the binaries pinned in binaries.lock, check their SHA-256 and extract them.

Usage: python packaging/fetch_binaries.py [--os windows|macos|linux] [--arch x64|arm64]
Each binary lands in packaging/bin/<os>-<arch>/ next to its verified archive, which is kept so
a second run can skip the download. It uses only the standard library.
"""

import argparse
import hashlib
import http.client
import os
import platform
import re
import shutil
import stat
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from urllib.parse import urlsplit

PACKAGING_DIR = Path(__file__).resolve().parent
LOCK_PATH = PACKAGING_DIR / "binaries.lock"
BIN_DIR = PACKAGING_DIR / "bin"

OS_CHOICES = ("windows", "macos", "linux")
ARCH_CHOICES = ("x64", "arm64")
# What platform.system() and platform.machine() report, mapped to the names the lock uses.
OS_BY_SYSTEM = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}
ARCH_BY_MACHINE = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}

REQUIRED_FIELDS = ("url", "sha256", "archive_member", "version", "license", "source_url")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMPONENT_PATTERN = re.compile(r"[a-z0-9_-]+")
# A plain file name, so no backslash, drive or ".." that Windows would read as a path.
BINARY_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
CHUNK_SIZE = 1024 * 1024
# How long one read may stall before the download fails, so a dead server can't hang CI.
TIMEOUT_SECONDS = 60
USER_AGENT = "sc-mp3-fetch-binaries"
# Where the system supplies ffmpeg, so an empty fetch is expected; anywhere else it's an error.
SYSTEM_PROVIDED = {"linux": "On Linux, ffmpeg comes from the system package manager."}

type UrlOpener = Callable[[str], BinaryIO]
type MemberOpener = Callable[[Path, str], AbstractContextManager[tuple[BinaryIO, int]]]


class FetchError(Exception):
    """A pinned binary couldn't be downloaded, verified or extracted."""


class Action(StrEnum):
    """What fetching one binary did, worded for the summary."""

    DOWNLOADED = "downloaded and verified"
    EXTRACTED = "extracted again from the verified archive"
    UP_TO_DATE = "already up to date"


@dataclass(frozen=True)
class Pin:
    """One binary pinned in binaries.lock, for one OS and architecture."""

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


@dataclass(frozen=True)
class Fetched:
    """The result of fetching one pin."""

    pin: Pin
    binary: Path
    action: Action


@contextmanager
def open_zip_member(archive: Path, member: str) -> Iterator[tuple[BinaryIO, int]]:
    """Open one file inside a zip archive, yielding the stream and its unpacked size."""
    with zipfile.ZipFile(archive) as zip_file:
        try:
            info = zip_file.getinfo(member)
        except KeyError:
            raise missing_member_error(archive, member) from None
        # Zip archives made on Unix can hold symlinks, which store the target path as content.
        if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
            raise not_a_file_error(archive, member)
        with zip_file.open(info) as stream:
            yield stream, info.file_size


@contextmanager
def open_tar_member(archive: Path, member: str) -> Iterator[tuple[BinaryIO, int]]:
    """Open one file inside a tar archive (any compression), yielding the stream and size."""
    with tarfile.open(archive) as tar_file:
        try:
            info = tar_file.getmember(member)
        except KeyError:
            raise missing_member_error(archive, member) from None
        stream = tar_file.extractfile(info) if info.isfile() else None
        if stream is None:
            raise not_a_file_error(archive, member)
        with stream:
            yield stream, info.size


# Supported archive types, keyed by the suffix the download URL ends in.
MEMBER_OPENERS: dict[str, MemberOpener] = {
    ".zip": open_zip_member,
    ".tar.gz": open_tar_member,
    ".tgz": open_tar_member,
    ".tar.xz": open_tar_member,
}


def archive_suffix(url: str) -> str | None:
    """The archive suffix the URL's path ends in, or None when it isn't a known type."""
    path = urlsplit(url).path.lower()
    return next((suffix for suffix in MEMBER_OPENERS if path.endswith(suffix)), None)


def missing_member_error(archive: Path, member: str) -> FetchError:
    return FetchError(
        f"{archive.name} has no file named {member}. "
        "Check archive_member in packaging/binaries.lock."
    )


def not_a_file_error(archive: Path, member: str) -> FetchError:
    return FetchError(
        f"{member} in {archive.name} isn't a regular file. "
        "Point archive_member in packaging/binaries.lock at the binary itself."
    )


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
            pins.append(parse_pin(component, target, entry, lock_path.name))
    return pins


def parse_pin(component: str, target: str, entry: object, lock_name: str) -> Pin:
    """Turn one [<component>.<os>-<arch>] table into a Pin, or explain what's wrong with it."""
    where = f"[{component}.{target}] in {lock_name}"
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
    return Pin(component=component, os_name=os_name, arch=arch, archive_suffix=suffix, **fields)


def is_text(value: object) -> bool:
    return isinstance(value, str) and value != ""


def sha256_of_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_of_file(path: Path) -> str:
    with path.open("rb") as file:
        return sha256_of_stream(file)


def partial_path(dest: Path) -> Path:
    """Where dest is written before it's complete; renaming it into place is atomic."""
    return dest.with_name(dest.name + ".partial")


def download_verified(pin: Pin, dest: Path, open_url: UrlOpener) -> None:
    """Download the pin's archive to dest, keeping it only if its SHA-256 matches the lock."""
    partial = partial_path(dest)
    try:
        actual = download_to(pin, partial, open_url)
        if actual != pin.sha256:
            raise FetchError(
                f"SHA-256 mismatch: the download of {pin.label} doesn't match binaries.lock, "
                "so it was deleted.\n"
                f"  url:                   {pin.url}\n"
                f"  binaries.lock expects: {pin.sha256}\n"
                f"  the download has:      {actual}\n"
                "Check url and sha256 in packaging/binaries.lock."
            )
        os.replace(partial, dest)
    finally:
        # After a successful rename there's nothing here; after any failure this removes the rest.
        partial.unlink(missing_ok=True)


def download_to(pin: Pin, path: Path, open_url: UrlOpener) -> str:
    """Stream the pin's URL into path, returning the SHA-256 of what arrived."""
    digest = hashlib.sha256()
    # Only the network calls are wrapped, so a full disk is reported as a write error instead.
    with network_errors(pin):
        response = open_url(pin.url)
    with response, path.open("wb") as out:
        while True:
            with network_errors(pin):
                chunk = response.read(CHUNK_SIZE)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
            out.write(chunk)


@contextmanager
def network_errors(pin: Pin) -> Iterator[None]:
    """Turn a failed connection or read into a FetchError naming the pin and its URL."""
    try:
        yield
    except (OSError, http.client.HTTPException) as error:
        raise FetchError(f"Couldn't download {pin.label} from {pin.url}: {error}") from error


@contextmanager
def open_member(pin: Pin, archive: Path) -> Iterator[tuple[BinaryIO, int]]:
    """Open the pin's binary inside its verified archive."""
    opener = MEMBER_OPENERS[pin.archive_suffix]
    try:
        with opener(archive, pin.archive_member) as opened:
            yield opened
    except (zipfile.BadZipFile, tarfile.TarError) as error:
        raise FetchError(f"Couldn't read {archive.name} for {pin.label}: {error}") from error


def binary_matches(pin: Pin, archive: Path, binary: Path) -> bool:
    """Whether the binary on disk is byte for byte the one inside the archive."""
    with open_member(pin, archive) as (stream, size):
        if binary.stat().st_size != size:
            return False
        return sha256_of_stream(stream) == sha256_of_file(binary)


def extract_binary(pin: Pin, archive: Path, dest: Path) -> None:
    """Copy only the pin's binary out of the archive, marking it executable for macOS and Linux."""
    partial = partial_path(dest)
    try:
        with open_member(pin, archive) as (stream, _size), partial.open("wb") as out:
            shutil.copyfileobj(stream, out, CHUNK_SIZE)
        if pin.os_name != "windows":
            make_executable(partial)
        os.replace(partial, dest)
    finally:
        partial.unlink(missing_ok=True)


def make_executable(path: Path) -> None:
    """Add execute permission for everyone, like `chmod +x`. Windows ignores this."""
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def log_progress(message: str) -> None:
    # Flushed, so CI logs show what's downloading before a long wait or an error.
    print(message, flush=True)


def fetch_pin(
    pin: Pin, target_dir: Path, open_url: UrlOpener, log: Callable[[str], None] = log_progress
) -> Fetched:
    """Make target_dir hold the pin's verified archive and its binary, doing only what's missing."""
    archive = target_dir / pin.archive_name
    binary = target_dir / pin.binary_name

    downloaded = False
    if not (archive.is_file() and sha256_of_file(archive) == pin.sha256):
        # Drop files from an older pin first, so a failed download leaves nothing behind.
        archive.unlink(missing_ok=True)
        binary.unlink(missing_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        log(f"Downloading {pin.label} from {pin.url}")
        download_verified(pin, archive, open_url)
        downloaded = True

    if binary.is_file() and binary_matches(pin, archive, binary):
        return Fetched(pin, binary, Action.UP_TO_DATE)

    extract_binary(pin, archive, binary)
    return Fetched(pin, binary, Action.DOWNLOADED if downloaded else Action.EXTRACTED)


def fetch_target(
    pins: Sequence[Pin], os_name: str, arch: str, target_dir: Path, open_url: UrlOpener
) -> list[Fetched]:
    """Fetch every pin for one OS and architecture into target_dir."""
    results = []
    for pin in pins:
        if pin.os_name != os_name or pin.arch != arch:
            continue
        try:
            results.append(fetch_pin(pin, target_dir, open_url))
        except OSError as error:
            # For example a full disk, or ffmpeg.exe still running so Windows won't replace it.
            raise FetchError(f"Couldn't write {pin.label} to {target_dir}: {error}") from error
    return results


def unpinned_error(target: str, pins: Sequence[Pin], lock_name: str) -> FetchError:
    pinned = ", ".join(sorted({pin.target for pin in pins})) or "none"
    return FetchError(
        f"Nothing is pinned for {target} in {lock_name}, so a release build for it would have "
        f"no ffmpeg. Pin it there, or use a pinned target: {pinned}."
    )


def format_summary(target_dir: Path, results: Sequence[Fetched]) -> str:
    """One line per binary: what it is, its size and what this run did."""
    lines = [f"{target_dir}:"]
    for result in results:
        pin = result.pin
        size_mb = result.binary.stat().st_size / 1_000_000
        lines.append(
            f"  {pin.component} {pin.version} ({pin.license}): {result.binary.name}, "
            f"{size_mb:.1f} MB, {result.action}"
        )
    return "\n".join(lines)


def open_download(url: str) -> BinaryIO:
    """Open a URL for streaming, the default way to fetch archives."""
    # ffmpeg.martin-riedl.de answers 403 to urllib's default User-Agent, so send our own.
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)


def host_target(system: str, machine: str) -> tuple[str | None, str | None]:
    """This machine's OS and architecture in the lock's names, or None where it's unknown."""
    return OS_BY_SYSTEM.get(system), ARCH_BY_MACHINE.get(machine.lower())


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Read --os and --arch, defaulting to this machine's."""
    default_os, default_arch = host_target(platform.system(), platform.machine())
    parser = argparse.ArgumentParser(description="Download and verify the pinned binaries.")
    parser.add_argument("--os", choices=OS_CHOICES, default=default_os, help="default: this OS")
    parser.add_argument(
        "--arch", choices=ARCH_CHOICES, default=default_arch, help="default: this machine's"
    )
    args = parser.parse_args(argv)
    if args.os is None:
        parser.error(f"Couldn't tell which OS {platform.system()!r} is. Pass --os.")
    if args.arch is None:
        parser.error(f"Couldn't tell which architecture {platform.machine()!r} is. Pass --arch.")
    return args


def main(
    argv: Sequence[str] | None = None,
    *,
    lock_path: Path = LOCK_PATH,
    bin_dir: Path = BIN_DIR,
    open_url: UrlOpener = open_download,
) -> int:
    """Fetch the binaries for one OS and architecture, returning the exit code."""
    args = parse_args(argv)
    target = f"{args.os}-{args.arch}"
    target_dir = bin_dir / target
    try:
        pins = load_pins(lock_path)
        results = fetch_target(pins, args.os, args.arch, target_dir, open_url)
        if not results and args.os not in SYSTEM_PROVIDED:
            raise unpinned_error(target, pins, lock_path.name)
    except FetchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if not results:
        print(f"Nothing is pinned for {target} in {lock_path.name}. {SYSTEM_PROVIDED[args.os]}")
        return 0
    print(format_summary(target_dir, results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
