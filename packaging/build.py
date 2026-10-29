"""Build the release for this machine: fetch ffmpeg, run PyInstaller, check the app, then zip it.

Usage: uv run python packaging/build.py
On Windows it writes dist/sc-mp3-<version>-windows-x64.zip, and on a Mac
dist/sc-mp3-<version>-macos-<arch>.zip for the Mac's own architecture, each with a .sha256 file
beside it. Both zips unpack to one sc-mp3 folder, which is also left in dist/. Each step stops the
build with exit code 1 when it fails.
"""

import hashlib
import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# packaging/ is on sys.path when this file runs as a script.
import bundle_check
import bundle_licenses
import fetch_binaries

PACKAGING_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGING_DIR.parent
SPEC = PACKAGING_DIR / "scmp3.spec"
FETCH_SCRIPT = PACKAGING_DIR / "fetch_binaries.py"
DIST_DIR = REPO_ROOT / "dist"
WORK_DIR = REPO_ROOT / "build"

APP_NAME = "sc-mp3"
# Files from the repo that go next to the app, by the name they get there; .txt opens in Notepad.
RELEASE_FILES = {"LICENSE": "LICENSE.txt", "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.txt"}

# GitHub's runners build as one of these accounts, whose home folder also turns up in the wheels
# that other projects built on those runners. It names no person, so there it doesn't count.
RUNNER_ACCOUNTS = frozenset({"runner", "runneradmin"})

# A cold PyInstaller run takes about a minute, so this only stops a build that hangs.
STEP_TIMEOUT_SECONDS = 1800
VERSION_TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1024 * 1024
MB = 1_000_000

type Runner = Callable[..., subprocess.CompletedProcess[str]]
# Writes the zip of a folder to a path.
type Zipper = Callable[[Runner, Path, Path], None]


class BuildError(Exception):
    """A build step failed. The message says which one and what to look at."""


def run_step(run: Runner, description: str, command: Sequence[str]) -> None:
    """Run one build command with its output shown, and stop the build if it fails."""
    print(f"{description}...", flush=True)
    try:
        completed = run(command, cwd=REPO_ROOT, timeout=STEP_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as error:
        raise BuildError(f"{description} took over {STEP_TIMEOUT_SECONDS} s.") from error
    if completed.returncode != 0:
        raise BuildError(f"{description} failed with exit code {completed.returncode}.")


def zip_with_python(run: Runner, folder: Path, target: Path) -> None:
    """Zip the app folder so that it unzips as one folder named after the app."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in bundle_check.bundle_files(folder):
            archive.write(path, Path(APP_NAME) / path.relative_to(folder))


def zip_with_ditto(run: Runner, folder: Path, target: Path) -> None:
    """Zip the app folder with macOS's ditto, which keeps the links the app's signature covers."""
    command = ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(folder), str(target)]
    run_step(run, f"Zipping {folder.name} with ditto", command)


@dataclass(frozen=True)
class Layout:
    """How one OS's app is laid out in the sc-mp3 folder that its zip holds."""

    # PyInstaller's program, in the folder it writes beside _internal.
    exe: str
    # The program people start, inside the sc-mp3 folder.
    launcher: str
    zipper: Zipper
    # The .app PyInstaller writes beside its folder on macOS, which the sc-mp3 folder then holds.
    bundle: str | None = None


LAYOUTS = {
    "windows": Layout(f"{APP_NAME}.exe", f"{APP_NAME}.exe", zip_with_python),
    "macos": Layout(
        APP_NAME,
        f"{APP_NAME}.app/Contents/MacOS/{APP_NAME}",
        zip_with_ditto,
        bundle=f"{APP_NAME}.app",
    ),
}


@dataclass(frozen=True)
class Target:
    """The OS and architecture a build is for, named as in binaries.lock."""

    os_name: str
    arch: str

    @property
    def name(self) -> str:
        return f"{self.os_name}-{self.arch}"

    @property
    def layout(self) -> Layout:
        return LAYOUTS[self.os_name]


def host_target(system: str, machine: str) -> Target | None:
    """The target this machine builds, or None when it can't build a release."""
    os_name, arch = fetch_binaries.host_target(system, machine)
    if os_name not in LAYOUTS or arch is None:
        return None
    return Target(os_name, arch)


def zip_name(version: str, target: Target) -> str:
    return f"{APP_NAME}-{version}-{target.name}.zip"


def fetch_ffmpeg(run: Runner, target: Target) -> None:
    """Download and verify the pinned ffmpeg into packaging/bin, or reuse the verified copy."""
    command = [sys.executable, str(FETCH_SCRIPT), "--os", target.os_name, "--arch", target.arch]
    run_step(run, "Fetching ffmpeg", command)


def run_pyinstaller(run: Runner, dist_dir: Path, target: Target) -> None:
    """Build the app from scmp3.spec into dist_dir, starting from a clean cache."""
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
        "--",
        "--target",
        target.name,
    ]
    run_step(run, "Running PyInstaller", command)


def gather_bundle(bundle: Path, folder: Path) -> None:
    """Move the .app PyInstaller wrote into a new sc-mp3 folder, where its licenses go too."""
    try:
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        # A move within one drive is a rename, so the links inside the .app stay links.
        shutil.move(bundle, folder / bundle.name)
    except OSError as error:
        raise BuildError(f"Couldn't move {bundle.name} into {folder}: {error}") from error


def verify_signature(run: Runner, bundle: Path) -> None:
    """Check PyInstaller's ad-hoc signature, since it only warns when signing fails."""
    command = ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(bundle)]
    run_step(run, f"Checking the signature of {bundle.name}", command)


def builder_paths(environ: Mapping[str, str], home: Path) -> list[str]:
    """The paths the app mustn't hold: the checkout, and the home folder unless it's a runner's."""
    if environ.get("GITHUB_ACTIONS") == "true" and home.name in RUNNER_ACCOUNTS:
        return [str(REPO_ROOT)]
    return [str(REPO_ROOT), str(home)]


def check_bundle(folder: Path, launcher: Path, private_paths: Sequence[str]) -> None:
    """Stop the build if the app is missing, or would publish where it was built."""
    if not launcher.is_file():
        raise BuildError(f"PyInstaller didn't write {launcher.relative_to(folder)} to {folder}.")
    leaks = bundle_check.leaks(folder, launcher, private_paths)
    if leaks:
        raise BuildError(f"The app folder holds the builder's paths: {', '.join(leaks)}.")


def add_release_files(folder: Path) -> None:
    """Put the license and the notices next to the app, where people who unzip it look."""
    for source, name in RELEASE_FILES.items():
        try:
            shutil.copyfile(REPO_ROOT / source, folder / name)
        except OSError as error:
            raise BuildError(f"Couldn't copy {source} into the app folder: {error}") from error


def add_licenses(built: Path, folder: Path, target: Target) -> None:
    """Fill licenses/ next to the app with the license files of what PyInstaller bundled."""
    exe = built / target.layout.exe
    try:
        packages = bundle_licenses.add_licenses(built, exe, folder, APP_NAME, target.name)
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


def write_zip(run: Runner, folder: Path, target: Path, zipper: Zipper) -> None:
    """Zip the app folder with `zipper`, leaving no zip behind when that fails."""
    partial = target.with_name(target.name + ".partial")
    try:
        zipper(run, folder, partial)
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
    """The total bytes and the number of files in a folder, not counting links twice."""
    files = [path for path in bundle_check.bundle_files(folder) if not path.is_symlink()]
    return sum(path.stat().st_size for path in files), len(files)


def print_summary(folder: Path, target: Path, checksum: Path) -> None:
    total, count = folder_size(folder)
    print(f"App folder: {folder}, {total / MB:.1f} MB in {count} files")
    print(f"Zip: {target}, {target.stat().st_size / MB:.1f} MB")
    print(f"SHA-256: {checksum.read_text(encoding='utf-8').split()[0]}")


def build(run: Runner, version: str, target: Target, dist_dir: Path) -> None:
    """Run every step, from fetching ffmpeg to writing the checksum."""
    zip_path = dist_dir / zip_name(version, target)
    # A failed build must not leave the last build's zip looking like its result.
    zip_path.unlink(missing_ok=True)
    checksum_path(zip_path).unlink(missing_ok=True)
    layout = target.layout
    fetch_ffmpeg(run, target)
    # PyInstaller's folder ships as it is on Windows; on macOS only the .app from beside it does.
    pyinstaller_dist = dist_dir if layout.bundle is None else WORK_DIR / "dist"
    run_pyinstaller(run, pyinstaller_dist, target)
    folder = dist_dir / APP_NAME
    if layout.bundle is not None:
        gather_bundle(pyinstaller_dist / layout.bundle, folder)
        verify_signature(run, folder / layout.bundle)
    check_bundle(folder, folder / layout.launcher, builder_paths(os.environ, Path.home()))
    add_release_files(folder)
    add_licenses(pyinstaller_dist / APP_NAME, folder, target)
    check_version(run, folder / layout.launcher, version)
    print(f"Writing {zip_path.name}...", flush=True)
    write_zip(run, folder, zip_path, layout.zipper)
    print_summary(folder, zip_path, write_checksum(zip_path))


def main(
    run: Runner = subprocess.run, system: str | None = None, machine: str | None = None
) -> int:
    """Build the release for this machine, or the one given, and return the exit code."""
    system = system or platform.system()
    machine = machine or platform.machine()
    target = host_target(system, machine)
    if target is None:
        print(
            f"error: build.py makes the Windows and macOS releases, not one for {system} on "
            f"{machine}, so run it on Windows or a Mac.",
            file=sys.stderr,
        )
        return 1
    try:
        build(run, importlib.metadata.version(APP_NAME), target, DIST_DIR)
    except BuildError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
