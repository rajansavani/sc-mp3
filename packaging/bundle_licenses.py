"""Fill the built app folder's licenses/ folder with the license files of everything bundled.

packaging/build.py calls add_licenses after PyInstaller. The packages come from the folder itself:
each bundled file, and each module in the exe's PYZ, is matched to the installed package whose
RECORD lists it.
"""

import importlib.metadata
import re
import shutil
import sys
import sysconfig
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath

PACKAGING_DIR = Path(__file__).resolve().parent

# The folder next to the app with the license files of everything bundled, one folder each.
LICENSES_DIR = "licenses"
# File names a package's metadata folder uses for its license texts and notices.
LICENSE_NAMES = ("license", "licence", "copying", "notice", "authors")
# License files kept in the repo, by package, for packages whose wheels ship none.
REPO_LICENSES = PACKAGING_DIR / "licenses"
# The app's own program is PyInstaller's bootloader, which no bundled file is named after.
ALWAYS_LICENSED = frozenset({"pyinstaller"})
# Python keeps its license at the top on Windows and in the standard library's folder elsewhere.
PYTHON_LICENSES = (
    Path(sys.base_prefix) / "LICENSE.txt",
    Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
)
# Where fetch_binaries.py keeps each target's verified downloads, as <component><suffix>.
BIN_DIR = PACKAGING_DIR / "bin"
FFMPEG_ARCHIVE = "ffmpeg.zip"
# Our ffmpeg zips hold the binary, a README with its configuration and where its source is, and
# a licenses folder with one folder of texts per part. All but the binary go to licenses/ffmpeg.
FFMPEG_README = "README.txt"
FFMPEG_LICENSES = "licenses"
# A plain file or folder name, so no drive, separator, "." or ".." that a path on disk acts on.
PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
# The texts every ffmpeg zip must hold, so a zip without them stops the build.
FFMPEG_NOTICES = (FFMPEG_README, "licenses/FFmpeg/COPYING.LGPLv2.1", "licenses/LAME/COPYING")
# The runtime notices an OS's zip must also hold, since ffmpeg.exe has parts of both runtimes in it.
RUNTIME_NOTICES = {"windows": ("licenses/MinGW-w64/copyright", "licenses/GCC/copyright")}

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


def bundled_names(built: Path, exe: Path) -> list[str]:
    """Every file in PyInstaller's folder and module in its exe, by its path in site-packages."""
    internal = built / "_internal"
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


def python_license(candidates: Iterable[Path]) -> Path:
    """The first of the candidates that exists: Python's license, wherever this Python keeps it."""
    found = next((path for path in candidates if path.is_file()), None)
    if found is None:
        raise LicenseError(f"Couldn't find Python's LICENSE.txt in {sys.base_prefix}.")
    return found


def required_ffmpeg_notices(target: str) -> tuple[str, ...]:
    """The texts the target's ffmpeg zip must hold, by their names in the zip."""
    os_name = target.partition("-")[0]
    return FFMPEG_NOTICES + RUNTIME_NOTICES.get(os_name, ())


def notice_name(member: str) -> PurePosixPath | None:
    """Where a zip member goes under licenses/ffmpeg, or None when it isn't a notice."""
    parts = member.split("/")
    # Each name becomes a path on disk, so nothing may climb out of licenses/ffmpeg.
    if not all(PLAIN_NAME.fullmatch(part) for part in parts):
        return None
    if member == FFMPEG_README:
        return PurePosixPath(member)
    if parts[0] == FFMPEG_LICENSES and len(parts) > 1:
        return PurePosixPath(*parts[1:])
    return None


def read_notices(files: zipfile.ZipFile) -> dict[PurePosixPath, bytes]:
    """Every notice in an open ffmpeg zip, by where it goes in licenses/ffmpeg."""
    notices = {}
    for info in files.infolist():
        name = notice_name(info.filename)
        if name is not None and not info.is_dir():
            notices[name] = files.read(info)
    return notices


def ffmpeg_notices(archive: Path, required: Iterable[str]) -> dict[PurePosixPath, bytes]:
    """The README and the license texts in our ffmpeg zip, by where they go in licenses/ffmpeg."""
    try:
        with zipfile.ZipFile(archive) as files:
            notices = read_notices(files)
    except (OSError, zipfile.BadZipFile) as error:
        raise LicenseError(
            f"Couldn't read ffmpeg's notices from {archive.name}: {error}"
        ) from error
    missing = [name for name in required if notice_name(name) not in notices]
    if missing:
        raise LicenseError(
            f"{archive.name} has no {', '.join(missing)}. Pin a zip that ffmpeg.yml built."
        )
    return notices


def target_ffmpeg_notices(target: str) -> dict[PurePosixPath, bytes]:
    """The notices in the target's verified ffmpeg zip, which fetch_binaries.py keeps."""
    return ffmpeg_notices(BIN_DIR / target / FFMPEG_ARCHIVE, required_ffmpeg_notices(target))


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


def add_licenses(built: Path, exe: Path, release: Path, app_name: str, target: str) -> list[str]:
    """Fill licenses/ in the release folder for every package, Python and ffmpeg in the build.

    `built` is PyInstaller's folder, with `exe` and _internal. Returns the packages.
    """
    licenses = release / LICENSES_DIR
    packages = bundled_packages(bundled_names(built, exe), installed_owners(), app_name)
    for name in packages:
        copy_licenses(package_license_files(name), licenses / name)
    python = python_license(PYTHON_LICENSES)
    copy_licenses([(PurePosixPath("LICENSE.txt"), python)], licenses / "python")
    for name, data in target_ffmpeg_notices(target).items():
        destination = licenses / "ffmpeg" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return packages
