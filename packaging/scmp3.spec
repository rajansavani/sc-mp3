# PyInstaller spec for the Windows and macOS releases: one folder with no console window, and on
# macOS the sc-mp3.app around it. packaging/build.py runs it after fetching ffmpeg, passing the
# target after "--", so run that rather than PyInstaller on its own.
import argparse
import re
from importlib.metadata import version
from pathlib import Path, PureWindowsPath

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

PACKAGING_DIR = Path(SPECPATH)
REPO_ROOT = PACKAGING_DIR.parent
APP_NAME = "sc-mp3"
APP_VERSION = version("sc-mp3")
ENTRY_SCRIPT = REPO_ROOT / "src" / "scmp3" / "__main__.py"
TARGETS = ("windows-x64", "macos-arm64", "macos-x64")
FFMPEG_NAMES = {"windows": "ffmpeg.exe", "macos": "ffmpeg"}

# pywebview backends no release uses, and the toolkits they would pull in.
EXCLUDES = [
    "webview.platforms.android",
    "webview.platforms.cef",
    "webview.platforms.gtk",
    "webview.platforms.qt",
    "cefpython3",
    "gi",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "qtpy",
]
# The backends of the other OS, by the OS being built.
OTHER_BACKENDS = {
    "windows": ["webview.platforms.cocoa"],
    "macos": [
        "webview.platforms.edgechromium",
        "webview.platforms.mshtml",
        "webview.platforms.winforms",
    ],
}

# An editable install's metadata holds the builder's checkout path and build times, and its .pth
# file holds the path of src/. A wheel's DELVEWHEEL holds the temp paths of the machine that
# repaired it, which on a GitHub runner match the runner's home folder.
LOCAL_ONLY_METADATA = {"direct_url.json", "uv_cache.json", "DELVEWHEEL"}

# "en-US, Unicode", the language and code page of the version strings.
VERSION_LANGUAGE = "040904B0"
VERSION_TRANSLATION = [0x0409, 1200]

# The usual form for a project hosted on GitHub. The Keychain and macOS's privacy settings know
# the app by it, so it stays the same from release to release.
BUNDLE_ID = "io.github.rajansavani.sc-mp3"
# Martin Riedl's ffmpeg 9.0.2 builds need macOS 12, the newest of any bundled binary.
MINIMUM_MACOS = "12.0"
# PyInstaller's names for the architectures, which it thins universal2 binaries down to.
MACOS_ARCHS = {"arm64": "arm64", "x64": "x86_64"}


def read_target() -> tuple[str, str]:
    """The OS and architecture build.py passes after "--", like ("macos", "arm64")."""
    parser = argparse.ArgumentParser(prog="scmp3.spec")
    parser.add_argument("--target", choices=TARGETS, required=True)
    os_name, arch = parser.parse_args().target.split("-")
    return os_name, arch


def is_local_only(name: str) -> bool:
    """Whether a file, named by its path or in a RECORD line, is one of the local-only ones."""
    # Either slash, since PyInstaller names files with backslashes and RECORD with slashes.
    return PureWindowsPath(name).name in LOCAL_ONLY_METADATA or name.endswith(".pth")


def is_dist_info(dest_name: str) -> bool:
    return Path(dest_name).parent.name.endswith(".dist-info")


def scrubbed_record(source: str) -> str:
    """Copy a RECORD without the lines for local-only files, and return the copy's path."""
    # A line's hash and size could confirm a guess at the builder's paths.
    lines = Path(source).read_bytes().splitlines(keepends=True)
    kept = [line for line in lines if not is_local_only(line.split(b",")[0].decode("utf-8"))]
    target = Path(workpath) / "records" / Path(source).parent.name / "RECORD"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"".join(kept))
    return str(target)


def without_local_paths(datas: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """The data files minus local-only metadata, with each RECORD rewritten to match."""
    kept = []
    for dest, source, typecode in datas:
        if is_dist_info(dest) and is_local_only(dest):
            continue
        if is_dist_info(dest) and Path(dest).name == "RECORD":
            source = scrubbed_record(source)
        kept.append((dest, source, typecode))
    return kept


def windows_version(text: str) -> tuple[int, int, int, int]:
    """Windows wants four numbers, so 0.0.1 becomes (0, 0, 1, 0) and 1.2rc1 becomes (1, 2, 0, 0)."""
    release = re.match(r"\d+(\.\d+)*", text)
    numbers = [int(part) for part in release.group().split(".")][:4] if release else []
    return tuple(numbers + [0] * (4 - len(numbers)))


def version_resource():
    """The version details Explorer shows in the exe's Properties."""
    # Only on Windows, since the module needs pefile, which only Windows builds install.
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = windows_version(APP_VERSION)
    strings = {
        "FileDescription": APP_NAME,
        "FileVersion": APP_VERSION,
        "InternalName": APP_NAME,
        "LegalCopyright": "Licensed under GPL-3.0-or-later",
        "OriginalFilename": f"{APP_NAME}.exe",
        "ProductName": APP_NAME,
        "ProductVersion": APP_VERSION,
    }
    table = StringTable(VERSION_LANGUAGE, [StringStruct(k, v) for k, v in strings.items()])
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([table]),
            VarFileInfo([VarStruct("Translation", VERSION_TRANSLATION)]),
        ],
    )


def check_minimum_macos(binaries: list[tuple[str, str, str]]) -> None:
    """Stop the build if a binary needs a newer macOS than Info.plist says the app runs on."""
    from PyInstaller.utils.osx import macosx_version_min

    floor = tuple(int(part) for part in MINIMUM_MACOS.split("."))
    # Compared by major and minor version, as LSMinimumSystemVersion gives them.
    newer = [dest for dest, source, _ in binaries if macosx_version_min(source)[:2] > floor]
    if newer:
        raise SystemExit(f"error: These need a macOS newer than {MINIMUM_MACOS}: {newer}")


def info_plist() -> dict[str, str]:
    """What Info.plist holds beyond PyInstaller's own keys, like the name and short version."""
    return {
        "CFBundleVersion": APP_VERSION,
        "LSMinimumSystemVersion": MINIMUM_MACOS,
        "NSHumanReadableCopyright": "Licensed under GPL-3.0-or-later",
    }


OS_NAME, ARCH = read_target()
# Only the binary: the verified ffmpeg.zip next to it stays out of the bundle (T-004).
FFMPEG = PACKAGING_DIR / "bin" / f"{OS_NAME}-{ARCH}" / FFMPEG_NAMES[OS_NAME]
ON_MACOS = OS_NAME == "macos"

datas = [
    # The UI under scmp3/web, where app/main.py looks, plus the TOML files read through
    # importlib.resources, which PyInstaller doesn't follow by itself.
    *collect_data_files("scmp3"),
    # scmp3.__version__ reads this, and import scmp3 fails without it.
    *copy_metadata("sc-mp3"),
    # The engine's version in snapshot() comes from here.
    *copy_metadata("yt-dlp"),
]

a = Analysis(
    [str(ENTRY_SCRIPT)],
    binaries=[(str(FFMPEG), ".")],
    datas=datas,
    excludes=EXCLUDES + OTHER_BACKENDS[OS_NAME],
    noarchive=False,
)
a.datas = without_local_paths(a.datas)
if ON_MACOS:
    check_minimum_macos(a.binaries)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    icon=None if ON_MACOS else str(PACKAGING_DIR / "icons" / "app.ico"),
    version=None if ON_MACOS else version_resource(),
    console=False,
    upx=False,
    target_arch=MACOS_ARCHS[ARCH] if ON_MACOS else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)
if ON_MACOS:
    # Signed ad hoc, PyInstaller's default without a Developer ID.
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(PACKAGING_DIR / "icons" / "app.icns"),
        bundle_identifier=BUNDLE_ID,
        version=APP_VERSION,
        info_plist=info_plist(),
    )
