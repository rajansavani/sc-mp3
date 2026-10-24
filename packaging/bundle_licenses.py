"""Fill the built app folder's licenses/ folder with the license files of everything bundled.

packaging/build.py calls add_licenses after PyInstaller. The packages come from the folder itself:
each bundled file, and each module in the exe's PYZ, is matched to the installed package whose
RECORD lists it.
"""

import importlib.metadata
import shutil
import sys
import tomllib
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath

PACKAGING_DIR = Path(__file__).resolve().parent

# The folder next to sc-mp3.exe with the license files of everything bundled, one folder each.
LICENSES_DIR = "licenses"
# File names a package's metadata folder uses for its license texts and notices.
LICENSE_NAMES = ("license", "licence", "copying", "notice", "authors")
# License files kept in the repo, by package, for packages whose wheels ship none.
REPO_LICENSES = PACKAGING_DIR / "licenses"
# sc-mp3.exe is PyInstaller's bootloader, which no bundled file is named after.
ALWAYS_LICENSED = frozenset({"pyinstaller"})
PYTHON_LICENSE = Path(sys.base_prefix) / "LICENSE.txt"
BINARIES_LOCK = PACKAGING_DIR / "binaries.lock"
# Where fetch_binaries.py keeps each target's verified downloads.
BIN_DIR = PACKAGING_DIR / "bin"
# What gyan.dev's archive holds beside its bin folder: the GPL and the build's configuration.
FFMPEG_NOTICE_FILES = frozenset({"LICENSE", "README.txt"})

# Where a license file goes under licenses/<name>, and where it is now.
type LicenseFile = tuple[PurePosixPath, Path]


class LicenseError(Exception):
    """A license file is missing or couldn't be copied. The message says which."""


def packed_modules(exe: Path) -> Iterator[str]:
    """The names of the modules in the exe's PYZ."""
    from PyInstaller.archive.readers import PKG_ITEM_PYZ, CArchiveReader

    archive = CArchiveReader(str(exe))
    for name, (*_, typecode) in archive.toc.items():
        if typecode == PKG_ITEM_PYZ:
            yield from archive.open_embedded_archive(name).toc


def module_files(module: str) -> list[str]:
    """Where a module's source sits in site-packages, as a file or as a package."""
    base = module.replace(".", "/")
    return [f"{base}.py", f"{base}/__init__.py"]


def bundled_names(folder: Path, exe: Path) -> list[str]:
    """Every bundled file and module, named by its path in site-packages."""
    internal = folder / "_internal"
    files = [
        path.relative_to(internal).as_posix() for path in internal.rglob("*") if path.is_file()
    ]
    return files + [name for module in packed_modules(exe) for name in module_files(module)]


def installed_owners() -> dict[str, str]:
    """The installed packages' files, by their path in site-packages, with the package's name."""
    return {
        str(file).casefold(): dist.name
        for dist in importlib.metadata.distributions()
        for file in dist.files or []
    }


def bundled_packages(names: Iterable[str], owners: dict[str, str], app_name: str) -> list[str]:
    """The packages that own a bundled file or module, plus PyInstaller, but not the app itself."""
    found = {owners[name.casefold()] for name in names if name.casefold() in owners}
    return sorted((found | ALWAYS_LICENSED) - {app_name}, key=str.casefold)


def license_files(dist: importlib.metadata.Distribution) -> list[LicenseFile]:
    """A package's license files in its metadata folders, each with its name under licenses/."""
    found = []
    for file in dist.files or []:
        info = next((part for part in file.parts if part.endswith(".dist-info")), None)
        if info is None or not file.name.casefold().startswith(LICENSE_NAMES):
            continue
        # A vendored package's files go in a folder of their own, like setuptools/tomli-2.4.0.
        own = file.parts[0] == info
        target = PurePosixPath(
            file.name if own else f"{info.removesuffix('.dist-info')}/{file.name}"
        )
        found.append((target, Path(str(dist.locate_file(file)))))
    return found


def package_license_files(name: str, kept: Path = REPO_LICENSES) -> list[LicenseFile]:
    """A package's license files, or the ones kept in the repo for it."""
    found = license_files(importlib.metadata.distribution(name))
    if not found and (kept / name).is_dir():
        found = [(PurePosixPath(path.name), path) for path in sorted((kept / name).iterdir())]
    if not found:
        raise LicenseError(f"{name} ships no license file. Add it to packaging/licenses/{name}/.")
    return found


def ffmpeg_top_folder(target: str, lock: Path = BINARIES_LOCK) -> str:
    """The folder the pinned ffmpeg archive keeps everything in, from binaries.lock."""
    pin = tomllib.loads(lock.read_text(encoding="utf-8"))["ffmpeg"][target]
    return PurePosixPath(pin["archive_member"]).parts[0]


def ffmpeg_notices(archive: Path, top: str) -> dict[str, bytes]:
    """The notice files in the ffmpeg archive's top folder, by name."""
    try:
        with zipfile.ZipFile(archive) as files:
            return {name: files.read(f"{top}/{name}") for name in sorted(FFMPEG_NOTICE_FILES)}
    except (KeyError, OSError, zipfile.BadZipFile) as error:
        raise LicenseError(
            f"Couldn't read ffmpeg's notices from {archive.name}: {error}"
        ) from error


def copy_licenses(files: Iterable[LicenseFile], folder: Path) -> None:
    """Copy each license file to its name under the folder."""
    for target, source in files:
        destination = folder / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(source, destination)
        except OSError as error:
            raise LicenseError(
                f"Couldn't copy {source.name} into {folder.name}: {error}"
            ) from error


def add_licenses(folder: Path, app_name: str, target: str) -> list[str]:
    """Fill licenses/ for every bundled package, Python and ffmpeg, and return the packages."""
    licenses = folder / LICENSES_DIR
    names = bundled_names(folder, folder / f"{app_name}.exe")
    packages = bundled_packages(names, installed_owners(), app_name)
    for name in packages:
        copy_licenses(package_license_files(name), licenses / name)
    copy_licenses([(PurePosixPath("LICENSE.txt"), PYTHON_LICENSE)], licenses / "python")
    (licenses / "ffmpeg").mkdir(parents=True, exist_ok=True)
    notices = ffmpeg_notices(BIN_DIR / target / "ffmpeg.zip", ffmpeg_top_folder(target))
    for name, data in notices.items():
        (licenses / "ffmpeg" / name).write_bytes(data)
    return packages
