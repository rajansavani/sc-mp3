"""Tests for how the paste box accepts or refuses a pasted token, with no window."""

import base64
import json

from token_box import (
    clean_token,
    judge_submission,
    reject_access_token,
    reject_non_access_token,
)

REFUSAL = "That's the access token."


def base64url_json(data: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def reject_access(token: str) -> str | None:
    """A stand-in rejector that refuses anything starting with "access-"."""
    return REFUSAL if token.startswith("access-") else None


def test_a_good_paste_is_accepted_and_closes_the_box() -> None:
    outcome = judge_submission("  refresh-1 ", reject_access, rejections=0)

    assert outcome.token == "refresh-1"
    assert outcome.done
    assert outcome.message == ""


def test_a_refused_paste_keeps_the_box_open_for_one_more_try() -> None:
    outcome = judge_submission("access-1", reject_access, rejections=0)

    assert outcome.token is None
    assert outcome.message == REFUSAL
    assert not outcome.done


def test_a_second_refused_paste_closes_the_box_with_no_token() -> None:
    outcome = judge_submission("access-1", reject_access, rejections=1)

    assert outcome.token is None
    assert outcome.done


def test_an_empty_paste_closes_the_box_with_no_token() -> None:
    outcome = judge_submission("   ", reject_access, rejections=0)

    assert outcome.token is None
    assert outcome.done


def test_the_token_stays_out_of_repr() -> None:
    assert "refresh-1" not in repr(judge_submission("refresh-1", None, rejections=0))


def test_clean_token_strips_the_refresh_cookie_name() -> None:
    assert clean_token("oauth_refresh_token=refresh-1") == "refresh-1"


def test_rejectors_refuse_the_wrong_token_type() -> None:
    access = f"{base64url_json({'alg': 'RS256', 'typ': 'at+JWT'})}.{base64url_json({})}.sig"

    assert reject_access_token(access) is not None
    assert reject_access_token("not-a-jwt") is None
    assert reject_non_access_token(access) is None
    assert reject_non_access_token("not-a-jwt") is not None
