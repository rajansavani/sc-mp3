"""Tests for packaging/fetch_binaries.py. They build small archives in a temp folder and fetch
them through file:// URLs, so nothing touches the network."""

import errno
import hashlib
import importlib.util
import io
import stat
import sys
import tarfile
import urllib.error
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import IO, Any, BinaryIO

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "fetch_binaries.py"
BINARY = b"pretend ffmpeg build\n" * 1000
MEMBER = "ffmpeg-1.0/bin/ffmpeg.exe"


def load_script() -> ModuleType:
    """Import the script by its path, since packaging/ isn't a package."""
    spec = importlib.util.spec_from_file_location("fetch_binaries", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first, as a normal import would, so dataclasses can find the module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fetch_binaries = load_script()


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def make_tar(path: Path, members: dict[str, bytes], mode: str) -> Path:
    with tarfile.open(path, mode) as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def sha256_hex(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_lock(
    folder: Path,
    archive: Path,
    *,
    target: str = "windows-x64",
    member: str = MEMBER,
    sha256: str | None = None,
) -> Path:
    """Write a lock file pinning `archive` as ffmpeg 1.0 for one target."""
    lock = folder / "binaries.lock"
    lock.write_text(
        f"[ffmpeg.{target}]\n"
        f'url = "{archive.as_uri()}"\n'
        f'sha256 = "{sha256 or sha256_hex(archive)}"\n'
        f'archive_member = "{member}"\n'
        'version = "1.0"\n'
        'license = "GPL-3.0-or-later"\n'
        'source_url = "https://example.invalid/ffmpeg-1.0.tar.xz"\n',
        encoding="utf-8",
    )
    return lock


class CountingOpener:
    """Opens URLs the way the script does, remembering each one."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> BinaryIO:
        self.urls.append(url)
        return fetch_binaries.open_download(url)


def fetch(
    lock: Path,
    bin_dir: Path,
    target: str = "windows-x64",
    open_url: Callable[[str], BinaryIO] = fetch_binaries.open_download,
) -> int:
    os_name, arch = target.split("-")
    argv = ["--os", os_name, "--arch", arch]
    return fetch_binaries.main(argv, lock_path=lock, bin_dir=bin_dir, open_url=open_url)


def files_under(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*") if path.is_file())


@pytest.fixture
def sources(tmp_path: Path) -> Path:
    """A folder for archives and locks, apart from the bin folder the script writes to."""
    folder = tmp_path / "sources"
    folder.mkdir()
    return folder


@pytest.fixture
def ffmpeg_zip(sources: Path) -> Path:
    members = {MEMBER: BINARY, "ffmpeg-1.0/bin/ffprobe.exe": b"probe", "ffmpeg-1.0/LICENSE": b"GPL"}
    return make_zip(sources / "ffmpeg-1.0.zip", members)


def test_fetch_extracts_only_the_binary_and_keeps_the_verified_archive(
    sources: Path, ffmpeg_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bin_dir = tmp_path / "bin"
    assert fetch(write_lock(sources, ffmpeg_zip), bin_dir) == 0

    target_dir = bin_dir / "windows-x64"
    assert files_under(bin_dir) == [target_dir / "ffmpeg.exe", target_dir / "ffmpeg.zip"]
    assert (target_dir / "ffmpeg.exe").read_bytes() == BINARY
    assert (target_dir / "ffmpeg.zip").read_bytes() == ffmpeg_zip.read_bytes()
    assert "ffmpeg 1.0 (GPL-3.0-or-later): ffmpeg.exe" in capsys.readouterr().out


def test_wrong_hash_fails_with_a_clear_error_and_leaves_no_file(
    sources: Path, ffmpeg_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wrong = "0" * 64
    bin_dir = tmp_path / "bin"
    assert fetch(write_lock(sources, ffmpeg_zip, sha256=wrong), bin_dir) == 1

    error = capsys.readouterr().err
    assert "SHA-256 mismatch" in error
    assert f"binaries.lock expects: {wrong}" in error
    assert f"the download has:      {sha256_hex(ffmpeg_zip)}" in error
    assert files_under(bin_dir) == []


def test_wrong_hash_for_a_new_version_also_removes_the_old_files(
    sources: Path, ffmpeg_zip: Path, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "bin"
    assert fetch(write_lock(sources, ffmpeg_zip), bin_dir) == 0

    newer = make_zip(sources / "ffmpeg-1.1.zip", {MEMBER: b"newer build"})
    assert fetch(write_lock(sources, newer, sha256="0" * 64), bin_dir) == 1
    assert files_under(bin_dir) == []


def test_second_run_downloads_nothing_and_changes_no_file(
    sources: Path, ffmpeg_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = write_lock(sources, ffmpeg_zip)
    bin_dir = tmp_path / "bin"
    assert fetch(lock, bin_dir) == 0
    before = {path: path.stat().st_mtime_ns for path in files_under(bin_dir)}
    capsys.readouterr()

    opener = CountingOpener()
    assert fetch(lock, bin_dir, open_url=opener) == 0

    assert opener.urls == []
    assert {path: path.stat().st_mtime_ns for path in files_under(bin_dir)} == before
    assert "already up to date" in capsys.readouterr().out


# One replacement is shorter than the real binary; the other is the same size, so only the hash
# comparison can catch it.
@pytest.mark.parametrize("damaged", [b"tampered", b"X" + BINARY[1:]], ids=["shorter", "same-size"])
def test_rerun_restores_a_changed_binary_from_the_kept_archive(
    damaged: bytes, sources: Path, ffmpeg_zip: Path, tmp_path: Path
) -> None:
    lock = write_lock(sources, ffmpeg_zip)
    bin_dir = tmp_path / "bin"
    assert fetch(lock, bin_dir) == 0
    binary = bin_dir / "windows-x64" / "ffmpeg.exe"
    binary.write_bytes(damaged)

    opener = CountingOpener()
    pins = fetch_binaries.load_pins(lock)
    results = fetch_binaries.fetch_target(pins, "windows", "x64", binary.parent, opener)

    assert opener.urls == []
    assert [result.action for result in results] == [fetch_binaries.Action.EXTRACTED]
    assert binary.read_bytes() == BINARY


def test_rerun_downloads_again_when_the_kept_archive_is_damaged(
    sources: Path, ffmpeg_zip: Path, tmp_path: Path
) -> None:
    lock = write_lock(sources, ffmpeg_zip)
    bin_dir = tmp_path / "bin"
    assert fetch(lock, bin_dir) == 0
    kept = bin_dir / "windows-x64" / "ffmpeg.zip"
    kept.write_bytes(kept.read_bytes()[:100])

    opener = CountingOpener()
    assert fetch(lock, bin_dir, open_url=opener) == 0

    assert opener.urls == [ffmpeg_zip.as_uri()]
    assert kept.read_bytes() == ffmpeg_zip.read_bytes()
    assert (bin_dir / "windows-x64" / "ffmpeg.exe").read_bytes() == BINARY


TAR_KINDS = [(".tar.gz", "w:gz"), (".tgz", "w:gz"), (".tar.xz", "w:xz")]


@pytest.mark.parametrize(("suffix", "mode"), TAR_KINDS)
def test_tar_archives_are_extracted_like_zips(
    suffix: str, mode: str, sources: Path, tmp_path: Path
) -> None:
    archive = make_tar(sources / f"ffmpeg-1.0{suffix}", {"ffmpeg-1.0/ffmpeg": BINARY}, mode)
    lock = write_lock(sources, archive, target="linux-x64", member="ffmpeg-1.0/ffmpeg")
    bin_dir = tmp_path / "bin"
    assert fetch(lock, bin_dir, target="linux-x64") == 0

    target_dir = bin_dir / "linux-x64"
    assert files_under(bin_dir) == [target_dir / "ffmpeg", target_dir / f"ffmpeg{suffix}"]
    assert (target_dir / "ffmpeg").read_bytes() == BINARY


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no execute permission bit")
@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_macos_binaries_are_marked_executable(kind: str, sources: Path, tmp_path: Path) -> None:
    members = {"ffmpeg": BINARY}
    if kind == "zip":
        archive = make_zip(sources / "ffmpeg.zip", members)
    else:
        archive = make_tar(sources / "ffmpeg.tar.gz", members, "w:gz")
    lock = write_lock(sources, archive, target="macos-arm64", member="ffmpeg")
    bin_dir = tmp_path / "bin"
    assert fetch(lock, bin_dir, target="macos-arm64") == 0

    binary = bin_dir / "macos-arm64" / "ffmpeg"
    assert binary.stat().st_mode & 0o111 == 0o111


def test_missing_archive_member_is_a_clear_error(
    sources: Path, ffmpeg_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bin_dir = tmp_path / "bin"
    assert fetch(write_lock(sources, ffmpeg_zip, member="ffmpeg/ffmpeg.exe"), bin_dir) == 1

    error = capsys.readouterr().err
    assert "has no file named ffmpeg/ffmpeg.exe" in error
    assert "archive_member" in error
    assert not (bin_dir / "windows-x64" / "ffmpeg.exe").exists()


def make_special_zip(path: Path, kind: str) -> str:
    """Write a zip whose only member is a symlink or a directory, returning the member name."""
    with zipfile.ZipFile(path, "w") as archive:
        if kind == "directory":
            archive.mkdir("bin/ffmpeg")
            return "bin/ffmpeg/"
        link = zipfile.ZipInfo("bin/ffmpeg")
        # Unix zip tools mark a symlink in the member's mode and store its target as the data.
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/usr/bin/ffmpeg")
        return link.filename


def make_special_tar(path: Path, kind: str) -> str:
    """Write a tar whose only member is a symlink or a directory, returning the member name."""
    member = tarfile.TarInfo("bin/ffmpeg")
    if kind == "directory":
        member.type = tarfile.DIRTYPE
    else:
        member.type = tarfile.SYMTYPE
        member.linkname = "/usr/bin/ffmpeg"
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(member)
    return member.name


SPECIAL_MEMBER_MAKERS = {".zip": make_special_zip, ".tar.gz": make_special_tar}


@pytest.mark.parametrize("suffix", list(SPECIAL_MEMBER_MAKERS))
@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_a_symlink_or_directory_in_the_archive_is_refused(
    kind: str, suffix: str, sources: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = sources / f"ffmpeg{suffix}"
    member = SPECIAL_MEMBER_MAKERS[suffix](archive, kind)
    lock = write_lock(sources, archive, target="macos-arm64", member=member)
    bin_dir = tmp_path / "bin"

    assert fetch(lock, bin_dir, target="macos-arm64") == 1
    assert "isn't a regular file" in capsys.readouterr().err
    assert not (bin_dir / "macos-arm64" / "ffmpeg").exists()


def test_a_member_named_with_a_windows_path_is_refused_before_anything_is_written(
    sources: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Tar keeps backslashes in names, so on Windows this member would land beside the folder.
    member = "..\\escaped.exe"
    archive = make_tar(sources / "ffmpeg.tar.gz", {member: BINARY}, "w:gz")
    lock = write_lock(sources, archive, member=member.replace("\\", "\\\\"))
    bin_dir = tmp_path / "bin"

    assert fetch(lock, bin_dir) == 1
    assert "plain file name" in capsys.readouterr().err
    assert files_under(tmp_path) == files_under(sources)


def test_a_failed_connection_is_reported_and_leaves_no_file(
    sources: Path, ffmpeg_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(url: str) -> BinaryIO:
        raise urllib.error.URLError("connection refused")

    bin_dir = tmp_path / "bin"
    assert fetch(write_lock(sources, ffmpeg_zip), bin_dir, open_url=refuse) == 1

    assert "Couldn't download ffmpeg 1.0 for windows-x64" in capsys.readouterr().err
    assert files_under(bin_dir) == []


def test_a_local_write_failure_is_a_clear_error(
    sources: Path, ffmpeg_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A file where the target folder should be, so nothing can be written there.
    (bin_dir / "windows-x64").write_bytes(b"")

    assert fetch(write_lock(sources, ffmpeg_zip), bin_dir) == 1
    assert "Couldn't write ffmpeg 1.0 for windows-x64" in capsys.readouterr().err


def test_a_full_disk_during_a_download_is_a_write_error_not_a_download_error(
    sources: Path,
    ffmpeg_zip: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = Path.open

    def open_on_a_full_disk(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        if path.name.endswith(".partial") and "w" in mode:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_on_a_full_disk)
    bin_dir = tmp_path / "bin"
    assert fetch(write_lock(sources, ffmpeg_zip), bin_dir) == 1

    error = capsys.readouterr().err
    assert "Couldn't write ffmpeg 1.0 for windows-x64" in error
    assert "No space left on device" in error
    assert "Couldn't download" not in error
    assert files_under(bin_dir) == []


class DroppedConnection(io.BytesIO):
    """A response that delivers some bytes, then fails like a dropped connection."""

    def read(self, size: int | None = -1) -> bytes:
        if self.tell() > 0:
            raise ConnectionResetError("connection reset by peer")
        return super().read(10)


def test_a_download_cut_short_leaves_no_partial_file(
    sources: Path, ffmpeg_zip: Path, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "bin"
    lock = write_lock(sources, ffmpeg_zip)
    data = ffmpeg_zip.read_bytes()
    assert fetch(lock, bin_dir, open_url=lambda url: DroppedConnection(data)) == 1
    assert files_under(bin_dir) == []


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ('[ffmpeg.windows-x64]\nurl = "https://x.invalid/a.zip"\n', "text value for sha256"),
        ('[ffmpeg.win-x64]\nurl = "https://x.invalid/a.zip"\n', "named <os>-<arch>"),
        ('["../ffmpeg".windows-x64]\n', "component names"),
        ("ffmpeg = 1\n", "should be a table of targets"),
        ("[ffmpeg\n", "isn't valid TOML"),
    ],
)
def test_lock_problems_are_clear_errors(entry: str, message: str, tmp_path: Path) -> None:
    lock = tmp_path / "binaries.lock"
    lock.write_text(entry, encoding="utf-8")
    with pytest.raises(fetch_binaries.FetchError, match=message):
        fetch_binaries.load_pins(lock)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "ABC123", "64 lowercase hex characters"),
        ("url", "https://x.invalid/ffmpeg.7z", "url should end in one of"),
        ("version", "", "needs a text value for version"),
        ("archive_member", "ffmpeg-1.0/..", "plain file name"),
        ("archive_member", "..\\escaped.exe", "plain file name"),
        ("archive_member", "C:\\outside\\abs.exe", "plain file name"),
        ("archive_member", "D:abs.exe", "plain file name"),
    ],
)
def test_bad_field_values_are_clear_errors(
    field: str, value: str, message: str, sources: Path, ffmpeg_zip: Path
) -> None:
    lock = write_lock(sources, ffmpeg_zip)
    lines = lock.read_text(encoding="utf-8").splitlines()
    # A TOML literal string, so backslashes in the value arrive as they are.
    lines = [f"{field} = '{value}'" if line.startswith(f"{field} =") else line for line in lines]
    lock.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(fetch_binaries.FetchError, match=message):
        fetch_binaries.load_pins(lock)


def test_nothing_pinned_for_linux_is_fine_because_the_system_provides_ffmpeg(
    sources: Path, ffmpeg_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bin_dir = tmp_path / "bin"
    assert fetch(write_lock(sources, ffmpeg_zip), bin_dir, target="linux-x64") == 0

    out = capsys.readouterr().out
    assert "Nothing is pinned for linux-x64" in out
    assert "system package manager" in out
    assert not bin_dir.exists()


@pytest.mark.parametrize("target", ["windows-arm64", "macos-x64"])
def test_nothing_pinned_for_another_target_is_an_error_naming_it(
    target: str,
    sources: Path,
    ffmpeg_zip: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bin_dir = tmp_path / "bin"
    assert fetch(write_lock(sources, ffmpeg_zip), bin_dir, target=target) == 1

    error = capsys.readouterr().err
    assert f"Nothing is pinned for {target} in binaries.lock" in error
    assert "would have no ffmpeg" in error
    assert "use a pinned target: windows-x64." in error
    assert not bin_dir.exists()


def test_the_lock_pins_ffmpeg_for_windows_and_both_macos_architectures() -> None:
    pins = fetch_binaries.load_pins(fetch_binaries.LOCK_PATH)
    ffmpeg = {pin.target: pin for pin in pins if pin.component == "ffmpeg"}

    assert set(ffmpeg) == {"windows-x64", "macos-arm64", "macos-x64"}
    for pin in ffmpeg.values():
        assert pin.url.startswith("https://")
        assert pin.source_url.startswith("https://")
        assert pin.license == "GPL-3.0-or-later"


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Windows", "AMD64", ("windows", "x64")),
        ("Windows", "ARM64", ("windows", "arm64")),
        ("Darwin", "arm64", ("macos", "arm64")),
        ("Darwin", "x86_64", ("macos", "x64")),
        ("Linux", "aarch64", ("linux", "arm64")),
        ("FreeBSD", "riscv64", (None, None)),
    ],
)
def test_host_target_uses_the_lock_names(
    system: str, machine: str, expected: tuple[str | None, str | None]
) -> None:
    assert fetch_binaries.host_target(system, machine) == expected


def test_unknown_host_needs_explicit_os(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_binaries.platform, "system", lambda: "Plan9")
    with pytest.raises(SystemExit) as exit_info:
        fetch_binaries.parse_args([])
    assert exit_info.value.code == 2
