# PyInstaller spec for the Windows release: one folder, no console window. packaging/build.py
# runs it after fetching ffmpeg, so run that rather than PyInstaller on its own.
import re
from importlib.metadata import version
from pathlib import Path, PureWindowsPath

from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

PACKAGING_DIR = Path(SPECPATH)
REPO_ROOT = PACKAGING_DIR.parent
APP_NAME = "sc-mp3"
APP_VERSION = version("sc-mp3")
ENTRY_SCRIPT = REPO_ROOT / "src" / "scmp3" / "__main__.py"
ICON = PACKAGING_DIR / "icons" / "app.ico"
# Only the binary: the verified ffmpeg.zip next to it stays out of the bundle (T-004).
FFMPEG = PACKAGING_DIR / "bin" / "windows-x64" / "ffmpeg.exe"

# pywebview backends the Windows build never uses, and the toolkits they would pull in.
EXCLUDES = [
    "webview.platforms.android",
    "webview.platforms.cef",
    "webview.platforms.cocoa",
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

# An editable install's metadata holds the builder's checkout path and build times, and its .pth
# file holds the path of src/.
LOCAL_ONLY_METADATA = {"direct_url.json", "uv_cache.json"}

# "en-US, Unicode", the language and code page of the version strings.
VERSION_LANGUAGE = "040904B0"
VERSION_TRANSLATION = [0x0409, 1200]


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


def version_resource() -> VSVersionInfo:
    """The version details Explorer shows in the exe's Properties."""
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
    excludes=EXCLUDES,
    noarchive=False,
)
a.datas = without_local_paths(a.datas)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    icon=str(ICON),
    version=version_resource(),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)
