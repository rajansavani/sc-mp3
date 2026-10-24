"""Tests for packaging/bundle_licenses.py, with pretend installed packages and ffmpeg downloads."""

import importlib.metadata
import importlib.util
import sys
import zipfile
from pathlib import Path
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
FFMPEG_TOP = licenses.ffmpeg_top_folder("windows-x64")


def make_ffmpeg_archive(path: Path, members: dict[str, bytes]) -> Path:
    """A pretend ffmpeg download holding the given members."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


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


def test_ffmpeg_notices_are_the_license_and_readme_in_the_archives_top_folder(
    tmp_path: Path,
) -> None:
    members = {
        f"{FFMPEG_TOP}/LICENSE": b"GPL",
        f"{FFMPEG_TOP}/README.txt": b"notes",
        "other/LICENSE": b"not this one",
    }
    archive = make_ffmpeg_archive(tmp_path / "ffmpeg.zip", members)
    assert licenses.ffmpeg_notices(archive, FFMPEG_TOP) == {
        "LICENSE": b"GPL",
        "README.txt": b"notes",
    }
    without_readme = make_ffmpeg_archive(tmp_path / "bare.zip", {f"{FFMPEG_TOP}/LICENSE": b"GPL"})
    with pytest.raises(licenses.LicenseError, match=r"README\.txt"):
        licenses.ffmpeg_notices(without_readme, FFMPEG_TOP)


def test_the_ffmpeg_top_folder_comes_from_the_pinned_archive_member() -> None:
    assert FFMPEG_TOP == "ffmpeg-9.0.2-essentials_build"
