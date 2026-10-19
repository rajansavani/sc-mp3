"""Record SoundCloud fixtures, and replay them in tests without the network.

Each fixture holds yt-dlp's info dict and api-v2's /resolve answer for one track, or the error
each gave. Tokens, the client_id and signed-URL parameters are scrubbed before anything is saved.

    uv run python tests/fixtures/record.py NAME=URL [NAME=URL ...]
"""

from __future__ import annotations

import copy
import io
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from yt_dlp import YoutubeDL
from yt_dlp.networking.common import Request, Response
from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.utils import DownloadError, ExtractorError, GeoRestrictedError

from scmp3.core import engine
from scmp3.core.errors import ScmpError
from scmp3.core.log import MASK, redact
from scmp3.core.sources import soundcloud
from scmp3.core.urls import normalize_soundcloud

FIXTURES_DIR = Path(__file__).parent / "soundcloud"

# What replays hand out as the client_id: 32 characters, like the real ones, but not one.
FAKE_CLIENT_ID = "0" * 32

# Query parameters dropped from every URL: CloudFront's and S3's signatures, and credentials.
SIGNED_PARAMS = frozenset(
    {
        "policy",
        "signature",
        "key-pair-id",
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "client_id",
        "secret_token",
        "oauth_token",
    }
)
# Keys dropped wherever they appear, with their values: tokens, cookies and credentials.
_SECRET_KEY = re.compile(r"(token|authorization|password|cookies?|client_id)$", re.IGNORECASE)
# Uploaders' personal details. Nothing reads them, and the fixtures are published.
PERSONAL_KEYS = frozenset(
    {"first_name", "last_name", "full_name", "date_of_birth", "city", "country_code"}
)

_URL = re.compile(r"https?://[^\s\"'<>]+")
# A client_id written out in text, as in "client_id=…" or "client_id: …", outside any URL.
_CLIENT_ID_IN_TEXT = re.compile(r"(client_id[\"']?\s*(?:[:=]|%3D)\s*[\"']?)[^\s\"'&;,]+", re.I)
# Punctuation that ends a sentence after a URL in a message, rather than belonging to it.
_AFTER_URL = ".,:;!?)"
_NAME = re.compile(r"[a-z0-9_]+")

# The yt-dlp errors a fixture can hold, by the name it records them under.
ERROR_TYPES: dict[str, type[ExtractorError]] = {
    "ExtractorError": ExtractorError,
    "GeoRestrictedError": GeoRestrictedError,
}


def scrub(value: Any) -> Any:
    """A copy of JSON data without secrets or personal details. Scrubbing twice changes nothing."""
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items() if _keeps_key(key)}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        text = _URL.sub(_scrub_found_url, value)
        return redact(_CLIENT_ID_IN_TEXT.sub(rf"\1{MASK}", text))
    return value


def _scrub_found_url(found: re.Match[str]) -> str:
    """scrub_url for a URL found in text, keeping any punctuation that follows it."""
    url = found[0].rstrip(_AFTER_URL)
    return scrub_url(url) + found[0][len(url) :]


def scrub_url(url: str) -> str:
    """The URL without the SIGNED_PARAMS in its query. Other URLs come back unchanged."""
    parts = urlsplit(url)
    params = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(key, item) for key, item in params if key.lower() not in SIGNED_PARAMS]
    if len(kept) == len(params):
        return url
    return urlunsplit(parts._replace(query=urlencode(kept)))


def _keeps_key(key: str) -> bool:
    return not _SECRET_KEY.search(key) and key not in PERSONAL_KEYS


def record(url: str, ydl: YoutubeDL) -> dict[str, Any]:
    """Resolve one track the way soundcloud.resolve does, and return the scrubbed fixture."""
    link = normalize_soundcloud(url)
    fixture: dict[str, Any] = {
        "url": link,
        "recorded": datetime.now(UTC).date().isoformat(),
        "yt_dlp": engine.engine_version(),
        "hand_built": None,
        "info": None,
        "info_error": None,
        "api": None,
        "api_error": None,
    }
    try:
        fixture["info"] = ydl.sanitize_info(ydl.extract_info(link, download=False))
    except DownloadError as error:
        fixture["info_error"] = describe_download_error(error)
    try:
        fixture["api"] = soundcloud.api_track(ydl, link)
    except ScmpError as error:
        fixture["api_error"] = {"kind": str(error.kind), "status": _http_status(error.__cause__)}
    return scrub(fixture)


def describe_download_error(error: DownloadError) -> dict[str, Any]:
    """What a fixture keeps of yt-dlp's error: the type it wraps, the message and any status.

    yt-dlp wraps the deepest cause it has: the HTTPError of a failed request, or else the
    ExtractorError itself.
    """
    inner = error.exc_info[1] if error.exc_info and error.exc_info[1] else error
    status = _http_status(inner) or _http_status(getattr(inner, "cause", None))
    return {"type": type(inner).__name__, "message": str(error), "status": status}


def _http_status(error: BaseException | None) -> int | None:
    return error.status if isinstance(error, HTTPError) else None


def save(name: str, fixture: dict[str, Any], folder: Path = FIXTURES_DIR) -> Path:
    """Scrub a fixture and write it as <name>.json, with LF line endings."""
    path = folder / f"{name}.json"
    # Scrubbed here too, so a hand-built fixture can't skip it.
    text = json.dumps(scrub(fixture), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def load(name: str, folder: Path = FIXTURES_DIR) -> dict[str, Any]:
    """Read one fixture by name."""
    return json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))


def names(folder: Path = FIXTURES_DIR) -> list[str]:
    """Every fixture's name, sorted."""
    return sorted(path.stem for path in folder.glob("*.json"))


class ReplayCache:
    """Stands in for yt-dlp's cache, which is where the client_id is kept."""

    def __init__(self) -> None:
        self.stored: dict[tuple[str, str], Any] = {("soundcloud", "client_id"): FAKE_CLIENT_ID}

    def load(self, section: str, key: str) -> Any:
        return self.stored.get((section, key))

    def store(self, section: str, key: str, value: Any) -> None:
        self.stored[(section, key)] = value


class ReplayYdl:
    """Stands in for YoutubeDL, answering from one fixture and keeping what it was asked."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture
        self.cache = ReplayCache()
        self.extracted: list[str] = []
        self.requests: list[Request] = []

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        self.extracted.append(url)
        if self.fixture["info_error"]:
            raise rebuild_download_error(self.fixture["info_error"])
        return copy.deepcopy(self.fixture["info"])

    def urlopen(self, request: Request) -> io.BytesIO:
        self.requests.append(request)
        api_error = self.fixture["api_error"]
        if api_error and api_error.get("status"):
            raise http_error(api_error["status"], request.url)
        if api_error:
            raise TransportError(msg="Could not resolve host")
        return io.BytesIO(json.dumps(self.fixture["api"]).encode())


def rebuild_download_error(recorded: dict[str, Any]) -> DownloadError:
    """The DownloadError yt-dlp raised when the fixture was recorded."""
    inner: Exception
    if recorded["type"] == "HTTPError":
        inner = http_error(recorded["status"])
    else:
        message = recorded["message"].removeprefix("ERROR: ")
        inner = ERROR_TYPES[recorded["type"]](message, expected=True)
    return DownloadError(recorded["message"], exc_info=(type(inner), inner, None))


def http_error(status: int, url: str = soundcloud.API_RESOLVE) -> HTTPError:
    """yt-dlp's HTTPError for a response with this status and an empty body."""
    return HTTPError(Response(io.BytesIO(b""), url, {}, status=status))


def main(argv: list[str]) -> int:
    """Record each NAME=URL pair into tests/fixtures/soundcloud/NAME.json."""
    pairs = [arg.partition("=") for arg in argv]
    if not pairs or any(not sep or not _NAME.fullmatch(name) for name, sep, _ in pairs):
        print(__doc__, file=sys.stderr)
        return 2
    with soundcloud.make_ydl() as ydl:
        for name, _, url in pairs:
            path = save(name, record(url, ydl))
            print(f"Saved {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
