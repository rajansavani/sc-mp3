"""Helpers that keep the token and personal data out of everything the spike prints."""

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote_plus, urlsplit

MASK = "***"
# No leading \b on the token shapes: URL encoding (%22, %3D) or an underscore can sit right
# before a token, and masking a little too much is harmless in a redactor.
# Older SoundCloud tokens look like 2-123456-123456789-AbCdEf12.
TOKEN_SHAPE = re.compile(r"2-\d+-\d+-[A-Za-z0-9]+")
# The oauth_token cookie seen in 2026-10 is a JWT: three base64url parts, starting with "eyJ".
JWT_SHAPE = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*")
# The refresh token seen is 32 characters, the same shape as a client_id, so no pattern can
# catch it. It's masked by value only, through the `secrets` list.
OAUTH_HEADER = re.compile(r"OAuth\s+\S+")
EMAIL_SHAPE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# Keys in an error response whose values say what went wrong, not who the user is.
ERROR_KEYS = frozenset(
    {"error", "error_code", "error_description", "error_message", "code", "reason", "message"}
)
MAX_ERROR_VALUE_LENGTH = 200
# A path part with three or more digits in a row is probably an account or track id.
ID_LIKE = re.compile(r"\d{3,}")
SOUNDCLOUD_HOSTS = frozenset({"soundcloud.com", "m.soundcloud.com", "www.soundcloud.com"})
# First path parts on soundcloud.com that aren't usernames, so they're safe to print.
PUBLIC_ROUTES = frozenset(
    {"", "signin", "signup", "discover", "feed", "stream", "you", "upload", "logout", "settings"}
)


def describe_token(token: str) -> str:
    """Return the only facts about a token the spike may print: its length and first 4 chars."""
    return f"length {len(token)}, starts with {token[:4]!r}"


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Mask every secret, token-shaped string, email address and OAuth header value in `text`."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, MASK)
    text = OAUTH_HEADER.sub(f"OAuth {MASK}", text)
    text = EMAIL_SHAPE.sub(MASK, text)
    text = JWT_SHAPE.sub(MASK, text)
    return TOKEN_SHAPE.sub(MASK, text)


def error_details(value: Any, path: str = "") -> dict[str, Any]:
    """Collect the error codes and messages in a JSON error body, redacted, keyed by path."""
    found: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child = _join(path, key)
            if key in ERROR_KEYS and isinstance(item, str | int | bool):
                found[child] = redact_text(str(item))[:MAX_ERROR_VALUE_LENGTH]
            else:
                found |= error_details(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found |= error_details(item, f"{path}[{index}]")
    return found


def redact_url(url: str) -> str:
    """Keep the scheme, host, generic path parts and query keys of `url`, and drop all values."""
    parts = urlsplit(url)
    if not parts.netloc:
        return _redact_opaque_url(url, parts.scheme)
    host = parts.hostname or ""
    # Rebuild the host without any user:password@ part; keep a non-default port.
    netloc = f"{host}:{parts.port}" if parts.port else host
    redacted = f"{parts.scheme}://{netloc}{_redact_path(host, parts.path)}"
    if parts.query:
        redacted += "?" + "&".join(_keys(parts.query))
    if parts.fragment:
        # Fragments sometimes carry tokens as key=value pairs, so show the keys only.
        keys = _keys(parts.fragment) if "=" in parts.fragment else [MASK]
        redacted += "#" + "&".join(keys)
    return redacted


def _redact_opaque_url(url: str, scheme: str) -> str:
    """Handle URLs with no host, such as about:blank, which say nothing about the user."""
    if not url:
        return ""
    if scheme == "about":
        return url
    return f"{scheme}:{MASK}" if scheme else MASK


def mask_json(value: Any, keep: frozenset[str] = frozenset(), path: str = "") -> Any:
    """Replace each leaf value with its type name, except at the dotted paths listed in `keep`."""
    if isinstance(value, dict):
        return {key: mask_json(item, keep, _join(path, key)) for key, item in value.items()}
    if isinstance(value, list):
        # One element shows the shape; the length could say something about the account.
        return [mask_json(value[0], keep, path + "[]")] if value else []
    if path in keep:
        return value
    if value is None:
        return "null"
    return type(value).__name__


def _redact_path(host: str, path: str) -> str:
    segments = path.split("/")
    # On soundcloud.com the first part of most paths is a username.
    if host in SOUNDCLOUD_HOSTS and len(segments) > 1 and segments[1] not in PUBLIC_ROUTES:
        return f"/{MASK}"
    return "/".join(MASK if ID_LIKE.search(segment) else segment for segment in segments)


def _keys(query: str) -> list[str]:
    """Keep the names of key=value pairs. A bare piece with no "=" could be a value, so mask it."""
    keys = []
    for piece in query.split("&"):
        if not piece:
            continue
        keys.append(unquote_plus(piece.split("=", 1)[0]) if "=" in piece else MASK)
    return keys


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key
