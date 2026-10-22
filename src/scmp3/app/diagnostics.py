"""The text "Copy diagnostics" puts on the clipboard for a bug report.

It holds the versions, the OS, the settings and the latest log lines. Folders are cut to their last
part, and the whole text goes through the log's redaction, so it carries no token and no username.
"""

import json
import platform
from pathlib import Path, PurePath

from scmp3.core.log import redact
from scmp3.core.models import Settings, field_types

LOG_LINES = 200
# Settings shown only as set or not, since their value says nothing about a bug.
HIDDEN_SETTINGS = frozenset({"soundcloud_client_id"})


def diagnostics_text(
    *,
    app_version: str,
    engine_version: str | None,
    settings: Settings,
    log_file: Path,
    os_name: str | None = None,
) -> str:
    """Everything a bug report needs, redacted. `log_file` is the current log file."""
    lines = [
        "sc-mp3 diagnostics",
        f"App: sc-mp3 {app_version}",
        f"Download engine: yt-dlp {engine_version or 'version unknown'}",
        f"OS: {os_name or platform.platform()}",
        f"Python: {platform.python_version()}",
        "",
        "Settings:",
        *settings_lines(settings),
        "",
        f"Last {LOG_LINES} log lines:",
        *last_log_lines(log_file),
    ]
    return redact("\n".join(lines))


def settings_lines(settings: Settings) -> list[str]:
    """Each setting on its own line, with folders cut to their last part."""
    types = field_types(Settings)
    return [
        f"  {name}: {_shown(name, types[name], value)}"
        for name, value in settings.to_dict().items()
    ]


def _shown(name: str, kind: object, value: object) -> str:
    if name in HIDDEN_SETTINGS:
        return "set" if value else "not set"
    if kind is Path and isinstance(value, str):
        return PurePath(value).name
    return json.dumps(value, ensure_ascii=False)


def last_log_lines(log_file: Path, count: int = LOG_LINES) -> list[str]:
    """The log's last lines, reaching into the newest rotated file when the current one is short."""
    lines: list[str] = []
    for path in (log_file, log_file.with_name(f"{log_file.name}.1")):
        if len(lines) >= count:
            break
        lines = _read_lines(path) + lines
    return lines[-count:]


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    except OSError as error:
        return [f"(The log file {path.name} couldn't be read: {error.strerror or error})"]
