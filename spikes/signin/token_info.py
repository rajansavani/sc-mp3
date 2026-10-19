"""Reads the parts of a JWT token that are safe to print: algorithm, claim names and times."""

import base64
import json
from dataclasses import dataclass
from typing import Any

SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400


@dataclass
class JwtFacts:
    """What a JWT says about itself, without any personal claim values."""

    alg: str
    typ: str
    header_keys: list[str]
    claim_names: list[str]
    issued_at: int | None = None
    expires_at: int | None = None


def read_jwt(token: str) -> JwtFacts | None:
    """Decode a JWT's header and payload locally. Returns None when it isn't a JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = decode_part(parts[0])
        claims = decode_part(parts[1])
    except ValueError:
        return None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        return None
    return JwtFacts(
        alg=str(header.get("alg")),
        typ=str(header.get("typ")),
        header_keys=sorted(header),
        claim_names=sorted(claims),
        issued_at=as_int(claims.get("iat")),
        expires_at=as_int(claims.get("exp")),
    )


def is_access_token(token: str) -> bool:
    """True for an OAuth access token in JWT form, whose header says typ "at+JWT" (RFC 9068)."""
    facts = read_jwt(token)
    return facts is not None and facts.typ.lower() == "at+jwt"


def access_token_client_id(token: str) -> str | None:
    """Return the client_id claim of a JWT access token. Used to form a request, never printed."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        claims = decode_part(parts[1])
    except ValueError:
        return None
    if not isinstance(claims, dict):
        return None
    client_id = claims.get("client_id")
    return client_id if isinstance(client_id, str) and client_id else None


def decode_part(part: str) -> Any:
    padded = part + "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def lifetime_lines(facts: JwtFacts, now: int) -> list[str]:
    """Describe exp - iat, exp - now and now - iat, in seconds and in hours or days."""
    if facts.issued_at is None or facts.expires_at is None:
        return ["the token has no iat or exp claim, so its lifetime can't be read"]
    spans = {
        "exp - iat (full lifetime)": facts.expires_at - facts.issued_at,
        "exp - now (time left)": facts.expires_at - now,
        "now - iat (age)": now - facts.issued_at,
    }
    return [f"{label}: {seconds} s, {readable(seconds)}" for label, seconds in spans.items()]


def readable(seconds: int) -> str:
    """Turn a number of seconds into hours or days, whichever reads better."""
    if abs(seconds) >= 2 * SECONDS_PER_DAY:
        return f"{seconds / SECONDS_PER_DAY:.1f} days"
    return f"{seconds / SECONDS_PER_HOUR:.2f} hours"
