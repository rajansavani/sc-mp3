"""Build the Windows release: fetch ffmpeg, run PyInstaller, check the app folder, then zip it.

Usage: uv run python packaging/build.py
It writes the app folder to dist/sc-mp3/, then dist/sc-mp3-<version>-windows-x64.zip and a
.sha256 file beside it. Each step stops the build with exit code 1 when it fails.
"""

import hashlib
import importlib.metadata
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path, PureWindowsPath
from urllib.parse import quote

# packaging/ is on sys.path when this file runs as a script.
import bundle_licenses

PACKAGING_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGING_DIR.parent
SPEC = PACKAGING_DIR / "scmp3.spec"
FETCH_SCRIPT = PACKAGING_DIR / "fetch_binaries.py"
DIST_DIR = REPO_ROOT / "dist"
WORK_DIR = REPO_ROOT / "build"

APP_NAME = "sc-mp3"
TARGET_OS = "windows"
TARGET_ARCH = "x64"
# An editable install's metadata holds the builder's checkout path and build times (T-002).
LOCAL_ONLY_FILES = frozenset({"direct_url.json", "uv_cache.json"})
# Files from the repo that go next to sc-mp3.exe, by the name they get there; .txt opens in Notepad.
RELEASE_FILES = {"LICENSE": "LICENSE.txt", "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.txt"}

# A cold PyInstaller run takes about a minute, so this only stops a build that hangs.
STEP_TIMEOUT_SECONDS = 1800
VERSION_TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1024 * 1024
MB = 1_000_000

type Runner = Callable[..., subprocess.CompletedProcess[str]]
# Something in the app folder, by its name, with its bytes.
type Entry = tuple[str, bytes]


class BuildError(Exception):
    """A build step failed. The message says which one and what to look at."""


def zip_name(version: str) -> str:
    return f"{APP_NAME}-{version}-{TARGET_OS}-{TARGET_ARCH}.zip"


def run_step(run: Runner, description: str, command: Sequence[str]) -> None:
    """Run one build command with its output shown, and stop the build if it fails."""
    print(f"{description}...", flush=True)
    try:
        completed = run(command, cwd=REPO_ROOT, timeout=STEP_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as error:
        raise BuildError(f"{description} took over {STEP_TIMEOUT_SECONDS} s.") from error
    if completed.returncode != 0:
        raise BuildError(f"{description} failed with exit code {completed.returncode}.")


def fetch_ffmpeg(run: Runner) -> None:
    """Download and verify the pinned ffmpeg into packaging/bin, or reuse the verified copy."""
    command = [sys.executable, str(FETCH_SCRIPT), "--os", TARGET_OS, "--arch", TARGET_ARCH]
    run_step(run, "Fetching ffmpeg", command)


def run_pyinstaller(run: Runner, dist_dir: Path) -> None:
    """Build the app folder from scmp3.spec, starting from a clean cache."""
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(WORK_DIR),
        str(SPEC),
    ]
    run_step(run, "Running PyInstaller", command)


def bundle_files(folder: Path) -> Iterator[Path]:
    """Every file in the app folder, in a fixed order."""
    return (path for path in sorted(folder.rglob("*")) if path.is_file())


def is_local_only(name: str) -> bool:
    """Whether a file named in the bundle or in a RECORD holds the builder's paths or times."""
    return PureWindowsPath(name).name in LOCAL_ONLY_FILES or name.endswith(".pth")


def local_only_files(folder: Path) -> list[str]:
    """The metadata files, and the RECORD lines for them, that would say where the app was built."""
    found = []
    for path in bundle_files(folder):
        name = str(path.relative_to(folder))
        if is_local_only(path.name):
            found.append(name)
        elif path.name == "RECORD" and path.parent.name.endswith(".dist-info"):
            # Each line is "<path>,<hash>,<size>", and the size and hash can confirm a guess.
            listed = [line.split(",")[0] for line in path.read_text(encoding="utf-8").splitlines()]
            found.extend(f"{name} line for {entry}" for entry in listed if is_local_only(entry))
    return found


def path_spellings(path: str) -> list[str]:
    """A path as a file can hold it, casefolded: either slash, doubled backslashes, URL-encoded."""
    windows = PureWindowsPath(path)
    drive, *names = windows.as_posix().split("/")
    url = "/".join([drive, *(quote(name) for name in names)])
    forms = {str(windows), str(windows).replace("\\", "\\\\"), windows.as_posix(), url}
    return sorted(form.casefold() for form in forms)


def decoded_texts(data: bytes) -> Iterator[str]:
    """The bytes read as UTF-8, and as UTF-16 starting at either byte, casefolded."""
    yield data.decode("utf-8", "replace").casefold()
    yield data.decode("utf-16-le", "replace").casefold()
    yield data[1:].decode("utf-16-le", "replace").casefold()


def packed_entries(exe: Path) -> Iterator[Entry]:
    """What PyInstaller packs into the exe, decompressed: its archive, and the PYZ of modules."""
    from PyInstaller.archive.readers import PKG_ITEM_PYZ, CArchiveReader

    archive = CArchiveReader(str(exe))
    for name, (*_, typecode) in archive.toc.items():
        if typecode != PKG_ITEM_PYZ:
            yield f"{exe.name}!{name}", archive.extract(name)
            continue
        modules = archive.open_embedded_archive(name)
        for module in modules.toc:
            # Raw is the marshalled code, whose strings are UTF-8; a namespace package has none.
            data = modules.extract(module, raw=True)
            if data is not None:
                yield f"{exe.name}!{name}!{module}", data


def bundle_entries(folder: Path) -> Iterator[Entry]:
    """Every file in the app folder with its bytes, then what's packed inside sc-mp3.exe."""
    for path in bundle_files(folder):
        yield str(path.relative_to(folder)), path.read_bytes()
    yield from packed_entries(folder / f"{APP_NAME}.exe")


def entries_naming(entries: Iterable[Entry], private_paths: Sequence[str]) -> list[str]:
    """The names of the entries that hold any of the private paths, in any letter case."""
    spellings = [spelling for path in private_paths for spelling in path_spellings(path)]
    return [
        name
        for name, data in entries
        if any(spelling in text for text in decoded_texts(data) for spelling in spellings)
    ]


def check_bundle(folder: Path, private_paths: Sequence[str]) -> None:
    """Stop the build if the app folder is missing, or would publish where it was built."""
    if not (folder / f"{APP_NAME}.exe").is_file():
        raise BuildError(f"PyInstaller didn't write {APP_NAME}.exe to {folder}.")
    leaks = local_only_files(folder) or entries_naming(bundle_entries(folder), private_paths)
    if leaks:
        raise BuildError(f"The app folder holds the builder's paths: {', '.join(leaks)}.")


def add_release_files(folder: Path) -> None:
    """Put the license and the notices next to sc-mp3.exe, where people who unzip the app look."""
    for source, name in RELEASE_FILES.items():
        try:
            shutil.copyfile(REPO_ROOT / source, folder / name)
        except OSError as error:
            raise BuildError(f"Couldn't copy {source} into the app folder: {error}") from error


def add_licenses(folder: Path) -> None:
    """Fill licenses/ next to sc-mp3.exe with the license files of everything bundled."""
    try:
        packages = bundle_licenses.add_licenses(folder, APP_NAME, f"{TARGET_OS}-{TARGET_ARCH}")
    except bundle_licenses.LicenseError as error:
        raise BuildError(str(error)) from error
    print(f"Licenses for {', '.join(packages)}, Python and ffmpeg", flush=True)


def check_version(run: Runner, exe: Path, version: str) -> None:
    """Start the built app with --version, which must print the version and exit with 0."""
    print("Checking the app's --version...", flush=True)
    # A windowed app prints only when its output is piped, as it is here.
    try:
        completed = run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise BuildError(
            f"{exe.name} --version didn't exit within {VERSION_TIMEOUT_SECONDS} s."
        ) from error
    printed = completed.stdout.strip()
    if completed.returncode != 0 or printed != version:
        raise BuildError(
            f"{exe.name} --version exited with {completed.returncode} and printed {printed!r}; "
            f"expected 0 and {version!r}."
        )


def write_zip(folder: Path, target: Path) -> None:
    """Zip the app folder so that it unzips as one folder named after the app."""
    partial = target.with_name(target.name + ".partial")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in bundle_files(folder):
                archive.write(path, Path(APP_NAME) / path.relative_to(folder))
        partial.replace(target)
    except OSError as error:
        raise BuildError(f"Couldn't write {target.name}: {error}") from error
    finally:
        # After the rename there's nothing here; after a failure this removes the half-written zip.
        partial.unlink(missing_ok=True)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def checksum_path(target: Path) -> Path:
    return target.with_name(target.name + ".sha256")


def write_checksum(target: Path) -> Path:
    """Write <zip>.sha256 in the format `sha256sum --check` reads."""
    checksum = checksum_path(target)
    checksum.write_text(f"{sha256_of(target)}  {target.name}\n", encoding="utf-8", newline="\n")
    return checksum


def folder_size(folder: Path) -> tuple[int, int]:
    """The total bytes and the number of files in a folder."""
    files = list(bundle_files(folder))
    return sum(path.stat().st_size for path in files), len(files)


def print_summary(folder: Path, target: Path, checksum: Path) -> None:
    total, count = folder_size(folder)
    print(f"App folder: {folder}, {total / MB:.1f} MB in {count} files")
    print(f"Zip: {target}, {target.stat().st_size / MB:.1f} MB")
    print(f"SHA-256: {checksum.read_text(encoding='utf-8').split()[0]}")


def build(run: Runner, version: str, dist_dir: Path) -> None:
    """Run every step, from fetching ffmpeg to writing the checksum."""
    target = dist_dir / zip_name(version)
    # A failed build must not leave the last build's zip looking like its result.
    target.unlink(missing_ok=True)
    checksum_path(target).unlink(missing_ok=True)
    fetch_ffmpeg(run)
    run_pyinstaller(run, dist_dir)
    folder = dist_dir / APP_NAME
    check_bundle(folder, [str(REPO_ROOT), str(Path.home())])
    add_release_files(folder)
    add_licenses(folder)
    check_version(run, folder / f"{APP_NAME}.exe", version)
    print(f"Writing {target.name}...", flush=True)
    write_zip(folder, target)
    print_summary(folder, target, write_checksum(target))


def main(run: Runner = subprocess.run, platform: str = sys.platform) -> int:
    """Build the release and return the exit code."""
    if platform != "win32":
        print("error: build.py makes the Windows release, so run it on Windows.", file=sys.stderr)
        return 1
    try:
        build(run, importlib.metadata.version(APP_NAME), DIST_DIR)
    except BuildError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
