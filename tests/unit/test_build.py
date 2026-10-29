"""Tests for packaging/build.py, with a fake runner in place of ffmpeg's fetch, PyInstaller and the
macOS tools.
"""

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

PACKAGING = Path(__file__).resolve().parents[2] / "packaging"
SCRIPT = PACKAGING / "build.py"
VERSION = "1.2.3"


def load_module(name: str, path: Path) -> ModuleType:
    """Import a file by its path, under the given name."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# build.py imports its siblings by name, as it does when it runs from packaging/.
licenses = load_module("bundle_licenses", PACKAGING / "bundle_licenses.py")
bundle_check = load_module("bundle_check", PACKAGING / "bundle_check.py")
load_module("fetch_binaries", PACKAGING / "fetch_binaries.py")
# A name that can't hide another module.
build = load_module("scmp3_packaging_build", SCRIPT)
WINDOWS = build.Target("windows", "x64")
MACOS = build.Target("macos", "arm64")
FFMPEG_TOP = licenses.ffmpeg_top_folder(WINDOWS.name)


def make_ffmpeg_archive(path: Path, members: dict[str, bytes]) -> Path:
    """A pretend ffmpeg download holding the given members."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


@pytest.fixture(autouse=True)
def pretend_build_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The pretend exes hold no PyInstaller archive, and the licenses come from pretend files."""
    monkeypatch.setattr(bundle_check, "packed_entries", lambda exe: iter(()))
    monkeypatch.setattr(licenses, "packed_modules", lambda exe: iter(()))
    folder = tmp_path_factory.mktemp("inputs")
    python_license = folder / "LICENSE.txt"
    python_license.write_text("PSF License", encoding="utf-8")
    monkeypatch.setattr(licenses, "PYTHON_LICENSES", (folder / "missing", python_license))
    members = {
        f"{FFMPEG_TOP}/LICENSE": b"GPL",
        f"{FFMPEG_TOP}/README.txt": b"build notes",
        f"{FFMPEG_TOP}/bin/ffmpeg.exe": b"MZ",
    }
    (folder / WINDOWS.name).mkdir()
    make_ffmpeg_archive(folder / WINDOWS.name / "ffmpeg.zip", members)
    monkeypatch.setattr(licenses, "BIN_DIR", folder)
    monkeypatch.setattr(build, "WORK_DIR", tmp_path_factory.mktemp("work"))


def write_files(folder: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def make_app(dist_dir: Path, target: Any) -> None:
    """What PyInstaller writes for the target: its folder, and on macOS the .app beside it."""
    layout = target.layout
    write_files(dist_dir / "sc-mp3", {layout.exe: b"exe", "_internal/python313.dll": b"dll"})
    if layout.bundle is not None:
        app = {"Contents/MacOS/sc-mp3": b"exe", "Contents/Info.plist": b"<plist/>"}
        write_files(dist_dir / layout.bundle, app)


def zip_folder(folder: Path, target: Path) -> None:
    """What ditto --keepParent writes: the folder, under its own name."""
    with zipfile.ZipFile(target, "w") as archive:
        for path in bundle_check.bundle_files(folder):
            archive.write(path, Path(folder.name) / path.relative_to(folder))


class FakeRunner:
    """Stands in for subprocess.run: records each command and acts out the build's steps."""

    def __init__(self, *, fail_on: str | None = None, printed: str = VERSION):
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
            target = build.Target(*command[-1].split("-"))
            make_app(Path(command[command.index("--distpath") + 1]), target)
        if step == "ditto":
            zip_folder(Path(command[-2]), Path(command[-1]))
        output = f"{self.printed}\n" if step == "version" else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    @staticmethod
    def step(command: list[str]) -> str:
        if str(build.FETCH_SCRIPT) in command:
            return "fetch"
        if "PyInstaller" in command:
            return "pyinstaller"
        if command[0] in ("codesign", "ditto"):
            return command[0]
        return "version"


@pytest.mark.parametrize(
    ("target", "name"),
    [
        (build.Target("windows", "x64"), "sc-mp3-0.0.1-windows-x64.zip"),
        (build.Target("macos", "arm64"), "sc-mp3-0.0.1-macos-arm64.zip"),
        (build.Target("macos", "x64"), "sc-mp3-0.0.1-macos-x64.zip"),
    ],
)
def test_the_zip_is_named_after_the_version_and_the_target(target: Any, name: str) -> None:
    assert build.zip_name("0.0.1", target) == name


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Windows", "AMD64", "windows-x64"),
        ("Darwin", "arm64", "macos-arm64"),
        ("Darwin", "x86_64", "macos-x64"),
        ("Linux", "x86_64", None),
        ("Darwin", "ppc", None),
    ],
)
def test_the_target_is_this_machines_os_and_architecture(
    system: str, machine: str, expected: str | None
) -> None:
    target = build.host_target(system, machine)
    assert (target.name if target else None) == expected


def test_a_windows_build_fetches_ffmpeg_runs_pyinstaller_then_writes_the_zip_and_checksum(
    tmp_path: Path,
) -> None:
    run = FakeRunner()
    build.build(run, VERSION, WINDOWS, tmp_path)
    assert [FakeRunner.step(command) for command in run.commands] == [
        "fetch",
        "pyinstaller",
        "version",
    ]
    assert run.commands[0][-4:] == ["--os", "windows", "--arch", "x64"]
    assert run.commands[1][-4:] == [str(build.SPEC), "--", "--target", "windows-x64"]
    assert str(tmp_path) in run.commands[1]
    assert run.commands[2] == [str(tmp_path / "sc-mp3" / "sc-mp3.exe"), "--version"]
    assert (tmp_path / "sc-mp3-1.2.3-windows-x64.zip").is_file()
    assert (tmp_path / "sc-mp3-1.2.3-windows-x64.zip.sha256").is_file()


def test_a_mac_build_checks_the_signature_and_zips_the_app_with_ditto(tmp_path: Path) -> None:
    run = FakeRunner()
    build.build(run, VERSION, MACOS, tmp_path)
    assert [FakeRunner.step(command) for command in run.commands] == [
        "fetch",
        "pyinstaller",
        "codesign",
        "version",
        "ditto",
    ]
    folder = tmp_path / "sc-mp3"
    assert run.commands[0][-4:] == ["--os", "macos", "--arch", "arm64"]
    # PyInstaller's own folder stays out of dist, since only the .app ships.
    assert str(build.WORK_DIR / "dist") in run.commands[1]
    assert run.commands[2][-1] == str(folder / "sc-mp3.app")
    launcher = folder / "sc-mp3.app" / "Contents" / "MacOS" / "sc-mp3"
    assert run.commands[3] == [str(launcher), "--version"]
    assert run.commands[4][:5] == ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent"]
    assert run.commands[4][-2] == str(folder)
    assert (tmp_path / "sc-mp3-1.2.3-macos-arm64.zip.sha256").is_file()


def test_the_mac_zip_holds_the_app_with_the_license_and_notices_beside_it(tmp_path: Path) -> None:
    build.build(FakeRunner(), VERSION, MACOS, tmp_path)
    with zipfile.ZipFile(tmp_path / build.zip_name(VERSION, MACOS)) as archive:
        names = set(archive.namelist())
        ffmpeg_license = archive.read("sc-mp3/licenses/ffmpeg/LICENSE")
    assert {
        "sc-mp3/sc-mp3.app/Contents/MacOS/sc-mp3",
        "sc-mp3/LICENSE.txt",
        "sc-mp3/THIRD_PARTY_NOTICES.txt",
        "sc-mp3/licenses/python/LICENSE.txt",
    } <= names
    # Martin Riedl's archive holds ffmpeg alone, and his builds are under sc-mp3's own GPL.
    assert ffmpeg_license == (build.REPO_ROOT / "LICENSE").read_bytes()
    assert not any(name.startswith("sc-mp3/_internal") for name in names)


def test_a_mac_build_replaces_the_last_builds_folder(tmp_path: Path) -> None:
    write_files(tmp_path / "sc-mp3", {"old.txt": b"the last build"})
    build.build(FakeRunner(), VERSION, MACOS, tmp_path)
    assert not (tmp_path / "sc-mp3" / "old.txt").exists()


@pytest.mark.parametrize(
    ("system", "machine", "step"),
    [
        ("Windows", "AMD64", "fetch"),
        ("Windows", "AMD64", "pyinstaller"),
        ("Windows", "AMD64", "version"),
        ("Darwin", "arm64", "codesign"),
        ("Darwin", "arm64", "ditto"),
    ],
)
def test_a_failed_step_stops_the_build_with_exit_code_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    system: str,
    machine: str,
    step: str,
) -> None:
    monkeypatch.setattr(build, "DIST_DIR", tmp_path)
    run = FakeRunner(fail_on=step)
    assert build.main(run, system, machine) == 1
    assert "error: " in capsys.readouterr().err
    assert not list(tmp_path.glob("*.zip*"))


def test_a_step_that_hangs_stops_the_build() -> None:
    def hang(command: Sequence[str], **options: Any) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(command, options["timeout"])

    with pytest.raises(build.BuildError, match="took over"):
        build.fetch_ffmpeg(hang, WINDOWS)


def test_the_build_runs_only_on_windows_or_a_mac(capsys: pytest.CaptureFixture[str]) -> None:
    def never(command: Sequence[str], **options: Any) -> subprocess.CompletedProcess:
        raise AssertionError("a step ran")

    assert build.main(never, "Linux", "x86_64") == 1
    assert "run it on Windows or a Mac" in capsys.readouterr().err


def test_version_check_wants_exit_code_0_and_the_version(tmp_path: Path) -> None:
    exe = tmp_path / "sc-mp3.exe"
    build.check_version(FakeRunner(), exe, VERSION)
    with pytest.raises(build.BuildError, match=re.escape("printed '0.0.0'")):
        build.check_version(FakeRunner(printed="0.0.0"), exe, VERSION)
    with pytest.raises(build.BuildError, match="exited with 3"):
        build.check_version(FakeRunner(fail_on="version"), exe, VERSION)


def test_an_app_without_its_launcher_fails_the_check(tmp_path: Path) -> None:
    write_files(tmp_path, {"_internal/python313.dll": b"dll"})
    launcher = tmp_path / "sc-mp3.app" / "Contents" / "MacOS" / "sc-mp3"
    with pytest.raises(build.BuildError, match=re.escape("didn't write sc-mp3.app")):
        build.check_bundle(tmp_path, launcher, [])


def test_an_app_holding_the_builders_path_fails_the_check(tmp_path: Path) -> None:
    write_files(tmp_path, {"sc-mp3.exe": b"MZ", "_internal/info.txt": b"/Users/builder/sc-mp3"})
    with pytest.raises(build.BuildError, match=re.escape("builder's paths: _internal")):
        build.check_bundle(tmp_path, tmp_path / "sc-mp3.exe", ["/Users/builder"])


@pytest.mark.parametrize(
    ("environ", "home", "checked"),
    [
        ({"GITHUB_ACTIONS": "true"}, "/Users/runner", ["checkout"]),
        ({"GITHUB_ACTIONS": "true"}, "/Users/runneradmin", ["checkout"]),
        ({}, "/Users/runner", ["checkout", "home"]),
        ({"GITHUB_ACTIONS": "true"}, "/Users/builder", ["checkout", "home"]),
    ],
    ids=["runner", "runneradmin, as on windows", "not on github", "self-hosted runner"],
)
def test_a_runners_home_folder_is_left_out_of_the_check_but_never_the_checkout(
    environ: dict[str, str], home: str, checked: list[str]
) -> None:
    named = {"checkout": str(build.REPO_ROOT), "home": str(Path(home))}
    assert build.builder_paths(environ, Path(home)) == [named[name] for name in checked]


@pytest.mark.parametrize(
    ("source", "name"),
    [("LICENSE", "LICENSE.txt"), ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.txt")],
)
def test_the_license_and_the_notices_go_next_to_the_exe(
    tmp_path: Path, source: str, name: str
) -> None:
    build.build(FakeRunner(), VERSION, WINDOWS, tmp_path)
    with zipfile.ZipFile(tmp_path / build.zip_name(VERSION, WINDOWS)) as archive:
        text = archive.read(f"sc-mp3/{name}")
    assert text == (build.REPO_ROOT / source).read_bytes()


def test_the_zip_unpacks_as_one_folder_named_after_the_app(tmp_path: Path) -> None:
    app = tmp_path / "dist" / "sc-mp3"
    write_files(app, {"sc-mp3.exe": b"MZ", "_internal/python313.dll": b"dll"})
    target = tmp_path / "release.zip"
    build.write_zip(FakeRunner(), app, target, build.zip_with_python)
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert archive.read("sc-mp3/_internal/python313.dll") == b"dll"
    assert names == ["sc-mp3/_internal/python313.dll", "sc-mp3/sc-mp3.exe"]
    assert not (tmp_path / "release.zip.partial").exists()


def test_a_zip_that_fails_halfway_leaves_nothing_behind(tmp_path: Path) -> None:
    def disk_full(run: Any, folder: Path, target: Path) -> None:
        target.write_bytes(b"half a zip")
        raise OSError(28, "No space left on device")

    target = tmp_path / "release.zip"
    with pytest.raises(build.BuildError, match="No space left"):
        build.write_zip(FakeRunner(), tmp_path, target, disk_full)
    assert not target.exists()
    assert not (tmp_path / "release.zip.partial").exists()


def test_a_failed_build_leaves_no_zip_from_the_last_build(tmp_path: Path) -> None:
    old = tmp_path / build.zip_name(VERSION, WINDOWS)
    old.write_bytes(b"the last build's zip")
    build.checksum_path(old).write_text("the last build's checksum", encoding="utf-8")
    with pytest.raises(build.BuildError):
        build.build(FakeRunner(fail_on="pyinstaller"), VERSION, WINDOWS, tmp_path)
    assert not old.exists()
    assert not build.checksum_path(old).exists()


def test_the_checksum_file_is_in_sha256sum_format(tmp_path: Path) -> None:
    target = tmp_path / "sc-mp3-1.2.3-windows-x64.zip"
    target.write_bytes(b"zip bytes")
    checksum = build.write_checksum(target)
    expected = hashlib.sha256(b"zip bytes").hexdigest()
    assert checksum.name == "sc-mp3-1.2.3-windows-x64.zip.sha256"
    assert checksum.read_bytes() == f"{expected}  sc-mp3-1.2.3-windows-x64.zip\n".encode()


def test_the_zip_holds_the_licenses_of_pyinstaller_python_and_ffmpeg(tmp_path: Path) -> None:
    build.build(FakeRunner(), VERSION, WINDOWS, tmp_path)
    with zipfile.ZipFile(tmp_path / build.zip_name(VERSION, WINDOWS)) as archive:
        names = archive.namelist()
        assert archive.read("sc-mp3/licenses/python/LICENSE.txt") == b"PSF License"
        assert archive.read("sc-mp3/licenses/ffmpeg/LICENSE") == b"GPL"
        assert archive.read("sc-mp3/licenses/ffmpeg/README.txt") == b"build notes"
    assert "sc-mp3/licenses/pyinstaller/COPYING.txt" in names
