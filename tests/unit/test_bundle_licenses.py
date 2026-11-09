"""Tests for packaging/bundle_licenses.py, with pretend installed packages and ffmpeg downloads."""

import importlib.metadata
import importlib.util
import sys
import zipfile
from pathlib import Path, PurePosixPath
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "bundle_licenses.py"


def load_script() -> ModuleType:
    """Import the module by its path, under a name that can't hide another module."""
    spec = importlib.util.spec_from_file_location("scmp3_packaging_bundle_licenses", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


licenses = load_script()

# What ffmpeg.yml's Windows zip holds, folders included, with pretend texts.
WINDOWS_ZIP = {
    "README.txt": b"FFmpeg 9.0.2 with LAME 4.0, for windows-x64",
    "ffmpeg.exe": b"MZ",
    "licenses/": b"",
    "licenses/FFmpeg/": b"",
    "licenses/FFmpeg/LICENSE.md": b"FFmpeg's license overview",
    "licenses/FFmpeg/COPYING.LGPLv2.1": b"LGPL 2.1",
    "licenses/LAME/": b"",
    "licenses/LAME/LICENSE": b"LAME's license note",
    "licenses/LAME/COPYING": b"LGPL 2",
    "licenses/MinGW-w64/": b"",
    "licenses/MinGW-w64/copyright": b"ZPL 2.1",
    "licenses/GCC/": b"",
    "licenses/GCC/copyright": b"GCC's runtime exception",
    "licenses/GCC/GPL-3": b"GPL 3",
}
WINDOWS_NOTICES = {
    "README.txt": b"FFmpeg 9.0.2 with LAME 4.0, for windows-x64",
    "FFmpeg/LICENSE.md": b"FFmpeg's license overview",
    "FFmpeg/COPYING.LGPLv2.1": b"LGPL 2.1",
    "LAME/LICENSE": b"LAME's license note",
    "LAME/COPYING": b"LGPL 2",
    "MinGW-w64/copyright": b"ZPL 2.1",
    "GCC/copyright": b"GCC's runtime exception",
    "GCC/GPL-3": b"GPL 3",
}
# The Mac zips hold the same, minus ffmpeg.exe and the two runtimes.
MAC_ZIP = {
    "ffmpeg": b"\xcf\xfa\xed\xfe",
    **{
        name: data
        for name, data in WINDOWS_ZIP.items()
        if name != "ffmpeg.exe" and "MinGW-w64" not in name and "GCC" not in name
    },
}


def make_ffmpeg_archive(path: Path, members: dict[str, bytes]) -> Path:
    """A pretend ffmpeg download holding the given members."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def as_names(notices: dict[PurePosixPath, bytes]) -> dict[str, bytes]:
    return {str(name): data for name, data in notices.items()}


def make_dist(site: Path, name: str, files: dict[str, str]) -> importlib.metadata.Distribution:
    """An installed package in a pretend site-packages, with these files listed in its RECORD."""
    info = site / f"{name}-1.0.dist-info"
    info.mkdir(parents=True)
    metadata = f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\n"
    (info / "METADATA").write_text(metadata, encoding="utf-8")
    for relative, text in files.items():
        (site / relative).parent.mkdir(parents=True, exist_ok=True)
        (site / relative).write_text(text, encoding="utf-8")
    record = [f"{relative},," for relative in [*files, f"{info.name}/METADATA"]]
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    return importlib.metadata.PathDistribution(info)


def test_bundled_files_and_modules_name_the_packages_that_own_them() -> None:
    owners = {
        "pil/_imaging.cp313-win_amd64.pyd": "pillow",
        "jaraco/context/__init__.py": "jaraco.context",
        "jaraco/classes/__init__.py": "jaraco.classes",
        "sc_mp3-1.0.dist-info/metadata": "sc-mp3",
    }
    names = [
        "PIL/_imaging.cp313-win_amd64.pyd",
        *licenses.module_files("jaraco.context"),
        "sc_mp3-1.0.dist-info/METADATA",
        "python313.dll",
    ]
    assert licenses.bundled_packages(names, owners, "sc-mp3") == [
        "jaraco.context",
        "pillow",
        "pyinstaller",
    ]


def test_a_module_is_found_as_a_file_or_a_package() -> None:
    assert licenses.module_files("a.b") == ["a/b.py", "a/b/__init__.py"]


def test_license_files_come_from_the_metadata_folders_including_vendored_ones(
    tmp_path: Path,
) -> None:
    dist = make_dist(
        tmp_path,
        "tool",
        {
            "tool-1.0.dist-info/licenses/LICENSE": "MIT",
            "tool-1.0.dist-info/licenses/NOTICE": "notice",
            "tool/_vendor/lib-2.0.dist-info/LICENSE.txt": "BSD",
            "tool/LICENSE_helper.py": "not a license",
        },
    )
    found = {
        str(target): source.read_text(encoding="utf-8")
        for target, source in licenses.license_files(dist)
    }
    assert found == {"LICENSE": "MIT", "NOTICE": "notice", "lib-2.0/LICENSE.txt": "BSD"}


def test_a_package_without_license_files_uses_the_copy_kept_in_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = make_dist(tmp_path / "site", "bare", {"bare/__init__.py": ""})
    monkeypatch.setattr(licenses.importlib.metadata, "distribution", lambda name: dist)
    kept = tmp_path / "kept"
    (kept / "bare").mkdir(parents=True)
    (kept / "bare" / "LICENSE.txt").write_text("BSD", encoding="utf-8")
    found = licenses.package_license_files("bare", kept)
    assert [(str(target), source.read_text(encoding="utf-8")) for target, source in found] == [
        ("LICENSE.txt", "BSD")
    ]
    with pytest.raises(licenses.LicenseError, match="bare ships no license file"):
        licenses.package_license_files("bare", tmp_path / "nothing kept")


def test_each_package_folder_kept_in_the_repo_holds_a_license() -> None:
    for folder in licenses.REPO_LICENSES.iterdir():
        assert any(folder.iterdir()), folder.name


def test_ffmpeg_notices_are_the_readme_and_every_license_text_in_our_zip(tmp_path: Path) -> None:
    archive = make_ffmpeg_archive(tmp_path / "ffmpeg.zip", WINDOWS_ZIP)
    required = licenses.required_ffmpeg_notices("windows-x64")
    assert as_names(licenses.ffmpeg_notices(archive, required)) == WINDOWS_NOTICES


@pytest.mark.parametrize("target", ["macos-arm64", "macos-x64"])
def test_a_mac_zip_needs_no_runtime_notices(target: str, tmp_path: Path) -> None:
    archive = make_ffmpeg_archive(tmp_path / "ffmpeg.zip", MAC_ZIP)
    notices = as_names(licenses.ffmpeg_notices(archive, licenses.required_ffmpeg_notices(target)))
    assert set(notices) == {
        "README.txt",
        "FFmpeg/LICENSE.md",
        "FFmpeg/COPYING.LGPLv2.1",
        "LAME/LICENSE",
        "LAME/COPYING",
    }


@pytest.mark.parametrize("missing", licenses.required_ffmpeg_notices("windows-x64"))
def test_a_zip_without_a_required_text_stops_the_build_naming_it(
    missing: str, tmp_path: Path
) -> None:
    members = {name: data for name, data in WINDOWS_ZIP.items() if name != missing}
    archive = make_ffmpeg_archive(tmp_path / "ffmpeg.zip", members)
    with pytest.raises(licenses.LicenseError, match=f"has no {missing}"):
        licenses.ffmpeg_notices(archive, licenses.required_ffmpeg_notices("windows-x64"))


def test_windows_needs_the_mingw_and_gcc_notices_and_every_target_ffmpegs_and_lames() -> None:
    windows = licenses.required_ffmpeg_notices("windows-x64")
    mac = licenses.required_ffmpeg_notices("macos-arm64")
    assert set(windows) - set(mac) == {"licenses/MinGW-w64/copyright", "licenses/GCC/copyright"}
    assert {"README.txt", "licenses/FFmpeg/COPYING.LGPLv2.1", "licenses/LAME/COPYING"} <= set(mac)


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ("README.txt", "README.txt"),
        ("licenses/GCC/GPL-3", "GCC/GPL-3"),
        ("licenses/FFmpeg/COPYING.LGPLv2.1", "FFmpeg/COPYING.LGPLv2.1"),
        ("ffmpeg.exe", None),
        ("licenses", None),
        ("licenses/", None),
        ("other/README.txt", None),
        ("licenses/../../outside.txt", None),
        ("licenses/./FFmpeg/COPYING", None),
        ("/licenses/FFmpeg/COPYING", None),
        ("licenses//FFmpeg/COPYING", None),
        ("licenses\\..\\outside.txt", None),
        # On Windows a drive in any part replaces the folder it's joined to.
        ("licenses/C:/x.txt", None),
        ("licenses/D:x.txt", None),
        ("C:/licenses/x.txt", None),
        ("", None),
    ],
)
def test_only_the_readme_and_plain_names_inside_licenses_are_notices(
    member: str, expected: str | None
) -> None:
    found = licenses.notice_name(member)
    assert (str(found) if found else None) == expected


def test_a_damaged_ffmpeg_zip_is_a_license_error(tmp_path: Path) -> None:
    archive = tmp_path / "ffmpeg.zip"
    archive.write_bytes(b"not a zip")
    with pytest.raises(licenses.LicenseError, match="Couldn't read ffmpeg's notices"):
        licenses.ffmpeg_notices(archive, ())


def test_the_targets_notices_come_from_the_zip_fetch_binaries_keeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "windows-x64").mkdir()
    make_ffmpeg_archive(tmp_path / "windows-x64" / "ffmpeg.zip", WINDOWS_ZIP)
    monkeypatch.setattr(licenses, "BIN_DIR", tmp_path)
    assert as_names(licenses.target_ffmpeg_notices("windows-x64")) == WINDOWS_NOTICES


def test_pythons_license_is_the_first_place_that_has_it(tmp_path: Path) -> None:
    (tmp_path / "Lib").mkdir()
    (tmp_path / "Lib" / "LICENSE.txt").write_text("PSF", encoding="utf-8")
    candidates = [tmp_path / "LICENSE.txt", tmp_path / "Lib" / "LICENSE.txt"]
    assert licenses.python_license(candidates) == tmp_path / "Lib" / "LICENSE.txt"
    with pytest.raises(licenses.LicenseError, match=r"Couldn't find Python's LICENSE\.txt"):
        licenses.python_license(candidates[:1])
