"""Checks that need the token: GET /me through yt-dlp's opener, and yt-dlp's own sign-in."""

import json
from dataclasses import dataclass, field
from typing import Any

from redact import mask_json, redact_text
from yt_dlp import YoutubeDL
from yt_dlp.extractor.soundcloud import SoundcloudBaseIE
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError, RequestError
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadError

ME_URL = "https://api-v2.soundcloud.com/me"
# A well-formed token that SoundCloud has never issued, for the "bad token" cases.
FAKE_TOKEN = "2-000000-000000000-AAAAAAAAAAAAAA"
# Two of yt-dlp's own test tracks: one whose original file needs an account, and one with
# an AAC HQ format for subscribers.
PROBE_TRACK_URLS = (
    "https://soundcloud.com/the80m/the-following",
    "https://soundcloud.com/wandw/the-chainsmokers-ft-daya-dont-let-me-down-ww-remix-1",
)
# The only /me values the spike prints: the subscription tiers.
TIER_PATHS = frozenset({"consumer_subscription.product.id", "creator_subscription.product.id"})
GUEST_WARNING = "Continuing as guest"


class RecordingLogger:
    """A yt-dlp logger that keeps redacted messages in memory instead of printing them."""

    def __init__(self, secrets: list[str]) -> None:
        self.secrets = secrets
        self.lines: list[str] = []

    def debug(self, message: str) -> None:
        self.record("debug", message)

    def info(self, message: str) -> None:
        self.record("info", message)

    def warning(self, message: str) -> None:
        self.record("warning", message)

    def error(self, message: str) -> None:
        self.record("error", message)

    def record(self, level: str, message: str) -> None:
        self.lines.append(f"{level}: {redact_text(message, self.secrets)}")


@dataclass
class MeResponse:
    """One GET /me attempt, with every personal value masked."""

    label: str
    status: int | None = None
    error: str = ""
    shape: Any = None
    tiers: dict[str, Any] = field(default_factory=dict)


@dataclass
class SigninCheck:
    """What yt-dlp said and did when given a token as username "oauth"."""

    label: str
    holds_auth_header: bool = False
    guest_warning: bool = False
    messages: list[str] = field(default_factory=list)
    format_ids: dict[str, list[str]] = field(default_factory=dict)


def fetch_me(
    label: str, token: str | None, *, client_id: str = "", impersonate: bool = False
) -> MeResponse:
    """Send GET /me through yt-dlp's opener, with `Authorization: OAuth <token>` when given."""
    secrets = [token] if token else []
    headers = {"Authorization": f"OAuth {token}"} if token else {}
    url = f"{ME_URL}?client_id={client_id}" if client_id else ME_URL
    extensions = {"impersonate": ImpersonateTarget("chrome")} if impersonate else {}
    result = MeResponse(label)
    with YoutubeDL({"logger": RecordingLogger(secrets), "cachedir": False}) as ydl:
        try:
            response = ydl.urlopen(Request(url, headers=headers, extensions=extensions))
        except HTTPError as error:
            result.status = error.status
            result.error = redact_text(str(error), secrets)
            return result
        except RequestError as error:
            result.error = redact_text(f"{type(error).__name__}: {error}", secrets)
            return result
        result.status = response.status
        body = json.loads(response.read())
    result.shape, result.tiers = summarize_me(body)
    return result


def summarize_me(body: Any) -> tuple[Any, dict[str, Any]]:
    """Return the /me body with every value masked, and the subscription tiers it names."""
    return mask_json(body, TIER_PATHS), {path: dig(body, path) for path in sorted(TIER_PATHS)}


def scrape_client_id() -> str:
    """Get SoundCloud's web client_id the way yt-dlp does, for the retry with a client_id."""
    with YoutubeDL({"logger": RecordingLogger([]), "cachedir": False}) as ydl:
        extractor = ydl.get_info_extractor("Soundcloud")
        # A private yt-dlp method, but it's the scraping code yt-dlp itself relies on.
        extractor._initialize_pre_login()
        return extractor._CLIENT_ID


def check_ytdlp_signin(label: str, password: str | None, *, extract: bool) -> SigninCheck:
    """Sign yt-dlp in with username "oauth" and `password`, then optionally extract a track."""
    secrets = [password] if password else []
    logger = RecordingLogger(secrets)
    params: dict[str, Any] = {"logger": logger, "cachedir": False}
    if password is not None:
        params |= {"username": "oauth", "password": password}
    result = SigninCheck(label)
    with YoutubeDL(params) as ydl:
        ydl.get_info_extractor("Soundcloud").initialize()
        # yt-dlp keeps the header on the class, so it's shared by every YoutubeDL in the process.
        result.holds_auth_header = "Authorization" in SoundcloudBaseIE._HEADERS
        if extract:
            result.format_ids = extract_format_ids(ydl, logger)
    result.messages = logger.lines
    result.guest_warning = any(GUEST_WARNING in line for line in logger.lines)
    return result


def extract_format_ids(ydl: YoutubeDL, logger: RecordingLogger) -> dict[str, list[str]]:
    """List the format ids yt-dlp offers for each probe track."""
    found: dict[str, list[str]] = {}
    for url in PROBE_TRACK_URLS:
        try:
            info = ydl.extract_info(url, download=False)
        except DownloadError as error:
            logger.record("extract failed", str(error))
            found[url] = []
            continue
        found[url] = [str(fmt.get("format_id")) for fmt in info.get("formats") or []]
    return found


def forget_ytdlp_signin() -> None:
    """Drop the token yt-dlp keeps on its SoundCloud extractor class."""
    SoundcloudBaseIE._HEADERS.pop("Authorization", None)


def dig(body: Any, dotted_path: str) -> Any:
    """Follow a dotted path through nested dicts, returning None where it stops."""
    value = body
    for key in dotted_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value
