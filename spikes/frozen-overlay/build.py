"""Build the spike app with PyInstaller, keep the log, and print the bundle's size and warnings."""

import argparse
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import PyInstaller

SPIKE_DIR = Path(__file__).absolute().parent
SPEC = SPIKE_DIR / "frozen_overlay.spec"
DIST_DIR = SPIKE_DIR / "dist"
BUILD_DIR = SPIKE_DIR / "build"
BUILD_TIMEOUT_SECONDS = 900
MB = 1024 * 1024

# Each variant's bundle name and the spec options that make it.
VARIANTS = {
    "full": ("frozen-overlay", []),
    "no-copy-metadata": ("frozen-overlay-no-copy-metadata", ["--no-copy-metadata"]),
    "no-metadata": ("frozen-overlay-no-metadata", ["--strip-metadata"]),
    "no-excludes": ("frozen-overlay-no-excludes", ["--no-excludes"]),
}


def run_pyinstaller(name: str, spec_args: list[str]) -> tuple[int, str, float]:
    """Run PyInstaller on the spec and return its exit code, its log and the build time."""
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR / name),
        str(SPEC),
        "--",
        "--name",
        name,
        *spec_args,
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=SPIKE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=BUILD_TIMEOUT_SECONDS,
        check=False,
    )
    return completed.returncode, completed.stdout + completed.stderr, time.perf_counter() - started


def folder_size(folder: Path) -> tuple[int, int]:
    """Return the total bytes and the number of files under a folder."""
    files = [path for path in folder.rglob("*") if path.is_file()]
    return sum(path.stat().st_size for path in files), len(files)


def largest_entries(folder: Path, count: int) -> list[tuple[str, int]]:
    """List the biggest top-level items in the bundle's _internal folder."""
    internal = folder / "_internal"
    sizes = []
    for entry in internal.iterdir():
        size = entry.stat().st_size if entry.is_file() else folder_size(entry)[0]
        sizes.append((entry.name, size))
    return sorted(sizes, key=lambda item: item[1], reverse=True)[:count]


def zipped_size(folder: Path, target: Path) -> int:
    """Zip the bundle the way a release would ship it, and return the zip's size."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in folder.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(folder.parent))
    return target.stat().st_size


def print_summary(name: str, log: str, seconds: float) -> None:
    """Print the facts the README records about a build."""
    bundle = DIST_DIR / name
    total, files = folder_size(bundle)
    zip_bytes = zipped_size(bundle, BUILD_DIR / f"{name}.zip")
    warnings = sorted({line.strip() for line in log.splitlines() if " WARNING: " in line})
    exe_bytes = (bundle / f"{name}.exe").stat().st_size
    print(f"PyInstaller {PyInstaller.__version__}, built in {seconds:.0f} s")
    print(f"Bundle: {bundle.relative_to(SPIKE_DIR)}, {total / MB:.1f} MB in {files} files")
    print(f"Zipped: {zip_bytes / MB:.1f} MB")
    print(f"{name}.exe, which holds the Python modules: {exe_bytes / MB:.1f} MB")
    print("Largest items in _internal:")
    for entry, size in largest_entries(bundle, 12):
        print(f"  {size / MB:7.1f} MB  {entry}")
    print(f"Warnings in the build log ({len(warnings)}):")
    for line in warnings:
        print(f"  {line}")


def main() -> int:
    """Build one variant of the spike app and return PyInstaller's exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=VARIANTS, default="full")
    options = parser.parse_args()
    name, spec_args = VARIANTS[options.variant]
    BUILD_DIR.mkdir(exist_ok=True)
    code, log, seconds = run_pyinstaller(name, spec_args)
    log_path = BUILD_DIR / f"{name}.log"
    log_path.write_text(log, encoding="utf-8")
    if code != 0:
        print(f"PyInstaller failed with exit code {code}. See {log_path}.", file=sys.stderr)
        return code
    print_summary(name, log, seconds)
    print(f"Full log: {log_path.relative_to(SPIKE_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
