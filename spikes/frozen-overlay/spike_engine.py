"""Load yt-dlp from a zipapp overlay at sys.path[0], or from the bundle if the overlay can't load.

yt-dlp patches requests and urllib3 when it's imported, so it must be imported once per process.
An overlay is checked before its code runs, and one that fails after that needs a restart.
"""

import importlib
import importlib.util
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

ENGINE_PREFIX = "yt_dlp"
# Like the real app's components folder, the overlay's status sits next to it.
STATUS_FILE = "manifest.json"


@dataclass
class EngineState:
    """Which yt-dlp is active, where it came from, and why an overlay wasn't used."""

    source: str
    version: str
    location: str
    overlay: str | None
    skipped_reason: str | None
    import_seconds: float
    module_origins: dict[str, int] = field(default_factory=dict)


class OverlayFailedError(Exception):
    """The overlay's code ran and failed, so this process can't fall back any more."""


def is_inside(path: str | None, folder: str) -> bool:
    """Tell whether a module file lives inside a zip or folder."""
    if not path:
        return False
    return os.path.normcase(path).startswith(os.path.normcase(folder) + os.sep)


def engine_module_names() -> list[str]:
    """List the loaded yt_dlp, yt_dlp_ejs and yt_dlp_plugins modules."""
    return [name for name in sys.modules if name.startswith(ENGINE_PREFIX)]


def count_origins(overlay: str | None) -> dict[str, int]:
    """Count loaded engine modules by where they were loaded from."""
    counts = {"overlay": 0, "elsewhere": 0, "no_file": 0}
    for name in engine_module_names():
        file = getattr(sys.modules[name], "__file__", None)
        if file is None:
            counts["no_file"] += 1
        elif overlay and is_inside(file, overlay):
            counts["overlay"] += 1
        else:
            counts["elsewhere"] += 1
    return counts


def read_status(overlay: Path) -> dict[str, Any] | None:
    """Return the overlay's recorded status, or None when it has none."""
    path = overlay.parent / STATUS_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_status(overlay: Path, status: str, detail: str) -> None:
    """Record the overlay as good or bad, with the version or the reason."""
    record = {"status": status, "detail": detail, "at": datetime.now(UTC).isoformat()}
    (overlay.parent / STATUS_FILE).write_text(json.dumps(record, indent=2), encoding="utf-8")


def spec_problem(entry: str) -> str | None:
    """Say why yt_dlp wouldn't load from the overlay, without running any of its code."""
    try:
        spec = importlib.util.find_spec("yt_dlp")
    except Exception as error:
        return f"find_spec raised {type(error).__name__}: {error}"
    if spec is None or not is_inside(spec.origin, entry):
        # A missing, unreadable or non-zip overlay is skipped, and the bundle answers instead.
        return f"yt_dlp would load from {spec.origin if spec else 'nowhere'}"
    return None


def take_off_path(entry: str) -> None:
    """Remove the overlay from sys.path and from the path importer cache."""
    while entry in sys.path:
        sys.path.remove(entry)
    sys.path_importer_cache.pop(entry, None)
    importlib.invalidate_caches()


def import_engine() -> tuple[ModuleType, float]:
    """Import yt_dlp and yt_dlp.version, and return the package with the import time."""
    started = time.perf_counter()
    module = importlib.import_module("yt_dlp")
    importlib.import_module("yt_dlp.version")
    return module, time.perf_counter() - started


def smoke_test(module: ModuleType) -> None:
    """Make a YoutubeDL and look up one extractor, which fails fast on a broken engine."""
    with module.YoutubeDL({"quiet": True, "logger": _SilentLogger()}) as ydl:
        ydl.get_info_extractor("Soundcloud")


def load_bundled(skipped_reason: str | None, overlay: str | None) -> EngineState:
    """Import the yt-dlp that PyInstaller bundled."""
    module, seconds = import_engine()
    return EngineState(
        source="bundled",
        version=module.version.__version__,
        location=str(module.__file__),
        overlay=overlay,
        skipped_reason=skipped_reason,
        import_seconds=seconds,
        module_origins=count_origins(overlay),
    )


def load_overlay(entry: str) -> EngineState:
    """Import yt-dlp from the overlay and raise if it's broken or wasn't the copy imported."""
    module, seconds = import_engine()
    if not is_inside(module.__file__, entry):
        raise RuntimeError(f"yt_dlp was imported from {module.__file__}")
    smoke_test(module)
    return EngineState(
        source="overlay",
        version=module.version.__version__,
        location=str(module.__file__),
        overlay=entry,
        skipped_reason=None,
        import_seconds=seconds,
        module_origins=count_origins(entry),
    )


def load_engine(overlay: Path | None) -> EngineState:
    """Use the overlay when it can be used, and otherwise import the bundled copy once."""
    if engine_module_names():
        raise RuntimeError("yt_dlp was imported before the overlay was set up.")
    if overlay is None:
        return load_bundled(None, None)
    entry = str(overlay)
    status = read_status(overlay)
    if status and status["status"] == "bad":
        return load_bundled(f"marked bad: {status['detail']}", entry)
    sys.path.insert(0, entry)
    problem = spec_problem(entry)
    if problem:
        take_off_path(entry)
        return load_bundled(problem, entry)
    try:
        return load_overlay(entry)
    except Exception as error:
        # Its code has run and patched other packages, so only a new process can drop it.
        reason = f"{type(error).__name__}: {error}"
        write_status(overlay, "bad", reason)
        raise OverlayFailedError(reason) from error


def check_overlay(overlay: Path) -> str:
    """Import and smoke-test an overlay with no fallback, and return its version."""
    if engine_module_names():
        raise RuntimeError("yt_dlp was imported before the check.")
    entry = str(overlay)
    sys.path.insert(0, entry)
    problem = spec_problem(entry)
    if problem:
        raise RuntimeError(problem)
    return load_overlay(entry).version


class _SilentLogger:
    """A yt-dlp logger that drops everything, used for the smoke test."""

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass
