"""Tests for the frozen Windows app's startup checks: the folder path limit and the certificates."""

import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path, PurePath

import pytest

from scmp3 import frozen
from scmp3.__main__ import main
from scmp3.app import messages
from scmp3.app.message_box import MB_ICONWARNING, MB_OK

# A code page without Cyrillic, like the ANSI code page of an English Windows.
CODE_PAGE = "cp1252"
CERTIFICATES = b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n# F\xc5\x91\n"


class FakeMessageBox:
    """Records what would have been shown, and answers OK."""

    def __init__(self) -> None:
        self.shown: list[tuple[str, str, int]] = []

    def __call__(self, text: str, title: str, flags: int) -> int:
        self.shown.append((text, title, flags))
        return 1


def make_files(folder: Path, names: Sequence[str]) -> Path:
    for name in names:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return folder


def make_app(root: Path, longest: str = "curl_cffi.libs/libcurl.dll") -> Path:
    """A pretend app folder; returns its contents folder, _internal."""
    contents = root / "sc-mp3" / "_internal"
    make_files(contents, ["base_library.zip", longest])
    (root / "sc-mp3" / "sc-mp3.exe").write_bytes(b"")
    return contents


def units(path: Path | str) -> int:
    return len(str(path).encode("utf-16-le")) // 2


def test_utf16_units_count_a_character_outside_the_bmp_twice() -> None:
    assert frozen.utf16_units("ab") == 2
    assert frozen.utf16_units("a\N{GRINNING FACE}") == 3


def test_longest_relative_path_is_measured_from_the_folder(tmp_path: Path) -> None:
    make_files(tmp_path, ["x.txt", "aa/bbb/c.txt"])
    assert frozen.longest_relative_path(tmp_path) == len(os.path.join("aa", "bbb", "c.txt"))


def test_longest_relative_path_counts_utf16_units(tmp_path: Path) -> None:
    make_files(tmp_path, ["\N{GRINNING FACE}.txt"])
    assert frozen.longest_relative_path(tmp_path) == 6


def test_app_folder_limit_leaves_the_margin_after_the_longest_file(tmp_path: Path) -> None:
    contents = make_app(tmp_path, longest="curl_cffi.libs/libcurl-impersonate-0123456789.dll")
    check = frozen.check_app_folder(contents)
    inside = len(os.path.join("curl_cffi.libs", "libcurl-impersonate-0123456789.dll"))
    assert check.folder == tmp_path / "sc-mp3"
    assert check.length == units(tmp_path / "sc-mp3")
    # The folder, a separator, _internal, a separator, then the longest file inside it.
    longest_path = check.limit + 1 + len("_internal") + 1 + inside
    assert longest_path == frozen.FILE_PATH_LIMIT - frozen.PATH_MARGIN


def test_app_folder_fits_up_to_its_limit_and_not_one_unit_more() -> None:
    assert frozen.FolderCheck(Path("app"), length=158, limit=158).fits
    assert not frozen.FolderCheck(Path("app"), length=159, limit=158).fits


def test_a_longer_file_inside_lowers_the_limit(tmp_path: Path) -> None:
    short = frozen.check_app_folder(make_app(tmp_path / "a", longest="x/" + "y" * 20 + ".dll"))
    long = frozen.check_app_folder(make_app(tmp_path / "b", longest="x/" + "y" * 30 + ".dll"))
    assert short.limit - long.limit == 10


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_a_junction_is_measured_by_the_real_path_it_leads_to(tmp_path: Path) -> None:
    import _winapi

    real_app = make_app(tmp_path / ("deep" * 10)).parent
    link = tmp_path / "j"
    _winapi.CreateJunction(str(real_app), str(link))
    try:
        check = frozen.check_app_folder(link / "_internal")
    finally:
        os.rmdir(link)
    assert check.folder == Path(os.path.realpath(real_app))
    assert check.length == units(os.path.realpath(real_app))


def test_a_folder_that_is_too_long_says_where_to_move_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(frozen, "FILE_PATH_LIMIT", 0)
    box = FakeMessageBox()
    contents = make_app(tmp_path)
    assert frozen.check_bundle(contents, box) == frozen.EXIT_FOLDER_TOO_LONG
    [(text, title, flags)] = box.shown
    check = frozen.check_app_folder(contents)
    assert title == messages.FOLDER_TOO_LONG_TITLE
    assert flags == MB_OK | MB_ICONWARNING
    assert str(tmp_path / "sc-mp3") in text
    assert (
        f"The path has {check.length} characters, and sc-mp3 needs {check.limit} or fewer" in text
    )
    assert "C:\\sc-mp3" in text


def test_a_folder_that_fits_shows_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frozen, "bundled_certificates", lambda: make_certificates(tmp_path))
    box = FakeMessageBox()
    assert frozen.check_bundle(make_app(tmp_path), box) is None
    assert box.shown == []


def test_certifi_gives_the_readable_certificates_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import certifi

    # Set back after the test, since the check replaces it.
    monkeypatch.setattr(certifi, "where", certifi.where)
    readable = PurePath("C:\\Users\\4C47~1\\cacert.pem")
    monkeypatch.setattr(frozen, "bundled_certificates", lambda: make_certificates(tmp_path))
    monkeypatch.setattr(frozen, "prepare_certificates", lambda bundled, folder: readable)
    assert frozen.check_bundle(make_app(tmp_path), FakeMessageBox()) is None
    # yt-dlp hands certifi.where() to curl_cffi as a proxy's CA file.
    assert certifi.where() == str(readable)


def test_certifi_is_left_alone_when_the_certificates_needed_no_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import certifi

    where = certifi.where
    monkeypatch.setattr(frozen, "bundled_certificates", lambda: make_certificates(tmp_path))
    monkeypatch.setattr(frozen, "prepare_certificates", lambda bundled, folder: None)
    assert frozen.check_bundle(make_app(tmp_path), FakeMessageBox()) is None
    assert certifi.where is where


def test_only_the_frozen_windows_app_has_a_contents_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert frozen.windows_contents_folder("win32") is None
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert frozen.windows_contents_folder("win32") == tmp_path
    assert frozen.windows_contents_folder("darwin") is None


def test_prepare_does_nothing_outside_the_frozen_app(scmp3_home: Path) -> None:
    assert frozen.prepare() is None
    assert not (scmp3_home / "logs").exists()


def test_version_comes_before_the_frozen_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> int:
        raise AssertionError("--version ran the checks")

    monkeypatch.setattr(frozen, "prepare", fail)
    assert main(["--version"]) == 0


def test_the_window_stays_shut_when_the_checks_stop_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frozen, "prepare", lambda: frozen.EXIT_FOLDER_TOO_LONG)
    monkeypatch.setattr("scmp3.app.main.run", lambda paths: pytest.fail("the window opened"))
    assert main([]) == frozen.EXIT_FOLDER_TOO_LONG


def make_certificates(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "cacert.pem"
    path.write_bytes(CERTIFICATES)
    return path


def no_short_paths(path: Path) -> PurePath | None:
    return None


def short_paths(table: dict[Path, str]):
    """A stand-in for GetShortPathNameW that knows the short form of some paths."""

    def shorten(path: Path) -> PurePath | None:
        short = table.get(path)
        return PurePath(short) if short else None

    return shorten


def prepare(bundled: Path, copy_folder: Path, environ: dict[str, str], shorten=no_short_paths):
    return frozen.prepare_certificates(
        bundled, copy_folder, environ=environ, encoding=CODE_PAGE, shorten=shorten
    )


def test_certificates_in_a_folder_the_code_page_can_spell_stay_put(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    bundled = make_certificates(tmp_path / "caf\N{LATIN SMALL LETTER E WITH ACUTE}")
    assert prepare(bundled, tmp_path / "cache", environ) is None
    assert environ == {}
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("name", frozen.CA_FILE_VARIABLES)
def test_a_certificate_file_the_user_chose_is_left_alone(tmp_path: Path, name: str) -> None:
    environ = {name: "C:\\certs\\corporate.pem"}
    bundled = make_certificates(tmp_path / "жук")
    assert prepare(bundled, tmp_path / "cache", environ) is None
    assert environ == {name: "C:\\certs\\corporate.pem"}


def test_the_short_path_is_used_when_the_code_page_can_spell_it(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    bundled = make_certificates(tmp_path / "жук")
    shorten = short_paths({bundled: "C:\\Users\\4C47~1\\cacert.pem"})
    assert prepare(bundled, tmp_path / "cache", environ, shorten) == PurePath(
        "C:\\Users\\4C47~1\\cacert.pem"
    )
    assert environ == {frozen.CA_FILE_VARIABLE: str(PurePath("C:\\Users\\4C47~1\\cacert.pem"))}
    assert not (tmp_path / "cache").exists()


def test_a_copy_is_made_when_the_volume_has_no_short_paths(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    bundled = make_certificates(tmp_path / "жук")
    copy = tmp_path / "cache" / frozen.CA_FILE_NAME
    assert prepare(bundled, tmp_path / "cache", environ) == copy
    assert copy.read_bytes() == CERTIFICATES
    assert environ == {frozen.CA_FILE_VARIABLE: str(copy)}


def test_an_old_copy_is_replaced_with_the_bundled_certificates(tmp_path: Path) -> None:
    bundled = make_certificates(tmp_path / "жук")
    copy = tmp_path / "cache" / frozen.CA_FILE_NAME
    copy.parent.mkdir()
    copy.write_bytes(b"certificates from an older version\n")
    prepare(bundled, tmp_path / "cache", {})
    assert copy.read_bytes() == CERTIFICATES


def test_the_copys_short_path_is_used_when_the_copy_folder_cant_be_spelled(
    tmp_path: Path,
) -> None:
    environ: dict[str, str] = {}
    bundled = make_certificates(tmp_path / "жук")
    copy = tmp_path / "кэш" / frozen.CA_FILE_NAME
    shorten = short_paths({copy: "C:\\Users\\B0C8~1\\cacert.pem"})
    assert prepare(bundled, tmp_path / "кэш", environ, shorten) == PurePath(
        "C:\\Users\\B0C8~1\\cacert.pem"
    )
    assert copy.read_bytes() == CERTIFICATES


def test_nothing_is_set_when_no_path_can_be_spelled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    environ: dict[str, str] = {}
    bundled = make_certificates(tmp_path / "жук")
    with caplog.at_level(logging.WARNING, logger=frozen.__name__):
        assert prepare(bundled, tmp_path / "кэш", environ) is None
    assert environ == {}
    assert "will fail" in caplog.text


def test_a_copy_that_cant_be_written_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    environ: dict[str, str] = {}
    bundled = make_certificates(tmp_path / "жук")
    # A file where the copy's folder should be, so the folder can't be made.
    blocked = tmp_path / "cache"
    blocked.write_bytes(b"")
    with caplog.at_level(logging.WARNING, logger=frozen.__name__):
        assert prepare(bundled, blocked, environ) is None
    assert environ == {}
    assert "Couldn't copy the certificates" in caplog.text
