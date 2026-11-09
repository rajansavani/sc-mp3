"""The cookies in the sign-in window's profile: the two sign-in cookies, and deleting SoundCloud's.

After the sign-in is read, soundcloud.com's cookies are deleted from the kept profile and Google's
stay. Then the profile never holds the session sc-mp3 renews, which a renewal would end.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from scmp3.core.log import register_secret

ACCESS_COOKIE = "oauth_token"
REFRESH_COOKIE = "oauth_refresh_token"
# Both cookies are host-only on soundcloud.com. A domain cookie would apply to it too.
SIGN_IN_DOMAINS = frozenset({"soundcloud.com", ".soundcloud.com"})
SOUNDCLOUD_SITE = "soundcloud.com"
# Chromium deletes a cookie that's set again with an expiry in the past.
EXPIRED = 1  # seconds since 1970
# Attributes a cookie is matched by, which a deleting copy carries over as they are.
MATCHED_BY = (
    "path",
    "secure",
    "httpOnly",
    "sameSite",
    "partitionKey",
    "sourceScheme",
    "sourcePort",
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignInCookies:
    """The two cookie values a sign-in leaves, from the window or pasted. Kept out of repr."""

    access_token: str = field(repr=False)  # oauth_token
    refresh_token: str = field(repr=False)  # oauth_refresh_token


def cookie_list(answer: Any) -> list[dict[str, Any]]:
    """The cookies in a Storage.getCookies answer, skipping anything that isn't one."""
    cookies = answer if isinstance(answer, list) else []
    return [cookie for cookie in cookies if isinstance(cookie, dict)]


def pick_cookies(cookies: list[dict[str, Any]]) -> SignInCookies | None:
    """The two sign-in cookies for soundcloud.com, or None when one is missing.

    Every other cookie, Google's included, is dropped here without being looked at further.
    """
    values: dict[str, str] = {}
    for cookie in cookies:
        name, value = cookie.get("name"), cookie.get("value")
        if (
            name not in (ACCESS_COOKIE, REFRESH_COOKIE)
            or cookie.get("domain") not in SIGN_IN_DOMAINS
        ):
            continue
        if isinstance(value, str) and value:
            register_secret(value)
            values[name] = value
    if len(values) < 2:
        log.info("The browser profile has no sign-in: %s missing", _missing(values))
        return None
    return SignInCookies(values[ACCESS_COOKIE], values[REFRESH_COOKIE])


def _missing(values: dict[str, str]) -> str:
    return " and ".join(name for name in (ACCESS_COOKIE, REFRESH_COOKIE) if name not in values)


def is_soundcloud(cookie: dict[str, Any]) -> bool:
    """Whether a cookie belongs to soundcloud.com or one of its subdomains, such as secure."""
    domain = str(cookie.get("domain", "")).lstrip(".").lower()
    return domain == SOUNDCLOUD_SITE or domain.endswith(f".{SOUNDCLOUD_SITE}")


def soundcloud_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [cookie for cookie in cookies if is_soundcloud(cookie)]


def expired_copy(cookie: dict[str, Any]) -> dict[str, Any]:
    """A Storage.setCookies entry that deletes `cookie`, with no value of its own."""
    domain = str(cookie.get("domain", ""))
    copy: dict[str, Any] = {"name": cookie.get("name", ""), "value": "", "expires": EXPIRED}
    copy.update({key: cookie[key] for key in MATCHED_BY if key in cookie})
    if domain.startswith("."):
        copy["domain"] = domain
    else:
        # A host-only cookie is only matched by one set for its URL, with no domain.
        copy["url"] = f"https://{domain}{cookie.get('path', '/')}"
    return copy
