"""Download the binaries pinned in binaries.lock, check their SHA-256 and extract them.

Usage: python packaging/fetch_binaries.py [--os windows|macos|linux] [--arch x64|arm64]
    [--lock <file>] [--bin-dir <folder>]
Each binary lands in packaging/bin/<os>-<arch>/ next to its verified archive, which is kept so
a second run can skip the download. --lock and --bin-dir read another lock into another folder,
as ffmpeg.yml does for packaging/ffmpeg/reference.lock. It uses only the standard library.
binaries_lock.py reads the lock, and binary_archives.py opens the archives.
"""

import argparse
import hashlib
import http.client
import os
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

# packaging/ is on sys.path when this file runs as a script.
from binaries_lock import ARCH_CHOICES, OS_CHOICES, FetchError, Pin, load_pins
from binary_archives import MEMBER_OPENERS, ArchiveError

PACKAGING_DIR = Path(__file__).resolve().parent
LOCK_PATH = PACKAGING_DIR / "binaries.lock"
BIN_DIR = PACKAGING_DIR / "bin"

# What platform.system() and platform.machine() report, mapped to the names the lock uses.
OS_BY_SYSTEM = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}
ARCH_BY_MACHINE = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}

CHUNK_SIZE = 1024 * 1024
# How long one read may stall before the download fails, so a dead server can't hang CI.
TIMEOUT_SECONDS = 60
USER_AGENT = "sc-mp3-fetch-binaries"
# Where the system supplies ffmpeg, so an empty fetch is expected; anywhere else it's an error.
SYSTEM_PROVIDED = {"linux": "On Linux, ffmpeg comes from the system package manager."}

type UrlOpener = Callable[[str], BinaryIO]


class Action(StrEnum):
    """What fetching one binary did, worded for the summary."""

    DOWNLOADED = "downloaded and verified"
    EXTRACTED = "extracted again from the verified archive"
    UP_TO_DATE = "already up to date"


@dataclass(frozen=True)
class Fetched:
    """The result of fetching one pin."""

    pin: Pin
    binary: Path
    action: Action


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
                f"SHA-256 mismatch: the download of {pin.label} doesn't match {pin.lock}, "
                "so it was deleted.\n"
                f"  url:              {pin.url}\n"
                f"  the lock expects: {pin.sha256}\n"
                f"  the download has: {actual}\n"
                f"Check url and sha256 in {pin.lock}."
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
    except ArchiveError as error:
        # Its message names the archive and the member, and this adds where to fix it.
        raise FetchError(f"{error} Check archive_member in {pin.lock}.") from error
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


def parse_args(
    argv: Sequence[str] | None, lock_path: Path = LOCK_PATH, bin_dir: Path = BIN_DIR
) -> argparse.Namespace:
    """Read --os and --arch, defaulting to this machine's, and --lock and --bin-dir."""
    default_os, default_arch = host_target(platform.system(), platform.machine())
    parser = argparse.ArgumentParser(description="Download and verify the pinned binaries.")
    parser.add_argument("--os", choices=OS_CHOICES, default=default_os, help="default: this OS")
    parser.add_argument(
        "--arch", choices=ARCH_CHOICES, default=default_arch, help="default: this machine's"
    )
    parser.add_argument(
        "--lock", type=Path, default=lock_path, help="default: packaging/binaries.lock"
    )
    parser.add_argument(
        "--bin-dir", type=Path, default=bin_dir, help="where each <os>-<arch> folder goes"
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
    """Fetch the binaries for one OS and architecture, returning the exit code.

    lock_path and bin_dir are the defaults for --lock and --bin-dir.
    """
    args = parse_args(argv, lock_path, bin_dir)
    target = f"{args.os}-{args.arch}"
    target_dir = args.bin_dir / target
    try:
        pins = load_pins(args.lock)
        results = fetch_target(pins, args.os, args.arch, target_dir, open_url)
        if not results and args.os not in SYSTEM_PROVIDED:
            raise unpinned_error(target, pins, args.lock.name)
    except FetchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if not results:
        print(f"Nothing is pinned for {target} in {args.lock.name}. {SYSTEM_PROVIDED[args.os]}")
        return 0
    print(format_summary(target_dir, results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
