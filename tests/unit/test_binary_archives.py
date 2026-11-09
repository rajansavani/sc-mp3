"""Tests for packaging/binary_archives.py, on small archives made in a temp folder."""

import importlib.util
import io
import stat
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "binary_archives.py"
DATA = b"pretend ffmpeg build\n" * 100
MEMBER = "bin/ffmpeg"


def load_script() -> ModuleType:
    """Import the module by its path, under the name its siblings import it by."""
    spec = importlib.util.spec_from_file_location("binary_archives", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


archives = load_script()


def make_archive(path: Path, members: dict[str, bytes]) -> Path:
    """A zip or tar of the given members, picked by the path's suffix."""
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        return path
    mode = "w:xz" if path.name.endswith(".tar.xz") else "w:gz"
    with tarfile.open(path, mode) as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def make_special_zip(path: Path, kind: str) -> str:
    """Write a zip whose only member is a symlink or a directory, returning the member name."""
    with zipfile.ZipFile(path, "w") as archive:
        if kind == "directory":
            archive.mkdir(MEMBER)
            return f"{MEMBER}/"
        link = zipfile.ZipInfo(MEMBER)
        # Unix zip tools mark a symlink in the member's mode and store its target as the data.
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/usr/bin/ffmpeg")
        return link.filename


def make_special_tar(path: Path, kind: str) -> str:
    """Write a tar whose only member is a symlink or a directory, returning the member name."""
    member = tarfile.TarInfo(MEMBER)
    if kind == "directory":
        member.type = tarfile.DIRTYPE
    else:
        member.type = tarfile.SYMTYPE
        member.linkname = "/usr/bin/ffmpeg"
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(member)
    return member.name


SPECIAL_MEMBER_MAKERS = {".zip": make_special_zip, ".tar.gz": make_special_tar}


@pytest.mark.parametrize("suffix", list(archives.MEMBER_OPENERS))
def test_each_archive_type_yields_the_members_bytes_and_size(suffix: str, tmp_path: Path) -> None:
    archive = make_archive(tmp_path / f"ffmpeg{suffix}", {MEMBER: DATA, "LICENSE": b"text"})
    with archives.MEMBER_OPENERS[suffix](archive, MEMBER) as (stream, size):
        assert stream.read() == DATA
    assert size == len(DATA)


@pytest.mark.parametrize("suffix", list(SPECIAL_MEMBER_MAKERS))
def test_a_missing_member_is_named_with_its_archive(suffix: str, tmp_path: Path) -> None:
    archive = make_archive(tmp_path / f"ffmpeg{suffix}", {MEMBER: DATA})
    opener = archives.MEMBER_OPENERS[suffix]
    with pytest.raises(archives.ArchiveError) as error_info, opener(archive, "bin/other"):
        pass
    assert str(error_info.value) == f"ffmpeg{suffix} has no file named bin/other."


@pytest.mark.parametrize("suffix", list(SPECIAL_MEMBER_MAKERS))
@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_a_symlink_or_directory_in_the_archive_is_refused(
    kind: str, suffix: str, tmp_path: Path
) -> None:
    archive = tmp_path / f"ffmpeg{suffix}"
    member = SPECIAL_MEMBER_MAKERS[suffix](archive, kind)
    opener = archives.MEMBER_OPENERS[suffix]
    with (
        pytest.raises(archives.ArchiveError, match="isn't a regular file"),
        opener(archive, member),
    ):
        pass


@pytest.mark.parametrize(
    ("url", "suffix"),
    [
        ("https://x.invalid/ffmpeg.zip", ".zip"),
        ("https://x.invalid/FFMPEG.TAR.XZ", ".tar.xz"),
        ("https://x.invalid/ffmpeg.tgz?download=1", ".tgz"),
        ("https://x.invalid/ffmpeg.tar.gz#top", ".tar.gz"),
        ("https://x.invalid/ffmpeg.7z", None),
        ("https://x.invalid/get?file=ffmpeg.zip", None),
    ],
)
def test_the_archive_type_comes_from_the_end_of_the_urls_path(url: str, suffix: str | None) -> None:
    assert archives.archive_suffix(url) == suffix
