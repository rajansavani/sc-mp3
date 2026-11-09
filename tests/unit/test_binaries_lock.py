"""Tests for packaging/binaries_lock.py, and for the locks the repo keeps."""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKAGING = REPO / "packaging"
LOCK = PACKAGING / "binaries.lock"
REFERENCE_LOCK = PACKAGING / "ffmpeg" / "reference.lock"
RELEASE_WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
VALID_ENTRY = (
    "[ffmpeg.windows-x64]\n"
    'url = "https://x.invalid/ffmpeg-1.0.zip"\n'
    f'sha256 = "{"0" * 64}"\n'
    'archive_member = "ffmpeg-1.0/bin/ffmpeg.exe"\n'
    'version = "1.0"\n'
    'license = "LGPL-2.1-or-later"\n'
    'source_url = "https://x.invalid/ffmpeg-1.0"\n'
)


def load_module(name: str) -> ModuleType:
    """Import packaging/<name>.py by its path, under the name its siblings import it by."""
    spec = importlib.util.spec_from_file_location(name, PACKAGING / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first, as a normal import would, so dataclasses and later siblings find it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load_module("binary_archives")
binaries_lock = load_module("binaries_lock")


def write_lock(folder: Path, text: str) -> Path:
    lock = folder / "binaries.lock"
    lock.write_text(text, encoding="utf-8")
    return lock


def test_an_entry_becomes_a_pin_named_after_its_component_and_target(tmp_path: Path) -> None:
    [pin] = binaries_lock.load_pins(write_lock(tmp_path, VALID_ENTRY))
    assert pin.target == "windows-x64"
    assert pin.label == "ffmpeg 1.0 for windows-x64"
    # The kept archive is named after the component, so a new version replaces it.
    assert pin.archive_name == "ffmpeg.zip"
    assert pin.binary_name == "ffmpeg.exe"
    assert pin.lock == str(tmp_path / "binaries.lock")


@pytest.mark.parametrize(
    ("lock", "label"),
    [
        (LOCK, "packaging/binaries.lock"),
        (REFERENCE_LOCK, "packaging/ffmpeg/reference.lock"),
    ],
)
def test_a_lock_in_the_repo_is_named_from_the_repos_root(lock: Path, label: str) -> None:
    assert binaries_lock.lock_label(lock) == label
    assert {pin.lock for pin in binaries_lock.load_pins(lock)} == {label}


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ('[ffmpeg.windows-x64]\nurl = "https://x.invalid/a.zip"\n', "text value for sha256"),
        ('[ffmpeg.win-x64]\nurl = "https://x.invalid/a.zip"\n', "named <os>-<arch>"),
        ('["../ffmpeg".windows-x64]\n', "component names"),
        ("ffmpeg = 1\n", "should be a table of targets"),
        ("[ffmpeg\n", "isn't valid TOML"),
    ],
)
def test_lock_problems_are_clear_errors(entry: str, message: str, tmp_path: Path) -> None:
    with pytest.raises(binaries_lock.FetchError, match=message):
        binaries_lock.load_pins(write_lock(tmp_path, entry))


def test_a_missing_lock_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(binaries_lock.FetchError, match="Couldn't read"):
        binaries_lock.load_pins(tmp_path / "binaries.lock")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "ABC123", "64 lowercase hex characters"),
        ("url", "https://x.invalid/ffmpeg.7z", "url should end in one of"),
        ("version", "", "needs a text value for version"),
        ("archive_member", "ffmpeg-1.0/..", "plain file name"),
        ("archive_member", "..\\escaped.exe", "plain file name"),
        ("archive_member", "C:\\outside\\abs.exe", "plain file name"),
        ("archive_member", "D:abs.exe", "plain file name"),
    ],
)
def test_bad_field_values_are_clear_errors(
    field: str, value: str, message: str, tmp_path: Path
) -> None:
    lines = VALID_ENTRY.splitlines()
    # A TOML literal string, so backslashes in the value arrive as they are.
    lines = [f"{field} = '{value}'" if line.startswith(f"{field} =") else line for line in lines]
    with pytest.raises(binaries_lock.FetchError, match=message):
        binaries_lock.load_pins(write_lock(tmp_path, "\n".join(lines)))


def test_the_lock_pins_our_own_lgpl_ffmpeg_for_windows_and_both_macos_architectures() -> None:
    pins = binaries_lock.load_pins(LOCK)
    ffmpeg = {pin.target: pin for pin in pins if pin.component == "ffmpeg"}

    assert set(ffmpeg) == {"windows-x64", "macos-arm64", "macos-x64"}
    for target, pin in ffmpeg.items():
        release = f"https://github.com/rajansavani/sc-mp3/releases/download/ffmpeg-{pin.version}-"
        assert pin.url.startswith(release)
        assert pin.url.endswith(f"/ffmpeg-{pin.version}-sc-mp3-{target}.zip")
        # Our zips keep the binary at the top, beside its README and licenses folder.
        assert pin.archive_member == ("ffmpeg.exe" if target == "windows-x64" else "ffmpeg")
        assert pin.license == "LGPL-2.1-or-later"
        # The page of the release the zip comes from, which holds its source and build script.
        download_folder = pin.url.rsplit("/", 1)[0]
        assert pin.source_url == download_folder.replace("/releases/download/", "/releases/tag/")


def workflow_env(workflow: Path, name: str) -> str:
    """A value set in a workflow's top-level env, like FFMPEG_RELEASE_URL."""
    found = re.search(rf"^  {name}: (\S+)$", workflow.read_text(encoding="utf-8"), re.MULTILINE)
    assert found is not None, f"{workflow.name} sets no {name}"
    return found.group(1)


def test_app_releases_attach_ffmpegs_whole_source_from_the_release_the_lock_pins() -> None:
    pins = binaries_lock.load_pins(LOCK)
    release_page = workflow_env(RELEASE_WORKFLOW, "FFMPEG_RELEASE_URL")
    assert {pin.source_url for pin in pins} == {release_page}
    # The Windows job downloads each file it pins from that release, and checks its SHA-256.
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    for name in {f"ffmpeg-{pin.version}.tar.xz" for pin in pins} | {"lame-", "build.sh"}:
        assert re.search(rf'"{re.escape(name)}[^"]*" = "[0-9a-f]{{64}}"', workflow), name


def test_the_reference_lock_pins_a_full_ffmpeg_for_each_target_ffmpeg_yml_checks() -> None:
    pins = binaries_lock.load_pins(REFERENCE_LOCK)
    shipped = binaries_lock.load_pins(LOCK)
    assert {pin.target for pin in pins} == {pin.target for pin in shipped}
    assert all(pin.url.startswith("https://") for pin in pins)
