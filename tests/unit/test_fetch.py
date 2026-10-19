"""Tests for verified downloads, checksum files and unzipping one file, with a fake HTTP layer."""

import codecs
import hashlib
import io
import stat
import sys
import urllib.error
import zipfile
from collections.abc import Mapping
from email.message import Message
from pathlib import Path

import pytest

from scmp3.core import fetch
from scmp3.core.errors import ErrorKind, ScmpError

URL = "https://github.com/denoland/deno/releases/download/v2.9.7/deno-x86_64-pc-windows-msvc.zip"
SUMS_URL = URL + ".sha256sum"
BODY = b"pretend this is a zip" * 1000

# Deno 2.9.7's checksum files, byte for byte: PowerShell's Get-FileHash list on Windows, and
# sha256sum's output on macOS and Linux.
WINDOWS_SUMS = (
    b"\r\nAlgorithm : SHA256\r\n"
    b"Hash      : A0C3101B4158D1DFB7D6A78A7BF0F3DE80C96BB423C152BEEC8BEB22786F2238\r\n"
    b"Path      : C:\\a\\deno\\deno\\target\\release\\deno-x86_64-pc-windows-msvc.zip\r\n\r\n"
)
LINUX_SUMS = (
    b"c6527f24f4b16031d3ae4fa9f658d5f11534c8d84ce7dc8502420280919c3490  "
    b"deno-x86_64-unknown-linux-gnu.zip\n"
)
WINDOWS_SHA = "a0c3101b4158d1dfb7d6a78a7bf0f3de80c96bb423c152beec8beb22786f2238"
LINUX_SHA = "c6527f24f4b16031d3ae4fa9f658d5f11534c8d84ce7dc8502420280919c3490"


class FakeResponse(io.BytesIO):
    """A response shaped like urllib's: a status, email-style headers and a readable body."""

    def __init__(
        self, body: bytes = b"", status: int = 200, headers: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value


class BreaksMidway(FakeResponse):
    """A response whose connection drops after the first read."""

    def read(self, size: int | None = -1, /) -> bytes:
        if self.tell() > 0:
            raise ConnectionResetError("connection reset")
        return super().read(size)


class FakeWeb:
    """Canned responses by URL, and every URL asked for."""

    def __init__(self, responses: Mapping[str, FakeResponse] | None = None) -> None:
        self.responses = dict(responses or {})
        self.requests: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> FakeResponse:
        self.requests.append(url)
        if url not in self.responses:
            raise AssertionError(f"unexpected request for {url}")
        return self.responses[url]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_zip(path: Path, files: Mapping[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


# parse_checksums


def test_deno_windows_checksum_files_are_read() -> None:
    text = WINDOWS_SUMS.decode()
    assert fetch.parse_checksums(text) == {"deno-x86_64-pc-windows-msvc.zip": WINDOWS_SHA}


def test_deno_linux_and_macos_checksum_files_are_read() -> None:
    text = LINUX_SUMS.decode()
    assert fetch.parse_checksums(text) == {"deno-x86_64-unknown-linux-gnu.zip": LINUX_SHA}


def test_a_sums_file_with_many_files_lists_each_one() -> None:
    # The shape of yt-dlp's SHA2-256SUMS, with one name in binary mode.
    text = f"{'a' * 64}  yt-dlp\n{'B' * 64} *yt-dlp.exe\n{'c' * 64}  yt-dlp_macos\n"
    assert fetch.parse_checksums(text) == {
        "yt-dlp": "a" * 64,
        "yt-dlp.exe": "b" * 64,
        "yt-dlp_macos": "c" * 64,
    }


def test_several_get_filehash_records_are_each_read() -> None:
    text = (
        f"Algorithm : SHA256\nHash : {'A' * 64}\nPath : C:\\out\\one.zip\n\n"
        f"Algorithm : SHA256\nHash : {'B' * 64}\nPath : C:\\out\\two.zip\n"
    )
    assert fetch.parse_checksums(text) == {"one.zip": "a" * 64, "two.zip": "b" * 64}


def test_names_with_folders_are_reduced_to_the_file_name() -> None:
    assert fetch.parse_checksums(f"{'a' * 64}  ./dist/deno.zip") == {"deno.zip": "a" * 64}


def test_other_algorithms_and_junk_lines_are_ignored() -> None:
    text = (
        f"Algorithm : SHA512\nHash : {'a' * 128}\nPath : C:\\x.zip\n\n"
        f"Algorithm : SHA512\nHash : {'a' * 64}\nPath : C:\\y.zip\n\n"
        "not a checksum\n"
        f"{'a' * 63}  short.zip\n"
    )
    assert fetch.parse_checksums(text) == {}


def test_a_get_filehash_record_without_a_path_is_ignored() -> None:
    assert fetch.parse_checksums(f"Algorithm : SHA256\nHash : {'a' * 64}\n") == {}


def test_get_filehash_records_with_no_blank_line_between_are_kept_apart() -> None:
    text = (
        f"Algorithm : SHA256\nHash : {'A' * 64}\nPath : C:\\out\\one.zip\n"
        f"Algorithm : SHA256\nHash : {'B' * 64}\nPath : C:\\out\\two.zip\n"
    )
    assert fetch.parse_checksums(text) == {"one.zip": "a" * 64, "two.zip": "b" * 64}


def test_a_record_with_only_a_path_doesnt_take_the_hash_before_it() -> None:
    text = f"Hash : {'A' * 64}\nPath : C:\\out\\one.zip\nPath : C:\\out\\two.zip\n"
    assert fetch.parse_checksums(text) == {"one.zip": "a" * 64}


def test_a_name_listed_with_two_hashes_is_left_out() -> None:
    text = f"{'a' * 64}  deno.zip\n{'b' * 64}  other/deno.zip\n{'c' * 64}  yt-dlp\n"
    assert fetch.parse_checksums(text) == {"yt-dlp": "c" * 64}


def test_a_name_listed_twice_with_the_same_hash_is_kept() -> None:
    text = f"{'a' * 64}  deno.zip\nAlgorithm : SHA256\nHash : {'A' * 64}\nPath : C:\\deno.zip\n"
    assert fetch.parse_checksums(text) == {"deno.zip": "a" * 64}


# fetch_checksums


@pytest.mark.parametrize(
    "body",
    [
        LINUX_SUMS,
        b"\xef\xbb\xbf" + LINUX_SUMS,
        codecs.BOM_UTF16_LE + LINUX_SUMS.decode().encode("utf-16-le"),
        codecs.BOM_UTF16_BE + LINUX_SUMS.decode().encode("utf-16-be"),
    ],
    ids=["utf-8", "utf-8 with bom", "utf-16 le", "utf-16 be"],
)
def test_checksum_files_are_read_in_the_encodings_powershell_writes(body: bytes) -> None:
    web = FakeWeb({SUMS_URL: FakeResponse(body)})
    sums = fetch.fetch_checksums(SUMS_URL, opener=web)
    assert sums == {"deno-x86_64-unknown-linux-gnu.zip": LINUX_SHA}


@pytest.mark.parametrize(
    "body",
    [
        codecs.BOM_UTF16_LE + b"a",
        codecs.BOM_UTF16_LE + "\ud800".encode("utf-16-le", "surrogatepass"),
    ],
    ids=["an odd byte count", "a lone surrogate"],
)
def test_damaged_utf16_text_lists_nothing(body: bytes) -> None:
    web = FakeWeb({SUMS_URL: FakeResponse(body)})
    assert fetch.fetch_checksums(SUMS_URL, opener=web) == {}


def test_a_checksum_file_without_https_isnt_fetched() -> None:
    web = FakeWeb()
    with pytest.raises(ScmpError) as raised:
        fetch.fetch_checksums("http://github.com/x/deno.zip.sha256sum", opener=web)
    assert raised.value.kind is ErrorKind.bad_download
    assert web.requests == []


def test_a_missing_checksum_file_is_unavailable() -> None:
    web = FakeWeb({SUMS_URL: FakeResponse(status=404)})
    with pytest.raises(ScmpError) as raised:
        fetch.fetch_checksums(SUMS_URL, opener=web)
    assert raised.value.kind is ErrorKind.unavailable


def test_an_oversized_checksum_file_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "MAX_CHECKSUMS_BYTES", 10)
    web = FakeWeb({SUMS_URL: FakeResponse(LINUX_SUMS)})
    with pytest.raises(ScmpError) as raised:
        fetch.fetch_checksums(SUMS_URL, opener=web)
    assert raised.value.kind is ErrorKind.service_down


# download_verified


def test_a_download_with_the_published_hash_is_kept(tmp_path: Path) -> None:
    dest = tmp_path / "download"
    web = FakeWeb({URL: FakeResponse(BODY)})
    fetch.download_verified(URL, sha256(BODY), dest, opener=web)
    assert dest.read_bytes() == BODY


def test_the_published_hash_may_be_uppercase(tmp_path: Path) -> None:
    web = FakeWeb({URL: FakeResponse(BODY)})
    fetch.download_verified(URL, sha256(BODY).upper(), tmp_path / "download", opener=web)


def test_a_tampered_download_is_refused_as_a_bad_download(tmp_path: Path) -> None:
    tampered = b"X" + BODY[1:]
    web = FakeWeb({URL: FakeResponse(tampered)})
    with pytest.raises(ScmpError) as raised:
        fetch.download_verified(URL, sha256(BODY), tmp_path / "download", opener=web)
    assert raised.value.kind is ErrorKind.bad_download
    assert sha256(tampered) in str(raised.value)
    assert sha256(BODY) in str(raised.value)


def test_progress_reports_the_bytes_so_far_and_the_total(tmp_path: Path) -> None:
    web = FakeWeb({URL: FakeResponse(BODY, headers={"Content-Length": str(len(BODY))})})
    seen: list[tuple[int, int | None]] = []
    fetch.download_verified(
        URL,
        sha256(BODY),
        tmp_path / "download",
        opener=web,
        progress=lambda done, total: seen.append((done, total)),
    )
    assert seen[-1] == (len(BODY), len(BODY))
    assert [done for done, _ in seen] == sorted(done for done, _ in seen)


def test_progress_has_no_total_when_the_server_gives_no_length(tmp_path: Path) -> None:
    web = FakeWeb({URL: FakeResponse(BODY)})
    seen: list[int | None] = []
    fetch.download_verified(
        URL,
        sha256(BODY),
        tmp_path / "download",
        opener=web,
        progress=lambda done, total: seen.append(total),
    )
    assert set(seen) == {None}


def test_only_https_downloads_are_made(tmp_path: Path) -> None:
    web = FakeWeb()
    with pytest.raises(ScmpError) as raised:
        fetch.download_verified("http://example.com/deno.zip", "a" * 64, tmp_path / "x", opener=web)
    assert raised.value.kind is ErrorKind.bad_download
    assert web.requests == []


@pytest.mark.parametrize(
    ("status", "kind"),
    [(404, ErrorKind.unavailable), (429, ErrorKind.rate_limited), (503, ErrorKind.service_down)],
)
def test_error_statuses_become_error_kinds(tmp_path: Path, status: int, kind: ErrorKind) -> None:
    web = FakeWeb({URL: FakeResponse(status=status)})
    with pytest.raises(ScmpError) as raised:
        fetch.download_verified(URL, sha256(BODY), tmp_path / "download", opener=web)
    assert raised.value.kind is kind


def test_a_connection_that_cant_open_is_offline(tmp_path: Path) -> None:
    def no_connection(url: str, headers: Mapping[str, str]) -> FakeResponse:
        raise urllib.error.URLError("getaddrinfo failed")

    with pytest.raises(ScmpError) as raised:
        fetch.download_verified(URL, sha256(BODY), tmp_path / "download", opener=no_connection)
    assert raised.value.kind is ErrorKind.offline


def test_a_connection_that_drops_midway_is_offline(tmp_path: Path) -> None:
    web = FakeWeb({URL: BreaksMidway(BODY)})
    with pytest.raises(ScmpError) as raised:
        fetch.download_verified(URL, sha256(BODY), tmp_path / "download", opener=web)
    assert raised.value.kind is ErrorKind.offline


def test_a_download_far_too_big_is_cut_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "MAX_DOWNLOAD_BYTES", 1000)
    web = FakeWeb({URL: FakeResponse(BODY)})
    with pytest.raises(ScmpError) as raised:
        fetch.download_verified(URL, sha256(BODY), tmp_path / "download", opener=web)
    assert raised.value.kind is ErrorKind.bad_download


def test_a_download_that_cant_be_written_is_a_disk_error(tmp_path: Path) -> None:
    web = FakeWeb({URL: FakeResponse(BODY)})
    with pytest.raises(ScmpError) as raised:
        fetch.download_verified(URL, sha256(BODY), tmp_path / "missing" / "x", opener=web)
    assert raised.value.kind is ErrorKind.disk


# extract_member


def test_only_the_named_file_comes_out_of_the_zip(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "deno.zip", {"deno.exe": b"binary", "evil.exe": b"other"})
    out = tmp_path / "out"
    out.mkdir()
    fetch.extract_member(archive, "deno.exe", out / "deno.exe")
    assert [path.name for path in out.iterdir()] == ["deno.exe"]
    assert (out / "deno.exe").read_bytes() == b"binary"


def test_the_file_taken_out_of_the_zip_is_synced_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = make_zip(tmp_path / "deno.zip", {"deno.exe": b"binary"})
    synced: list[int] = []
    monkeypatch.setattr(fetch.os, "fsync", synced.append)
    fetch.extract_member(archive, "deno.exe", tmp_path / "deno.exe")
    assert len(synced) == 1


def test_sync_file_syncs_a_file_already_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "yt-dlp"
    binary.write_bytes(b"zipapp")
    synced: list[int] = []
    monkeypatch.setattr(fetch.os, "fsync", synced.append)
    fetch.sync_file(binary)
    assert len(synced) == 1
    assert binary.read_bytes() == b"zipapp"


def test_a_zip_without_the_file_is_a_bad_download(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "deno.zip", {"deno": b"binary"})
    with pytest.raises(ScmpError) as raised:
        fetch.extract_member(archive, "deno.exe", tmp_path / "deno.exe")
    assert raised.value.kind is ErrorKind.bad_download
    assert not (tmp_path / "deno.exe").exists()


def test_a_folder_in_the_zip_is_refused(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "deno.zip", {"deno/": b""})
    with pytest.raises(ScmpError) as raised:
        fetch.extract_member(archive, "deno/", tmp_path / "deno")
    assert raised.value.kind is ErrorKind.bad_download


def test_a_symlink_in_the_zip_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "deno.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        link = zipfile.ZipInfo("deno")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zip_file.writestr(link, "/usr/bin/sh")
    with pytest.raises(ScmpError) as raised:
        fetch.extract_member(archive, "deno", tmp_path / "deno")
    assert raised.value.kind is ErrorKind.bad_download


def test_a_file_that_isnt_a_zip_is_a_bad_download(tmp_path: Path) -> None:
    archive = tmp_path / "deno.zip"
    archive.write_bytes(b"<html>not a zip</html>")
    with pytest.raises(ScmpError) as raised:
        fetch.extract_member(archive, "deno", tmp_path / "deno")
    assert raised.value.kind is ErrorKind.bad_download


def test_a_zip_with_damaged_data_is_a_bad_download(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "deno.zip", {"deno": b"binary " * 1000})
    data = bytearray(archive.read_bytes())
    # The compressed data starts right after the 30-byte header and the name.
    start = 30 + len("deno")
    data[start : start + 20] = b"\xff" * 20
    archive.write_bytes(bytes(data))
    with pytest.raises(ScmpError) as raised:
        fetch.extract_member(archive, "deno", tmp_path / "deno")
    assert raised.value.kind is ErrorKind.bad_download


# make_executable


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no execute bits")
def test_make_executable_adds_execute_permission(tmp_path: Path) -> None:
    binary = tmp_path / "deno"
    binary.write_bytes(b"binary")
    binary.chmod(0o644)
    fetch.make_executable(binary)
    assert binary.stat().st_mode & 0o777 == 0o755
