"""Tests for packaging/build.py, with a fake runner in place of ffmpeg's fetch and PyInstaller."""

import hashlib
import importlib.util
import re
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "build.py"
VERSION = "1.2.3"


def load_script() -> ModuleType:
    """Import the script by its path, under a name that can't hide another module."""
    spec = importlib.util.spec_from_file_location("scmp3_packaging_build", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = load_script()


@pytest.fixture(autouse=True)
def nothing_packed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pretend exes here hold no PyInstaller archive to read."""
    monkeypatch.setattr(build, "packed_entries", lambda exe: iter(()))


def make_app(folder: Path, files: dict[str, bytes] | None = None) -> Path:
    """A pretend app folder with sc-mp3.exe and the given files."""
    app = folder / build.APP_NAME
    contents = {f"{build.APP_NAME}.exe": b"MZ", "_internal/python313.dll": b"dll", **(files or {})}
    for name, data in contents.items():
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return app


class FakeRunner:
    """Stands in for subprocess.run: records each command and acts out the build's steps."""

    def __init__(self, dist_dir: Path, *, fail_on: str | None = None, printed: str = VERSION):
        self.dist_dir = dist_dir
        self.fail_on = fail_on
        self.printed = printed
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str], **options: Any) -> subprocess.CompletedProcess:
        command = [str(part) for part in command]
        self.commands.append(command)
        step = self.step(command)
        if step == self.fail_on:
            return subprocess.CompletedProcess(command, 3, "", "")
        if step == "pyinstaller":
            make_app(self.dist_dir)
        output = f"{self.printed}\n" if step == "version" else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    @staticmethod
    def step(command: list[str]) -> str:
        if str(build.FETCH_SCRIPT) in command:
            return "fetch"
        if "PyInstaller" in command:
            return "pyinstaller"
        return "version"


def test_the_zip_is_named_after_the_version_and_the_target() -> None:
    assert build.zip_name("0.0.1") == "sc-mp3-0.0.1-windows-x64.zip"


def test_a_build_fetches_ffmpeg_runs_pyinstaller_then_writes_the_zip_and_checksum(
    tmp_path: Path,
) -> None:
    run = FakeRunner(tmp_path)
    build.build(run, VERSION, tmp_path)
    assert [FakeRunner.step(command) for command in run.commands] == [
        "fetch",
        "pyinstaller",
        "version",
    ]
    assert run.commands[0][-4:] == ["--os", "windows", "--arch", "x64"]
    assert run.commands[1][-1] == str(build.SPEC)
    assert run.commands[2] == [str(tmp_path / "sc-mp3" / "sc-mp3.exe"), "--version"]
    target = tmp_path / "sc-mp3-1.2.3-windows-x64.zip"
    assert target.is_file()
    assert (tmp_path / "sc-mp3-1.2.3-windows-x64.zip.sha256").is_file()


@pytest.mark.parametrize("step", ["fetch", "pyinstaller", "version"])
def test_a_failed_step_stops_the_build_with_exit_code_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], step: str
) -> None:
    monkeypatch.setattr(build, "DIST_DIR", tmp_path)
    run = FakeRunner(tmp_path, fail_on=step)
    assert build.main(run, platform="win32") == 1
    assert "error: " in capsys.readouterr().err
    assert not list(tmp_path.glob("*.zip"))


def test_a_step_that_hangs_stops_the_build(tmp_path: Path) -> None:
    def hang(command: Sequence[str], **options: Any) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(command, options["timeout"])

    with pytest.raises(build.BuildError, match="took over"):
        build.fetch_ffmpeg(hang)


def test_the_build_runs_only_on_windows(capsys: pytest.CaptureFixture[str]) -> None:
    def never(command: Sequence[str], **options: Any) -> subprocess.CompletedProcess:
        raise AssertionError("a step ran")

    assert build.main(never, platform="darwin") == 1
    assert "run it on Windows" in capsys.readouterr().err


def test_version_check_wants_exit_code_0_and_the_version(tmp_path: Path) -> None:
    exe = tmp_path / "sc-mp3.exe"
    build.check_version(FakeRunner(tmp_path), exe, VERSION)
    with pytest.raises(build.BuildError, match=re.escape("printed '0.0.0'")):
        build.check_version(FakeRunner(tmp_path, printed="0.0.0"), exe, VERSION)
    with pytest.raises(build.BuildError, match="exited with 3"):
        build.check_version(FakeRunner(tmp_path, fail_on="version"), exe, VERSION)


def test_a_clean_app_folder_passes_the_check(tmp_path: Path) -> None:
    build.check_bundle(make_app(tmp_path), ["C:\\Users\\builder"])


def test_an_app_folder_without_the_exe_fails_the_check(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    (app / "sc-mp3.exe").unlink()
    with pytest.raises(build.BuildError, match=re.escape("didn't write sc-mp3.exe")):
        build.check_bundle(app, [])


@pytest.mark.parametrize("name", ["direct_url.json", "uv_cache.json"])
def test_the_built_folder_holds_no_local_only_metadata(tmp_path: Path, name: str) -> None:
    app = make_app(tmp_path, {f"_internal/sc_mp3-0.0.1.dist-info/{name}": b"{}"})
    with pytest.raises(build.BuildError, match=name):
        build.check_bundle(app, [])


def test_the_bundled_record_lists_no_local_only_file(tmp_path: Path) -> None:
    record = "\n".join(
        [
            "sc_mp3-0.0.1.dist-info/METADATA,sha256=abc,1346",
            "sc_mp3-0.0.1.dist-info/direct_url.json,sha256=def,105",
            "sc_mp3.pth,sha256=ghi,62",
        ]
    )
    app = make_app(tmp_path, {"_internal/sc_mp3-0.0.1.dist-info/RECORD": record.encode()})
    with pytest.raises(build.BuildError) as error:
        build.check_bundle(app, [])
    assert "line for sc_mp3-0.0.1.dist-info/direct_url.json" in str(error.value)
    assert "line for sc_mp3.pth" in str(error.value)
    assert "METADATA" not in str(error.value)


def test_a_record_without_local_only_files_passes(tmp_path: Path) -> None:
    record = b"sc_mp3-0.0.1.dist-info/METADATA,sha256=abc,1346\n"
    build.check_bundle(make_app(tmp_path, {"_internal/x.dist-info/RECORD": record}), [])


HOME = "C:\\Users\\Дмитрий"


@pytest.mark.parametrize(
    ("private", "content"),
    [
        ("C:\\Users\\builder", b'{"url": "file:///C:/Users/builder/sc-mp3"}'),
        ("C:\\Users\\builder", b"C:\\USERS\\BUILDER\\sc-mp3\\src"),
        ("C:\\Users\\builder", "c:\\users\\builder".encode("utf-16-le")),
        ("C:\\Users\\builder", b"x" + "C:\\Users\\builder".encode("utf-16-le")),
        ("C:\\Users\\builder", b'{"path": "C:\\\\Users\\\\builder\\\\sc-mp3"}'),
        (HOME, f"{HOME}\\sc-mp3".encode()),
        (HOME, HOME.upper().encode()),
        ("C:\\Users\\John Smith", b'{"url": "file:///C:/Users/John%20Smith/sc-mp3"}'),
        (HOME, b"file:///C:/Users/%D0%94%D0%BC%D0%B8%D1%82%D1%80%D0%B8%D0%B9/sc-mp3"),
    ],
    ids=[
        "forward slashes",
        "other case",
        "utf-16",
        "utf-16 at an odd byte",
        "json backslashes",
        "non-ascii as is",
        "non-ascii other case",
        "url-encoded space",
        "url-encoded non-ascii",
    ],
)
def test_no_spelling_of_a_private_path_gets_past_the_check(
    tmp_path: Path, private: str, content: bytes
) -> None:
    app = make_app(tmp_path, {"_internal/data/info.txt": b"before " + content + b" after"})
    with pytest.raises(build.BuildError, match=re.escape("info.txt")):
        build.check_bundle(app, [private])


def test_a_private_path_packed_inside_the_exe_fails_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The PYZ holds the compiled modules, and its strings only show once it's decompressed.
    packed = [("sc-mp3.exe!PYZ.pyz!scmp3._probe", b"\xfa\x16C:\\Users\\builder\\sc-mp3")]
    monkeypatch.setattr(build, "packed_entries", lambda exe: iter(packed))
    with pytest.raises(build.BuildError, match=re.escape("PYZ.pyz!scmp3._probe")):
        build.check_bundle(make_app(tmp_path), ["C:\\Users\\builder"])


def test_the_license_goes_next_to_the_exe(tmp_path: Path) -> None:
    build.build(FakeRunner(tmp_path), VERSION, tmp_path)
    with zipfile.ZipFile(tmp_path / build.zip_name(VERSION)) as archive:
        license_text = archive.read("sc-mp3/LICENSE.txt")
    assert license_text == (build.REPO_ROOT / "LICENSE").read_bytes()


def test_the_zip_unpacks_as_one_folder_named_after_the_app(tmp_path: Path) -> None:
    app = make_app(tmp_path / "dist")
    target = tmp_path / "release.zip"
    build.write_zip(app, target)
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert archive.read("sc-mp3/_internal/python313.dll") == b"dll"
    assert names == ["sc-mp3/_internal/python313.dll", "sc-mp3/sc-mp3.exe"]
    assert not (tmp_path / "release.zip.partial").exists()


def test_a_zip_that_fails_halfway_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disk_full(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(zipfile.ZipFile, "write", disk_full)
    target = tmp_path / "release.zip"
    with pytest.raises(build.BuildError, match="No space left"):
        build.write_zip(make_app(tmp_path / "dist"), target)
    assert not target.exists()
    assert not (tmp_path / "release.zip.partial").exists()


def test_a_failed_build_leaves_no_zip_from_the_last_build(tmp_path: Path) -> None:
    old = tmp_path / build.zip_name(VERSION)
    old.write_bytes(b"the last build's zip")
    build.checksum_path(old).write_text("the last build's checksum", encoding="utf-8")
    with pytest.raises(build.BuildError):
        build.build(FakeRunner(tmp_path, fail_on="pyinstaller"), VERSION, tmp_path)
    assert not old.exists()
    assert not build.checksum_path(old).exists()


def test_the_checksum_file_is_in_sha256sum_format(tmp_path: Path) -> None:
    target = tmp_path / "sc-mp3-1.2.3-windows-x64.zip"
    target.write_bytes(b"zip bytes")
    checksum = build.write_checksum(target)
    expected = hashlib.sha256(b"zip bytes").hexdigest()
    assert checksum.name == "sc-mp3-1.2.3-windows-x64.zip.sha256"
    assert checksum.read_bytes() == f"{expected}  sc-mp3-1.2.3-windows-x64.zip\n".encode()
