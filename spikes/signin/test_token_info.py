"""Tests for reading a JWT's algorithm, claim names and times, with made-up tokens."""

import base64
import json

from token_info import is_access_token, lifetime_lines, read_jwt, readable

IAT = 1_700_000_000


def base64url_json(data: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def make_jwt(claims: dict[str, object]) -> str:
    """Build an unsigned JWT-shaped string with the given claims."""
    header = {"alg": "RS256", "typ": "JWT", "kid": "key-1"}
    return f"{base64url_json(header)}.{base64url_json(claims)}.c2lnbmF0dXJl"


def test_read_jwt_gives_algorithm_and_claim_names_but_no_personal_values() -> None:
    facts = read_jwt(make_jwt({"sub": "someone", "iat": IAT, "exp": IAT + 3600}))

    assert facts is not None
    assert (facts.alg, facts.typ) == ("RS256", "JWT")
    assert facts.header_keys == ["alg", "kid", "typ"]
    assert facts.claim_names == ["exp", "iat", "sub"]
    assert "someone" not in repr(facts)


def test_read_jwt_returns_none_for_older_token_shapes() -> None:
    assert read_jwt("2-123456-987654321-AbCdEfGhIjKlMn") is None


def test_read_jwt_returns_none_when_the_payload_is_not_json() -> None:
    assert read_jwt("eyJhbGciOiJub25lIn0.bm90IGpzb24.") is None


def test_lifetime_lines_give_full_lifetime_time_left_and_age() -> None:
    facts = read_jwt(make_jwt({"iat": IAT, "exp": IAT + 3600}))
    assert facts is not None

    lines = lifetime_lines(facts, now=IAT + 900)

    assert lines == [
        "exp - iat (full lifetime): 3600 s, 1.00 hours",
        "exp - now (time left): 2700 s, 0.75 hours",
        "now - iat (age): 900 s, 0.25 hours",
    ]


def test_lifetime_lines_say_when_times_are_missing() -> None:
    facts = read_jwt(make_jwt({"sub": "someone"}))
    assert facts is not None

    assert lifetime_lines(facts, now=IAT) == [
        "the token has no iat or exp claim, so its lifetime can't be read"
    ]


def test_is_access_token_recognizes_the_at_jwt_type() -> None:
    access = f"{base64url_json({'alg': 'RS256', 'typ': 'at+JWT'})}.{base64url_json({})}.c2ln"
    refresh = f"{base64url_json({'alg': 'RS256', 'typ': 'JWT'})}.{base64url_json({})}.c2ln"

    assert is_access_token(access)
    assert not is_access_token(refresh)
    assert not is_access_token("not-a-jwt")


def test_readable_switches_to_days_for_long_spans() -> None:
    assert readable(365 * 86400) == "365.0 days"
    assert readable(-7200) == "-2.00 hours"
