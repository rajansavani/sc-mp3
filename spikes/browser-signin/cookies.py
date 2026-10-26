"""Picks the two sign-in cookies out of the browser's cookies, and describes them without values."""

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from scmp3.core.log import register_secret

ACCESS_COOKIE = "oauth_token"
REFRESH_COOKIE = "oauth_refresh_token"
WANTED = (ACCESS_COOKIE, REFRESH_COOKIE)
# First path parts on soundcloud.com that aren't usernames, so a page trail may show them.
PUBLIC_ROUTES = frozenset({"", "signin", "signup", "discover", "feed", "stream", "you", "upload"})
PROFILE_HOSTS = frozenset({"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"})
MASK = "***"


@dataclass(frozen=True)
class CookieFacts:
    """What the report may say about a cookie: never its value."""

    name: str
    domain: str
    path: str
    http_only: bool
    secure: bool
    same_site: str
    expires: float | None  # seconds since the epoch, None for a session cookie
    length: int
    first_four: str


def sign_in_cookies(cookies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The two sign-in cookies set for soundcloud.com, by name, when they hold a value.

    Their values are registered as secrets at once, so no later output can show them.
    """
    found: dict[str, dict[str, Any]] = {}
    for cookie in cookies:
        name = cookie.get("name")
        if name in WANTED and is_soundcloud(cookie.get("domain", "")) and cookie.get("value"):
            register_secret(cookie["value"])
            found[name] = cookie
    return found


def token_values(found: dict[str, dict[str, Any]]) -> dict[str, str] | None:
    """Both values by cookie name, or None while one is missing."""
    if set(found) != set(WANTED):
        return None
    return {name: found[name]["value"] for name in WANTED}


def is_soundcloud(domain: str) -> bool:
    host = domain.lstrip(".")
    return host == "soundcloud.com" or host.endswith(".soundcloud.com")


def facts(cookie: dict[str, Any]) -> CookieFacts:
    value = cookie["value"]
    # DevTools gives -1 and session=True for a cookie that ends with the browser session.
    expires = None if cookie.get("session") else float(cookie.get("expires", -1))
    return CookieFacts(
        name=cookie["name"],
        domain=cookie.get("domain", ""),
        path=cookie.get("path", ""),
        http_only=bool(cookie.get("httpOnly")),
        secure=bool(cookie.get("secure")),
        same_site=cookie.get("sameSite", "not set"),
        expires=expires,
        length=len(value),
        first_four=value[:4],
    )


def describe(cookie: CookieFacts, now: float | None = None) -> str:
    now = time.time() if now is None else now
    if cookie.expires is None:
        expiry = "session cookie"
    else:
        when = datetime.fromtimestamp(cookie.expires, UTC).strftime("%Y-%m-%d %H:%M UTC")
        expiry = f"expires {when}, {(cookie.expires - now) / 86400:.1f} days from now"
    # Not "name: ...", since core.log.redact masks whatever follows a "token:".
    return (
        f"{cookie.name} cookie, domain {cookie.domain}, path {cookie.path}, "
        f"HttpOnly {cookie.http_only}, Secure {cookie.secure}, SameSite {cookie.same_site}, "
        f"{expiry}, "
        f"length {cookie.length}, starts with {cookie.first_four!r}"
    )


def page_place(url: str) -> str:
    """A page's scheme, host and path, with no query or fragment and no SoundCloud username."""
    parts = urlsplit(url)
    if not parts.netloc:
        return parts.scheme + ":" if parts.scheme else MASK
    path = parts.path
    host = parts.hostname or ""
    # Only the main site puts usernames in paths. secure.soundcloud.com has generic ones.
    if host in PROFILE_HOSTS:
        first = path.strip("/").split("/", 1)[0]
        if first not in PUBLIC_ROUTES:
            path = "/" + MASK
    return f"{parts.scheme}://{host}{path}"
