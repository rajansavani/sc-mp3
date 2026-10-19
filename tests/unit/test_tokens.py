"""Tests for the token rules: the access token's claims and cleaning up pasted values."""

import base64
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.tokens import (
    Claims,
    TokenProblem,
    TokenRefused,
    Tokens,
    normalize_access,
    normalize_refresh,
    read_claims,
)


def load_signin_fakes() -> ModuleType:
    """tests/fixtures/signin_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_signin_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "signin_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


fakes = load_signin_fakes()
NOW = fakes.NOW
HOUR = fakes.HOUR
jwt_with = fakes.jwt_with
make_access = fakes.make_access
make_refresh = fakes.make_refresh
claim_client_id = fakes.claim_client_id
make_tokens = fakes.make_tokens


# Reading claims


def test_read_claims_returns_exp_and_client_id() -> None:
    client_id = claim_client_id()
    claims = read_claims(make_access(NOW + 5 * HOUR, client_id))
    assert claims == Claims(exp=NOW + 5 * HOUR, client_id=client_id)


@pytest.mark.parametrize(
    "value",
    [
        make_refresh(),
        "2-294712-12345678-AbCdEf12",
        "eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjF9",
        "eyJhbGciOiJSUzI1NiJ9.!!!!.c2ln",
        f"eyJhbGciOiJSUzI1NiJ9.{base64.urlsafe_b64encode(b'not json').decode()}.c2ln",
        jwt_with([1, 2, 3]),
    ],
    ids=["refresh token", "older 2- shape", "two parts", "bad base64", "not JSON", "a list"],
)
def test_a_value_that_isnt_a_jwt_has_no_claims(value: str) -> None:
    with pytest.raises(ScmpError) as error_info:
        read_claims(value)
    assert error_info.value.kind is ErrorKind.auth_expired
    assert value not in str(error_info.value)
    assert error_info.value.detail in (
        "the access token isn't a JWT",
        "the access token's claims can't be read",
    )


@pytest.mark.parametrize(
    ("claims", "detail"),
    [
        ({"client_id": "abc"}, "the access token has no exp claim"),
        ({"client_id": "abc", "exp": "tomorrow"}, "the access token has no exp claim"),
        ({"client_id": "abc", "exp": True}, "the access token has no exp claim"),
        ({"exp": NOW}, "the access token has no client_id claim"),
        ({"exp": NOW, "client_id": ""}, "the access token has no client_id claim"),
        ({"exp": NOW, "client_id": 7}, "the access token has no client_id claim"),
    ],
)
def test_a_jwt_without_exp_or_client_id_is_refused(claims: dict[str, Any], detail: str) -> None:
    with pytest.raises(ScmpError) as error_info:
        read_claims(jwt_with(claims))
    assert error_info.value.kind is ErrorKind.auth_expired
    assert error_info.value.detail == detail


def test_a_fractional_exp_is_read() -> None:
    claims = read_claims(jwt_with({"exp": NOW + 0.5, "client_id": "abc"}))
    assert claims.exp == NOW + 0.5


# Cleaning up pasted values


WRAPPINGS = [
    "{}",
    "  {}  ",
    "\n{}\t",
    '"{}"',
    "'{}'",
    "OAuth {}",
    "oauth {}",
    "Bearer {}",
    '"OAuth {}"',
    "oauth_token={}",
    'oauth_token="{}"',
    '"oauth_token={}"',
    "oauth_refresh_token={}",
    " oauth_token = {} ",
]


@pytest.mark.parametrize("wrapping", WRAPPINGS)
def test_normalize_access_strips_what_wraps_the_token(wrapping: str) -> None:
    token = make_access()
    assert normalize_access(wrapping.format(token)) == token


@pytest.mark.parametrize("wrapping", WRAPPINGS)
def test_normalize_refresh_strips_what_wraps_the_token(wrapping: str) -> None:
    token = make_refresh()
    assert normalize_refresh(wrapping.format(token)) == token


@pytest.mark.parametrize("wrapping", ["{}", "oauth_refresh_token={}", '"{}"'])
def test_the_access_field_refuses_a_refresh_token(wrapping: str) -> None:
    with pytest.raises(TokenRefused) as error_info:
        normalize_access(wrapping.format(make_refresh()))
    assert error_info.value.problem is TokenProblem.access_is_refresh


@pytest.mark.parametrize("wrapping", ["{}", "oauth_token={}", "OAuth {}"])
def test_the_refresh_field_refuses_an_access_token(wrapping: str) -> None:
    with pytest.raises(TokenRefused) as error_info:
        normalize_refresh(wrapping.format(make_access()))
    assert error_info.value.problem is TokenProblem.refresh_is_access


@pytest.mark.parametrize(
    "value",
    [
        "2-294712-12345678-AbCdEf12",
        make_access()[:60],
        "eyJhbGciOiJSUzI1NiJ9",
        "my token",
        "a.b.c.d",
    ],
    ids=["older 2- shape", "cut-off JWT", "one part", "words", "four parts"],
)
def test_the_access_field_refuses_a_value_that_isnt_a_jwt(value: str) -> None:
    with pytest.raises(TokenRefused) as error_info:
        normalize_access(value)
    assert error_info.value.problem is TokenProblem.access_not_token


@pytest.mark.parametrize(
    "value",
    [
        "a" * 15,
        "a" * 4097,
        "abcdefgh ijklmnopq",
        'abcdefgh"ijklmnopq',
        "abcdefgh,ijklmnopq",
        "abcdefgh;ijklmnopq",
        "abcdefgh\\ijklmnopq",
        "abcdefgh\x07ijklmnopq",
        "abcdefghéijklmnopq",
    ],
    ids=[
        "15 long",
        "4097 long",
        "space",
        "quote",
        "comma",
        "semicolon",
        "backslash",
        "control",
        "é",
    ],
)
def test_the_refresh_field_refuses_what_cant_be_a_cookie_value(value: str) -> None:
    with pytest.raises(TokenRefused) as error_info:
        normalize_refresh(value)
    assert error_info.value.problem is TokenProblem.refresh_not_token


@pytest.mark.parametrize(
    "value",
    ["a" * 16, "a" * 4096, "Ab0-_.~!#$%&'()*+/:<=>?@[]^`{|}x"],
    ids=["16 long", "4096 long", "every punctuation mark a cookie allows"],
)
def test_the_refresh_field_takes_any_cookie_value_of_a_sensible_length(value: str) -> None:
    assert normalize_refresh(value) == value


@pytest.mark.parametrize("value", ["", "   ", '""', "oauth_token=", "OAuth  "])
def test_an_empty_field_is_refused(value: str) -> None:
    with pytest.raises(TokenRefused) as access_error:
        normalize_access(value)
    with pytest.raises(TokenRefused) as refresh_error:
        normalize_refresh(value)
    assert access_error.value.problem is TokenProblem.access_empty
    assert refresh_error.value.problem is TokenProblem.refresh_empty


def test_a_refusal_never_holds_the_value() -> None:
    access, refresh = make_access(), make_refresh()
    with pytest.raises(TokenRefused) as access_error:
        normalize_access(refresh)
    with pytest.raises(TokenRefused) as refresh_error:
        normalize_refresh(access)
    for error, token in ((access_error.value, refresh), (refresh_error.value, access)):
        assert token not in str(error)
        assert token not in repr(error)


def test_tokens_keep_their_values_out_of_repr() -> None:
    tokens = make_tokens()
    for value in (tokens.access, tokens.refresh, tokens.client_id):
        assert value not in repr(tokens)


def test_claims_keep_the_client_id_out_of_repr() -> None:
    client_id = claim_client_id()
    assert client_id not in repr(read_claims(make_access(client_id=client_id)))


# Keeping tokens secret


@pytest.mark.parametrize(
    ("read", "secret_values"),
    [
        (lambda tokens: normalize_access(tokens.access), lambda tokens: {tokens.access}),
        (lambda tokens: normalize_refresh(tokens.refresh), lambda tokens: {tokens.refresh}),
        (
            lambda tokens: read_claims(tokens.access),
            lambda tokens: {tokens.access, tokens.client_id},
        ),
    ],
    ids=["normalize_access", "normalize_refresh", "read_claims"],
)
def test_a_token_is_registered_as_a_secret_as_soon_as_it_is_read(
    read: Callable[[Tokens], object],
    secret_values: Callable[[Tokens], set[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = make_tokens()
    registered: list[str] = []
    monkeypatch.setattr("scmp3.core.tokens.register_secret", registered.append)
    read(tokens)
    assert set(registered) == secret_values(tokens)
