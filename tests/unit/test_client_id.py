"""Tests for the SoundCloud client_id override: what counts as one, and writing it at start."""

from pathlib import Path

import pytest

from scmp3.core import client_id, engine, paths
from scmp3.core.settings import SettingsStore

CLIENT_ID = "Zz0Yy9Xx8Ww7Vv6Uu5Tt4Ss3Rr2Qq1Pp"
FOUND_BY_YT_DLP = "a" * 32


@pytest.fixture(autouse=True)
def fake_ffmpeg(scmp3_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty file standing in for ffmpeg, so building a YoutubeDL warns about nothing."""
    exe = scmp3_home / "ffmpeg.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv(paths.FFMPEG_ENV, str(exe))


def cached_client_id() -> str | None:
    with engine.make_ydl() as ydl:
        return ydl.cache.load("soundcloud", "client_id")


def yt_dlp_found_its_own() -> None:
    """As when SoundCloud rejected the override, and yt-dlp found another client_id."""
    with engine.make_ydl() as ydl:
        ydl.cache.store("soundcloud", "client_id", FOUND_BY_YT_DLP)


@pytest.mark.parametrize("value", [CLIENT_ID, None])
def test_32_letters_and_digits_or_none_are_a_client_id(value: str | None) -> None:
    assert client_id.client_id_problem(value) is None


@pytest.mark.parametrize("value", ["short", CLIENT_ID + "a", CLIENT_ID[:-1] + "!"])
def test_anything_else_isnt(value: str) -> None:
    assert client_id.client_id_problem(value) == {"reason": client_id.NOT_A_CLIENT_ID}


def test_the_saved_override_is_written_again_at_start(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"soundcloud_client_id": CLIENT_ID})
    yt_dlp_found_its_own()
    client_id.apply_saved_override(store)
    assert cached_client_id() == CLIENT_ID


def test_without_an_override_yt_dlps_own_client_id_stays(tmp_path: Path) -> None:
    yt_dlp_found_its_own()
    client_id.apply_saved_override(SettingsStore(tmp_path / "settings.json"))
    assert cached_client_id() == FOUND_BY_YT_DLP
