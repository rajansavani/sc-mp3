"""Open one file inside a downloaded zip or tar archive, refusing links and folders.

packaging/fetch_binaries.py extracts each pinned binary through these. Standard library only.
"""

import stat
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

type MemberOpener = Callable[[Path, str], AbstractContextManager[tuple[BinaryIO, int]]]


class ArchiveError(Exception):
    """The archive has no file by that name, or it isn't a regular file. The message says which."""


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


# The caller adds which lock's archive_member to fix, since it knows which lock it read.
def missing_member_error(archive: Path, member: str) -> ArchiveError:
    return ArchiveError(f"{archive.name} has no file named {member}.")


def not_a_file_error(archive: Path, member: str) -> ArchiveError:
    return ArchiveError(f"{member} in {archive.name} isn't a regular file.")
