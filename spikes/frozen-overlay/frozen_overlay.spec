# PyInstaller spec for the frozen-overlay spike: onedir, windowed, WinForms + WebView2 only.
# build.py runs it. Spec options go after "--", for example "-- --strip-metadata".
import argparse
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

spec_parser = argparse.ArgumentParser(prog="frozen_overlay.spec")
spec_parser.add_argument("--name", default="frozen-overlay")
# Skips copy_metadata("sc-mp3"), to see whether PyInstaller collects the metadata by itself.
spec_parser.add_argument("--no-copy-metadata", action="store_true")
# Also removes the metadata PyInstaller collected, to show that `import scmp3` needs it.
spec_parser.add_argument("--strip-metadata", action="store_true")
# Builds without EXCLUDES, to measure what they change.
spec_parser.add_argument("--no-excludes", action="store_true")
spec_options = spec_parser.parse_args()

SPIKE_DIR = Path(SPECPATH)

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

skip_copy = spec_options.no_copy_metadata or spec_options.strip_metadata
datas = [] if skip_copy else copy_metadata("sc-mp3")

a = Analysis(
    [str(SPIKE_DIR / "spike_app.py")],
    pathex=[str(SPIKE_DIR)],
    datas=datas,
    hiddenimports=[],
    excludes=[] if spec_options.no_excludes else EXCLUDES,
    noarchive=False,
)

def is_sc_mp3_metadata(dest: str) -> bool:
    return Path(dest).parts[0].startswith("sc_mp3-")


# An editable install's metadata holds the builder's checkout path and build times.
LOCAL_ONLY_METADATA = ("direct_url.json", "uv_cache.json")
a.datas = [
    entry
    for entry in a.datas
    if not (is_sc_mp3_metadata(entry[0]) and Path(entry[0]).name in LOCAL_ONLY_METADATA)
]
if spec_options.strip_metadata:
    # PyInstaller finds version("sc-mp3") in scmp3's bytecode and collects the metadata itself.
    a.datas = [entry for entry in a.datas if not is_sc_mp3_metadata(entry[0])]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=spec_options.name,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=spec_options.name, upx=False)
