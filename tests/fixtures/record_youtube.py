"""Record YouTube fixtures for tests that replay them without the network.

Each fixture holds yt-dlp's info dict for one video, cut down to the fields sc-mp3 reads. Stream
and thumbnail links lose their query strings, which hold signatures, and the description keeps
only its first and last lines. It needs YouTube support: Deno from SCMP3_HOME, or one on PATH.

    uv run python tests/fixtures/record_youtube.py NAME=URL [NAME=URL ...]
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from scmp3.core import engine
from scmp3.core.sources import youtube
from scmp3.core.sources.deno import find_deno

FIXTURES_DIR = Path(__file__).parent / "youtube"

# The top-level fields kept, which are the ones core/sources/youtube.py reads, and a few more
# that make a fixture easy to recognize.
INFO_KEYS = (
    "id",
    "title",
    "uploader",
    "channel",
    "description",
    "track",
    "artists",
    "album",
    "release_year",
    "release_date",
    "upload_date",
    "duration",
    "live_status",
    "webpage_url",
)
FORMAT_KEYS = (
    "format_id",
    "ext",
    "acodec",
    "vcodec",
    "abr",
    "tbr",
    "language",
    "language_preference",
    "preference",
    "has_drm",
    "protocol",
    "format_note",
)
THUMBNAIL_KEYS = ("id", "url", "width", "height", "preference")
# How much of a description is kept at each end. A Topic upload's footer is at the very end.
DESCRIPTION_LINES = 3
# Video streams kept besides the audio ones, so tests see that they're left out.
VIDEO_FORMATS = 2

_NAME = re.compile(r"[a-z0-9_]+")


def trim(info: dict[str, Any]) -> dict[str, Any]:
    """The fields a fixture keeps, without signed links."""
    trimmed = {key: info.get(key) for key in INFO_KEYS}
    trimmed["description"] = _short_description(info.get("description"))
    trimmed["webpage_url"] = _without_query(info.get("webpage_url"), keep_video=True)
    trimmed["thumbnails"] = [_thumbnail(thumbnail) for thumbnail in info.get("thumbnails") or []]
    formats = info.get("formats") or []
    audio = [fmt for fmt in formats if fmt.get("vcodec") == "none"]
    video = [fmt for fmt in formats if fmt.get("vcodec") != "none"][:VIDEO_FORMATS]
    trimmed["formats"] = [{key: fmt.get(key) for key in FORMAT_KEYS} for fmt in audio + video]
    return trimmed


def _short_description(text: str | None) -> str | None:
    if not text:
        return text
    lines = text.splitlines()
    if len(lines) <= 2 * DESCRIPTION_LINES:
        return text
    return "\n".join([*lines[:DESCRIPTION_LINES], "…", *lines[-DESCRIPTION_LINES:]])


def _thumbnail(thumbnail: dict[str, Any]) -> dict[str, Any]:
    kept = {key: thumbnail.get(key) for key in THUMBNAIL_KEYS}
    kept["url"] = _without_query(thumbnail.get("url"))
    return kept


def _without_query(url: str | None, *, keep_video: bool = False) -> str | None:
    """The link without its query, which holds signatures, except a watch link's video id."""
    if not url:
        return url
    parts = urlsplit(url)
    query = parts.query if keep_video and parts.query.startswith("v=") else ""
    return urlunsplit(parts._replace(query=query.split("&")[0]))


def record(url: str) -> dict[str, Any]:
    """Extract one video and return the trimmed fixture."""
    deno = find_deno()
    if deno is None:
        raise SystemExit("Recording needs YouTube support. Install Deno first.")
    with youtube.make_ydl(deno.path) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "url": url,
        "recorded": datetime.now(UTC).date().isoformat(),
        "yt_dlp": engine.engine_version(),
        "info": trim(ydl.sanitize_info(info)),
    }


def save(name: str, fixture: dict[str, Any], folder: Path = FIXTURES_DIR) -> Path:
    """Write a fixture as <name>.json, with LF line endings."""
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}.json"
    text = json.dumps(fixture, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def load(name: str, folder: Path = FIXTURES_DIR) -> dict[str, Any]:
    """Read one fixture's info dict by name."""
    return json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))["info"]


def main(argv: list[str]) -> int:
    """Record each NAME=URL pair into tests/fixtures/youtube/NAME.json."""
    pairs = [arg.partition("=") for arg in argv]
    if not pairs or any(not sep or not _NAME.fullmatch(name) for name, sep, _ in pairs):
        print(__doc__, file=sys.stderr)
        return 2
    for name, _, url in pairs:
        path = save(name, record(url))
        print(f"Saved {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
