"""Downloads that must match a published SHA-256, the checksum files, and unzipping one file.

Nothing here runs what it downloads, and only the one file asked for leaves a zip. The HTTP
helpers these use, such as `open_url` and `network_errors`, are in `net.py`.
"""

import codecs
import hashlib
import logging
import os
import re
import shutil
import stat
import zipfile
import zlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import BinaryIO

from scmp3.core import net
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import safe_url
from scmp3.core.net import Opener

CHUNK_SIZE = 256 * 1024
# Far above any component's size, so a runaway download can't fill the disk.
MAX_DOWNLOAD_BYTES = 500_000_000
# A checksum file is a few lines long.
MAX_CHECKSUMS_BYTES = 1_000_000

SHA256_HEX = re.compile(r"[0-9A-Fa-f]{64}")
# A sha256sum line: the hash, a space, then a space or a * for binary mode, then the name.
SHA256SUM_LINE = re.compile(r"(?P<hash>[0-9A-Fa-f]{64}) [ *](?P<name>.+)")
# A line of PowerShell's Get-FileHash list, such as "Hash      : A0C3…".
FIELD_LINE = re.compile(r"(?P<key>\w+)\s*:\s*(?P<value>.*)")

# Called with the bytes downloaded so far and the total, when the server says it.
type DownloadProgress = Callable[[int, int | None], None]

log = logging.getLogger(__name__)


def download_verified(
    url: str,
    sha256: str,
    dest: Path,
    *,
    opener: Opener,
    progress: DownloadProgress | None = None,
) -> None:
    """Stream `url` into `dest`, raising ScmpError(bad_download) unless its SHA-256 is `sha256`.

    The caller deletes `dest` after a failure, since it's in a temp folder of the caller's.
    """
    actual = _download_to(url, dest, opener, progress)
    if actual != sha256.lower():
        detail = f"the download from {safe_url(url)} has SHA-256 {actual}, not {sha256.lower()}"
        raise ScmpError(ErrorKind.bad_download, detail)


def _download_to(url: str, dest: Path, opener: Opener, progress: DownloadProgress | None) -> str:
    """Stream a URL into a file and return the SHA-256 of what arrived."""
    _require_https(url)
    with net.network_errors(url):
        response = opener(url, {})
    with response:
        if response.status != 200:
            raise net.status_error(response.status, url)
        total = _content_length(response.headers.get("Content-Length"))
        digest = hashlib.sha256()
        done = 0
        with disk_errors(), dest.open("wb") as out:
            while chunk := _read_chunk(response, url):
                done += len(chunk)
                if done > MAX_DOWNLOAD_BYTES:
                    detail = f"{safe_url(url)} sent over {MAX_DOWNLOAD_BYTES} bytes"
                    raise ScmpError(ErrorKind.bad_download, detail)
                digest.update(chunk)
                out.write(chunk)
                if progress is not None:
                    progress(done, total)
    return digest.hexdigest()


def _content_length(text: str | None) -> int | None:
    """The size the server announced, or None when it didn't."""
    if text is None or not text.isdigit():
        return None
    return int(text)


def _read_chunk(response: net.Response, url: str) -> bytes:
    with net.network_errors(url):
        return response.read(CHUNK_SIZE)


def _require_https(url: str) -> None:
    """Refuse a plain-http link, since a download or its checksum file is only as safe as that."""
    if not url.startswith("https://"):
        raise ScmpError(ErrorKind.bad_download, f"won't download {safe_url(url)} without https")


def fetch_checksums(url: str, *, opener: Opener) -> dict[str, str]:
    """Download a checksum file and return each file's SHA-256 in it, by file name."""
    _require_https(url)
    with net.network_errors(url):
        response = opener(url, {})
    with response:
        if response.status != 200:
            raise net.status_error(response.status, url)
        body = net.read_capped(response, url, MAX_CHECKSUMS_BYTES)
    return parse_checksums(_decode(body))


def _decode(body: bytes) -> str:
    """A checksum file's text. PowerShell may have written it as UTF-16 or with a BOM."""
    # Damaged text decodes with replacement characters, which no checksum line matches.
    if body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return body.decode("utf-16", errors="replace")
    return body.decode("utf-8-sig", errors="replace")


def parse_checksums(text: str) -> dict[str, str]:
    """Each file's SHA-256 in a checksum file's text, by file name, in lowercase hex.

    Reads sha256sum lines, as yt-dlp and Deno's macOS and Linux files use, and the list that
    PowerShell's Get-FileHash prints, as Deno's Windows files use. A name listed with two
    different hashes is left out, since neither can be trusted.
    """
    found: list[tuple[str, str]] = []
    fields: dict[str, str] = {}
    # The empty line at the end closes the last Get-FileHash record.
    for raw_line in [*text.splitlines(), ""]:
        line = raw_line.strip()
        sha256sum = SHA256SUM_LINE.fullmatch(line)
        field_line = FIELD_LINE.fullmatch(line)
        if sha256sum:
            found.append((_file_name(sha256sum["name"]), sha256sum["hash"].lower()))
        elif field_line:
            # A key seen again starts the next record, even with no blank line before it.
            if field_line["key"] in fields:
                found.extend(_get_filehash_record(fields))
                fields = {}
            fields[field_line["key"]] = field_line["value"].strip()
        elif not line:
            found.extend(_get_filehash_record(fields))
            fields = {}
    return _unambiguous(found)


def _get_filehash_record(fields: Mapping[str, str]) -> list[tuple[str, str]]:
    """The file name and hash in one Get-FileHash record, if it's a complete SHA-256 one."""
    file_hash = fields.get("Hash", "")
    path = fields.get("Path")
    if path is None or not SHA256_HEX.fullmatch(file_hash):
        return []
    if fields.get("Algorithm", "SHA256").upper() != "SHA256":
        return []
    return [(_file_name(path), file_hash.lower())]


def _unambiguous(found: list[tuple[str, str]]) -> dict[str, str]:
    """Each name's hash, leaving out the names listed with more than one hash."""
    hashes: dict[str, set[str]] = {}
    for name, file_hash in found:
        hashes.setdefault(name, set()).add(file_hash)
    checksums: dict[str, str] = {}
    for name, values in hashes.items():
        if len(values) > 1:
            log.warning("The checksum file lists %s with %d different hashes", name, len(values))
            continue
        checksums[name] = values.pop()
    return checksums


def _file_name(path: str) -> str:
    """The last part of a path written with / or \\, as checksum files name their files."""
    return PureWindowsPath(path).name


def extract_member(archive: Path, member: str, dest: Path) -> None:
    """Copy one regular file out of a zip to `dest`. Nothing else in the zip is written."""
    try:
        with disk_errors(), zipfile.ZipFile(archive) as zip_file:
            try:
                info = zip_file.getinfo(member)
            except KeyError:
                raise ScmpError(ErrorKind.bad_download, f"the download has no {member}") from None
            # A symlink stores its target as its content, so it's refused like a folder.
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                detail = f"{member} in the download isn't a regular file"
                raise ScmpError(ErrorKind.bad_download, detail)
            with zip_file.open(info) as source, dest.open("wb") as out:
                shutil.copyfileobj(source, out, CHUNK_SIZE)
                _sync(out)
    except (zipfile.BadZipFile, zlib.error, EOFError) as error:
        detail = f"the download isn't a readable zip: {error}"
        raise ScmpError(ErrorKind.bad_download, detail) from error


def sync_file(path: Path) -> None:
    """Make sure a file's bytes are on disk, so a power cut can't leave it empty later."""
    with disk_errors(), path.open("rb+") as file:
        _sync(file)


def _sync(file: BinaryIO) -> None:
    file.flush()
    os.fsync(file.fileno())


def make_executable(path: Path) -> None:
    """Add execute permission for everyone, like `chmod +x`. Windows ignores this."""
    with disk_errors():
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@contextmanager
def disk_errors() -> Iterator[None]:
    """Turn a failure to write a downloaded file into ScmpError(disk)."""
    try:
        yield
    except OSError as error:
        detail = f"can't write in the components folder: {os_reason(error)}"
        raise ScmpError(ErrorKind.disk, detail) from error


def os_reason(error: BaseException) -> str:
    """Why an OS call failed, without the full path its message holds."""
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return type(error).__name__
