"""Tests for finding the Deno YouTube runs on: the downloaded copy, or in a uv install, its own."""

import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scmp3.core.components import Components
from scmp3.core.manifests import Manifest, write_manifest
from scmp3.core.sources import deno

FAKE_VERSION_LINE = "deno 2.9.8 (stable, release, x86_64-pc-windows-msvc)"


@pytest.fixture(autouse=True)
def fresh_version_cache() -> Iterator[None]:
    """Each test reads Deno versions anew."""
    deno._version_of.cache_clear()
    yield
    deno._version_of.cache_clear()


def installed(root: Path, version: str = "2.9.8") -> Components:
    """A components folder with a good Deno installed, written as an install leaves it."""
    folder = root / "deno" / version
    folder.mkdir(parents=True)
    (folder / "deno.exe").write_bytes(b"deno")
    manifest = Manifest(
        name="deno",
        version=version,
        sha256="0" * 64,
        source_url="https://github.com/denoland/deno/releases/download/v2.9.8/deno.zip",
        file="deno.exe",
        installed_at=datetime(2026, 10, 21, tzinfo=UTC),
    )
    write_manifest(folder, manifest)
    return Components(root)


def fake_deno(folder: Path, line: str = FAKE_VERSION_LINE) -> Path:
    """A program that prints `line` for --version, as a .cmd on Windows and a script elsewhere."""
    if sys.platform == "win32":
        path = folder / "deno.cmd"
        path.write_text(f"@echo {line}\r\n", encoding="ascii")
        return path
    path = folder / "deno"
    path.write_text(f"#!/bin/sh\necho '{line}'\n", encoding="ascii")
    path.chmod(0o755)
    return path


def runs(binary: Path) -> str:
    return "2.9.8"


def test_the_downloaded_copy_counts_first(tmp_path: Path) -> None:
    components = installed(tmp_path / "components")
    found = deno.find_deno(
        components, frozen=False, locate=lambda: tmp_path / "other", read_version=runs
    )
    assert found == deno.Deno(tmp_path / "components/deno/2.9.8/deno.exe", "2.9.8", managed=True)


def test_a_downloaded_copy_that_doesnt_run_counts_as_missing(tmp_path: Path) -> None:
    components = installed(tmp_path / "components")
    # The installed file is four bytes of text, which no system runs.
    assert deno.find_deno(components, frozen=True) is None
    found = deno.find_deno(
        components,
        frozen=False,
        locate=lambda: tmp_path / "own",
        read_version=lambda path: None if path.name == "deno.exe" else "2.5.1",
    )
    assert found == deno.Deno(tmp_path / "own", "2.5.1", managed=False)


def test_a_downloaded_copy_that_doesnt_run_is_marked_bad_so_it_installs_again(
    tmp_path: Path,
) -> None:
    components = installed(tmp_path / "components")
    deno.forget_broken_copy(components, read_version=runs)
    assert components.active("deno") is not None
    deno.forget_broken_copy(components, read_version=lambda path: None)
    assert components.active("deno") is None
    assert [manifest.status for manifest in components.installed("deno")] == ["bad"]


def test_without_a_downloaded_copy_a_uv_install_uses_its_own(tmp_path: Path) -> None:
    binary = tmp_path / "deno.exe"
    found = deno.find_deno(
        Components(tmp_path / "components"),
        frozen=False,
        locate=lambda: binary,
        read_version=lambda path: "2.5.1",
    )
    assert found == deno.Deno(binary, "2.5.1", managed=False)


def test_the_packaged_app_counts_only_its_downloaded_copy(tmp_path: Path) -> None:
    def locate() -> Path:
        raise AssertionError("the packaged app doesn't look for Deno elsewhere")

    found = deno.find_deno(Components(tmp_path / "components"), frozen=True, locate=locate)
    assert found is None


@pytest.mark.parametrize("version", ["2.2.9", "1.46.3", None])
def test_a_deno_too_old_for_yt_dlp_or_that_wont_run_doesnt_count(
    tmp_path: Path, version: str | None
) -> None:
    found = deno.find_deno(
        Components(tmp_path / "components"),
        frozen=False,
        locate=lambda: tmp_path / "deno.exe",
        read_version=lambda path: version,
    )
    assert found is None


def test_no_deno_anywhere_means_no_youtube_support(tmp_path: Path) -> None:
    found = deno.find_deno(Components(tmp_path / "components"), frozen=False, locate=lambda: None)
    assert found is None


@pytest.mark.parametrize(
    ("version", "supported"),
    [("2.3.0", True), ("2.9.8", True), ("3.0", True), ("2.2.10", False), ("2", False)],
)
def test_yt_dlp_accepts_deno_2_3_or_later(version: str, supported: bool) -> None:
    assert deno.is_supported(version) is supported


def test_the_version_is_read_from_deno_itself(tmp_path: Path) -> None:
    assert deno.read_deno_version(fake_deno(tmp_path)) == "2.9.8"


def test_a_program_that_doesnt_print_denos_version_gives_none(tmp_path: Path) -> None:
    assert deno.read_deno_version(fake_deno(tmp_path, "Python 3.13.7")) is None
    assert deno.read_deno_version(tmp_path / "missing.exe") is None


def test_the_version_is_read_once_until_the_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, FAKE_VERSION_LINE.encode(), b"")

    monkeypatch.setattr(deno.subprocess, "run", run)
    binary = tmp_path / "deno.exe"
    binary.write_bytes(b"one")
    assert deno.read_deno_version(binary) == deno.read_deno_version(binary) == "2.9.8"
    assert len(calls) == 1
    binary.write_bytes(b"a new build")
    deno.read_deno_version(binary)
    assert len(calls) == 2


def test_the_extras_deno_beside_python_comes_before_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    exe = deno.sysconfig.get_config_var("EXE") or ""
    (scripts / f"deno{exe}").write_bytes(b"deno")
    monkeypatch.setattr(deno.sysconfig, "get_path", lambda name: str(scripts))
    monkeypatch.setattr(deno.shutil, "which", lambda name: str(tmp_path / "on-path" / name))
    assert deno.locate_system_deno() == scripts / f"deno{exe}"


def test_path_is_searched_when_python_has_no_deno_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deno.sysconfig, "get_path", lambda name: str(tmp_path / "scripts"))
    monkeypatch.setattr(deno.shutil, "which", lambda name: str(tmp_path / "bin" / name))
    assert deno.locate_system_deno() == tmp_path / "bin" / "deno"
    monkeypatch.setattr(deno.shutil, "which", lambda name: None)
    assert deno.locate_system_deno() is None
