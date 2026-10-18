"""Shared fixtures: a temp SCMP3_HOME, ffmpeg, and tiny audio files generated with it."""

import shutil
import subprocess
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the markers for internet tests and for tests of the real app folders."""
    config.addinivalue_line("markers", "network: needs the internet; run with `pytest -m network`")
    config.addinivalue_line(
        "markers", "real_app_folders: runs without SCMP3_HOME, to check the real app folders"
    )


@pytest.fixture(autouse=True)
def scmp3_home(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Path | None:
    """An empty folder set as SCMP3_HOME for every test, so no test touches the real app folders.

    Tests marked `real_app_folders` run with SCMP3_HOME unset instead, and get None.
    """
    if request.node.get_closest_marker("real_app_folders"):
        monkeypatch.delenv("SCMP3_HOME", raising=False)
        return None
    # A folder of its own, so a test's tmp_path stays empty for the test.
    factory: pytest.TempPathFactory = request.getfixturevalue("tmp_path_factory")
    home = factory.mktemp("scmp3_home")
    monkeypatch.setenv("SCMP3_HOME", str(home))
    return home


@pytest.fixture(scope="session")
def ffmpeg_exe() -> Path:
    """The ffmpeg on PATH. Tests that use it skip when it's missing."""
    found = shutil.which("ffmpeg")
    if found is None:
        pytest.skip("ffmpeg isn't on PATH")
    return Path(found)


def make_silence(ffmpeg: Path, dest: Path, codec_args: list[str]) -> Path:
    """Write one second of stereo silence to `dest`, encoded with `codec_args`."""
    command = [
        str(ffmpeg),
        *("-hide_banner", "-loglevel", "error", "-nostdin"),
        *("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1"),
        *codec_args,
        str(dest),
    ]
    # Output isn't captured here, so pytest shows ffmpeg's error when the fixture fails.
    subprocess.run(command, check=True)
    return dest


# The files below are shared by the whole session, so copy one before changing it.
@pytest.fixture(scope="session")
def tiny_mp3(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A one-second silent MP3 at 320 kbps CBR."""
    dest = tmp_path_factory.mktemp("tiny_audio") / "silence.mp3"
    return make_silence(ffmpeg_exe, dest, ["-c:a", "libmp3lame", "-b:a", "320k"])


@pytest.fixture(scope="session")
def tiny_m4a(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A one-second silent AAC file in an M4A container."""
    dest = tmp_path_factory.mktemp("tiny_audio") / "silence.m4a"
    return make_silence(ffmpeg_exe, dest, ["-c:a", "aac", "-b:a", "128k"])
