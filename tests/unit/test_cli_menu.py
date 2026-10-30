"""Tests for `scmp3 app-menu`, which adds sc-mp3 to the Linux app menu and removes it again."""

import argparse
from pathlib import Path, PurePosixPath

import pytest

from scmp3 import cli
from scmp3.cli_menu import (
    MenuFiles,
    MenuPlace,
    data_home,
    desktop_entry,
    exec_quoted,
    find_command,
    is_inside,
    packaged_icon,
    run_menu,
    temporary_folders,
)
from scmp3.cli_output import EXIT_ERROR, EXIT_OK, EXIT_USAGE

PACKAGING_ICON = Path(__file__).resolve().parents[2] / "packaging" / "icons" / "icon-256.png"
COMMAND = "/home/sam/.local/bin/scmp3"


def args(*argv: str) -> argparse.Namespace:
    """The arguments as the CLI table's parser reads them."""
    return cli.build_parser().parse_args(["app-menu", *argv])


def linux(home: Path, **overrides: object) -> MenuPlace:
    """A Linux computer whose home is `home`, with scmp3 run from ~/.local/bin."""
    values: dict[str, object] = {
        "platform": "linux",
        "environ": {},
        "home": home,
        "argv0": COMMAND,
        "temp_dir": "/tmp",
        "which": lambda name: None,
    }
    values.update(overrides)
    return MenuPlace(**values)  # type: ignore[arg-type]


def entry_of(home: Path) -> Path:
    return home / ".local" / "share" / "applications" / "sc-mp3.desktop"


def icon_of(home: Path) -> Path:
    return home / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "sc-mp3.png"


def test_the_entry_and_icon_go_under_local_share(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_menu(args(), linux(tmp_path)) == EXIT_OK
    assert icon_of(tmp_path).read_bytes() == packaged_icon()
    entry = entry_of(tmp_path).read_text(encoding="utf-8")
    assert f'Exec="{COMMAND}" %F' in entry
    # A desktop entry writes a backslash as two, which only a Windows test path has.
    assert "Icon=" + str(icon_of(tmp_path)).replace("\\", "\\\\") in entry
    out, err = capsys.readouterr()
    assert out.splitlines() == [str(entry_of(tmp_path)), str(icon_of(tmp_path))]
    assert f"It opens {COMMAND}" in err
    assert "scmp3 app-menu --remove" in err


def test_xdg_data_home_moves_the_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    place = linux(tmp_path / "home", environ={"XDG_DATA_HOME": str(data)})
    assert run_menu(args(), place) == EXIT_OK
    assert (data / "applications" / "sc-mp3.desktop").is_file()
    assert (data / "icons" / "hicolor" / "256x256" / "apps" / "sc-mp3.png").is_file()


def test_a_relative_xdg_data_home_is_ignored_as_the_spec_says() -> None:
    home = Path("/home/sam")
    assert data_home({"XDG_DATA_HOME": "data"}, home) == home / ".local" / "share"
    assert data_home({"XDG_DATA_HOME": ""}, home) == home / ".local" / "share"


def test_adding_again_rewrites_the_entry(tmp_path: Path) -> None:
    run_menu(args(), linux(tmp_path))
    run_menu(args(), linux(tmp_path, argv0="/opt/bin/scmp3"))
    assert 'Exec="/opt/bin/scmp3" %F' in entry_of(tmp_path).read_text(encoding="utf-8")


def test_remove_deletes_both_files_and_names_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_menu(args(), linux(tmp_path))
    capsys.readouterr()
    assert run_menu(args("--remove"), linux(tmp_path)) == EXIT_OK
    assert not entry_of(tmp_path).exists()
    assert not icon_of(tmp_path).exists()
    out, err = capsys.readouterr()
    assert out.splitlines() == [str(entry_of(tmp_path)), str(icon_of(tmp_path))]
    assert "Removed sc-mp3 from your app menu" in err


def test_remove_leaves_the_folders_other_apps_use(tmp_path: Path) -> None:
    run_menu(args(), linux(tmp_path))
    run_menu(args("--remove"), linux(tmp_path))
    assert entry_of(tmp_path).parent.is_dir()
    assert icon_of(tmp_path).parent.is_dir()


def test_removing_when_nothing_is_there_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_menu(args("--remove"), linux(tmp_path)) == EXIT_OK
    assert "nothing to remove" in capsys.readouterr().err


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_other_systems_are_told_its_for_linux(
    platform: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_menu(args(), linux(tmp_path, platform=platform)) == EXIT_USAGE
    assert "Linux only" in capsys.readouterr().err
    assert not entry_of(tmp_path).exists()


def test_without_the_scmp3_command_nothing_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    place = linux(tmp_path, argv0="/usr/lib/python3/scmp3/__main__.py")
    assert run_menu(args(), place) == EXIT_ERROR
    assert "uv tool update-shell" in capsys.readouterr().err
    assert not entry_of(tmp_path).exists()


def test_a_folder_that_cant_be_written_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A file where the applications folder should be.
    applications = tmp_path / ".local" / "share" / "applications"
    applications.parent.mkdir(parents=True)
    applications.write_text("not a folder", encoding="utf-8")
    assert run_menu(args(), linux(tmp_path)) == EXIT_ERROR
    err = capsys.readouterr().err
    assert err.startswith(f"Couldn't write {entry_of(tmp_path)}: ")
    assert err.endswith("Check that you can write to that folder, then run scmp3 app-menu again.\n")


def test_a_failed_write_leaves_the_old_entry_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_menu(args(), linux(tmp_path))
    before = entry_of(tmp_path).read_text(encoding="utf-8")

    def full_disk(*_args: object) -> None:
        raise OSError(28, "No space left on device")

    # The new entry is written next to the old one, then swapped in.
    monkeypatch.setattr("scmp3.core.paths.os.replace", full_disk)
    assert run_menu(args(), linux(tmp_path, argv0="/opt/bin/scmp3")) == EXIT_ERROR
    assert entry_of(tmp_path).read_text(encoding="utf-8") == before
    assert [path.name for path in entry_of(tmp_path).parent.iterdir()] == ["sc-mp3.desktop"]


def test_a_file_that_cant_be_deleted_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A folder where the entry should be, which unlink() can't delete.
    entry_of(tmp_path).mkdir(parents=True)
    assert run_menu(args("--remove"), linux(tmp_path)) == EXIT_ERROR
    err = capsys.readouterr().err
    assert err.startswith(f"Couldn't delete {entry_of(tmp_path)}: ")
    assert "then run scmp3 app-menu --remove again." in err


@pytest.mark.parametrize(
    "command",
    [
        "/home/sam/.cache/uv/archive-v0/2Xw41uaduU1lXkmLFUt92/bin/scmp3",
        "/tmp/uv-run/bin/scmp3",
    ],
)
def test_a_command_in_a_temporary_folder_is_refused(
    command: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Such as uvx, which runs tools from uv's cache.
    place = linux(tmp_path, argv0=command, environ={"UV_CACHE_DIR": "/home/sam/.cache/uv"})
    assert run_menu(args(), place) == EXIT_ERROR
    assert "temporary folder" in capsys.readouterr().err
    assert not entry_of(tmp_path).exists()


def test_uvs_cache_is_found_where_uv_puts_it() -> None:
    home = PurePosixPath("/home/sam")
    assert temporary_folders({}, home, "/tmp") == ["/home/sam/.cache/uv", "/tmp"]
    xdg = {"XDG_CACHE_HOME": "/data/cache"}
    assert temporary_folders(xdg, home, "/tmp") == ["/data/cache/uv", "/tmp"]
    assert temporary_folders({"UV_CACHE_DIR": "/c"}, home, "/tmp") == ["/c", "/tmp"]


def test_a_folder_only_contains_paths_under_it() -> None:
    assert is_inside("/home/sam/.cache/uv/a/scmp3", "/home/sam/.cache/uv/")
    assert not is_inside("/home/sam/.cache/uvx/scmp3", "/home/sam/.cache/uv")
    assert not is_inside("/home/sam/.local/bin/scmp3", "/tmp")


def test_the_running_command_is_used_with_its_full_path() -> None:
    assert find_command("/home/sam/.local/bin/scmp3") == "/home/sam/.local/bin/scmp3"


def test_python_m_scmp3_falls_back_to_the_command_on_path() -> None:
    found = find_command("/x/scmp3/__main__.py", which=lambda name: f"/usr/local/bin/{name}")
    assert found == "/usr/local/bin/scmp3"


@pytest.mark.parametrize(
    ("path", "quoted"),
    [
        ("/home/sam/.local/bin/scmp3", '"/home/sam/.local/bin/scmp3"'),
        ("/home/sam/my apps/scmp3", '"/home/sam/my apps/scmp3"'),
        ('/a"b/scmp3', '"/a\\\\"b/scmp3"'),
        ("/a$b`c/scmp3", '"/a\\\\$b\\\\`c/scmp3"'),
        ("/a\\b/scmp3", '"/a\\\\\\\\b/scmp3"'),
        ("/100%/scmp3", '"/100%%/scmp3"'),
    ],
)
def test_the_command_is_quoted_as_the_desktop_entry_spec_says(path: str, quoted: str) -> None:
    assert exec_quoted(path) == quoted


def test_the_entry_matches_the_window_by_the_commands_name() -> None:
    entry = desktop_entry(COMMAND, Path("/icons/sc-mp3.png"))
    assert entry.startswith("[Desktop Entry]\n")
    assert "\nStartupWMClass=scmp3\n" in entry
    assert "\nTerminal=false\n" in entry


def test_the_packaged_icon_is_the_one_make_icons_draws() -> None:
    # Run packaging/make_icons.py, then copy icon-256.png over src/scmp3/icons/sc-mp3.png.
    assert packaged_icon() == PACKAGING_ICON.read_bytes()


def test_menu_files_sit_where_the_specs_put_them() -> None:
    files = MenuFiles.under(Path("/d"))
    assert files.entry == Path("/d/applications/sc-mp3.desktop")
    assert files.icon == Path("/d/icons/hicolor/256x256/apps/sc-mp3.png")
