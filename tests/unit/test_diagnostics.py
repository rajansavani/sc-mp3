"""Tests for the diagnostics text: what it holds, and what it never holds."""

from pathlib import Path, PureWindowsPath

import pytest

from scmp3.app import diagnostics
from scmp3.app.diagnostics import diagnostics_text, last_log_lines, settings_lines
from scmp3.core import log
from scmp3.core.log import MASK
from scmp3.core.models import Settings

TOKEN = "2-123456-987654321-AbCdEfGhIjKl"


def write_log(path: Path, count: int, prefix: str = "line") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{prefix} {number}\n" for number in range(count)), encoding="utf-8")
    return path


def make_text(log_file: Path, settings: Settings | None = None) -> str:
    return diagnostics_text(
        app_version="0.1.0",
        engine_version="2026.8.19",
        settings=settings or Settings(),
        log_file=log_file,
        os_name="Windows-11-10.0.26200-SP0",
    )


def test_the_versions_and_the_os_come_first(tmp_path: Path) -> None:
    text = make_text(tmp_path / "sc-mp3.log")
    assert text.splitlines()[:4] == [
        "sc-mp3 diagnostics",
        "App: sc-mp3 0.1.0",
        "Download engine: yt-dlp 2026.8.19",
        "OS: Windows-11-10.0.26200-SP0",
    ]


def test_an_unknown_engine_version_says_so(tmp_path: Path) -> None:
    text = diagnostics_text(
        app_version="0.1.0", engine_version=None, settings=Settings(), log_file=tmp_path / "x.log"
    )
    assert "Download engine: yt-dlp version unknown" in text


def test_folders_are_cut_to_their_last_part() -> None:
    settings = Settings(library_dir=Path("/home/kaia/Music/sc-mp3"))
    lines = settings_lines(settings)
    assert "  library_dir: sc-mp3" in lines
    assert not any("kaia" in line for line in lines)


def test_every_setting_is_listed_and_the_client_id_only_as_set() -> None:
    settings = Settings(soundcloud_client_id="a1b2c3d4e5", custom_words=["[Bass Boosted]"])
    lines = settings_lines(settings)
    assert len(lines) == len(Settings().to_dict())
    assert "  soundcloud_client_id: set" in lines
    assert '  custom_words: ["[Bass Boosted]"]' in lines
    assert '  when_file_exists: "ask"' in lines
    assert settings_lines(Settings())[-1] == "  first_run_done: false"


def test_the_last_200_log_lines_are_included(tmp_path: Path) -> None:
    log_file = write_log(tmp_path / "sc-mp3.log", 250)
    lines = last_log_lines(log_file)
    assert len(lines) == diagnostics.LOG_LINES == 200
    assert lines[0] == "line 50"
    assert lines[-1] == "line 249"


def test_a_short_log_reaches_into_the_last_rotated_file(tmp_path: Path) -> None:
    log_file = write_log(tmp_path / "sc-mp3.log", 30, "new")
    write_log(tmp_path / "sc-mp3.log.1", 500, "old")
    lines = last_log_lines(log_file)
    assert len(lines) == 200
    assert lines[0] == "old 330"
    assert lines[-1] == "new 29"


def test_a_missing_log_gives_no_lines(tmp_path: Path) -> None:
    assert last_log_lines(tmp_path / "sc-mp3.log") == []


def test_an_unreadable_log_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = write_log(tmp_path / "sc-mp3.log", 3)

    def refuse(self: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "read_text", refuse)
    # The older file's lines come first, as they would if it could be read.
    assert last_log_lines(log_file) == [
        "(The log file sc-mp3.log.1 couldn't be read: Access is denied)",
        "(The log file sc-mp3.log couldn't be read: Access is denied)",
    ]


def test_tokens_and_the_home_folder_never_reach_the_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = PureWindowsPath("C:/Users/Kaia Lou")
    monkeypatch.setattr(log, "_home", log.home_pattern(home, ignore_case=True))
    # Written before the log masked the home folder, as by an older version.
    log_file = tmp_path / "sc-mp3.log"
    log_file.write_text(
        f"saved C:\\Users\\Kaia Lou\\Music\\a.mp3\nAuthorization: OAuth {TOKEN}\n", encoding="utf-8"
    )
    settings = Settings(library_dir=Path("C:/Users/Kaia Lou/Music/sc-mp3"))
    text = make_text(log_file, settings)
    assert "Kaia Lou" not in text
    assert TOKEN not in text
    assert "saved ~\\Music\\a.mp3" in text
    assert f"Authorization: {MASK}" in text
