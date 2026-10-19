"""Tests for the helpers that keep the token and personal data out of the spike's output."""

import base64
import json

from redact import (
    MASK,
    describe_token,
    error_details,
    mask_json,
    redact_text,
    redact_url,
)

FAKE_TOKEN = "2-123456-987654321-AbCdEfGhIjKlMn"


def base64url_json(data: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def make_jwt(claims: dict[str, object]) -> str:
    """Build an unsigned JWT-shaped string with the given claims."""
    return f"{base64url_json({'alg': 'none'})}.{base64url_json(claims)}.c2lnbmF0dXJl"


FAKE_JWT = make_jwt({"sub": "someone", "iat": 1_700_000_000, "exp": 1_700_003_600})


def test_describe_token_shows_only_length_and_first_four_characters() -> None:
    description = describe_token(FAKE_TOKEN)

    assert description == "length 33, starts with '2-12'"
    assert "AbCd" not in description


def test_redact_text_masks_a_known_secret_anywhere() -> None:
    assert redact_text("value=xyz-secret;", ["xyz-secret"]) == f"value={MASK};"


def test_redact_text_masks_token_shapes_without_being_told_the_token() -> None:
    assert FAKE_TOKEN not in redact_text(f"cookie oauth_token={FAKE_TOKEN} set")


def test_redact_text_masks_oauth_header_values() -> None:
    assert redact_text("Authorization: OAuth abc.def") == f"Authorization: OAuth {MASK}"


def test_redact_text_ignores_empty_secrets() -> None:
    assert redact_text("unchanged", [""]) == "unchanged"


def test_redact_text_masks_jwt_shaped_tokens() -> None:
    assert FAKE_JWT not in redact_text(f"cookie oauth_token={FAKE_JWT};")


def test_redact_text_masks_tokens_right_after_url_encoding_or_an_underscore() -> None:
    for prefix in ["x%22", "x%3D", "Bearer%20", "x_"]:
        assert FAKE_JWT not in redact_text(prefix + FAKE_JWT)
        assert FAKE_TOKEN not in redact_text(prefix + FAKE_TOKEN)


def test_redact_text_masks_email_addresses() -> None:
    assert redact_text("Signing in as some.one+x@mail.example.com") == f"Signing in as {MASK}"


def test_error_details_keeps_error_codes_and_messages() -> None:
    body = {"error": "invalid_credentials", "message": "Wrong password", "user": {"id": 5}}

    assert error_details(body) == {"error": "invalid_credentials", "message": "Wrong password"}


def test_error_details_finds_nested_errors_and_masks_emails() -> None:
    body = {"errors": [{"code": 403, "reason": "blocked for a@b.com"}]}

    assert error_details(body) == {
        "errors[0].code": "403",
        "errors[0].reason": f"blocked for {MASK}",
    }


def test_error_details_ignores_non_error_keys() -> None:
    assert error_details({"session": {"access_token": FAKE_TOKEN}}) == {}


def test_redact_url_keeps_query_keys_and_drops_values() -> None:
    url = "https://secure.soundcloud.com/web-auth?client_id=abc&state=xyz"

    assert redact_url(url) == "https://secure.soundcloud.com/web-auth?client_id&state"


def test_redact_url_keeps_public_soundcloud_routes() -> None:
    assert redact_url("https://soundcloud.com/signin") == "https://soundcloud.com/signin"


def test_redact_url_masks_usernames_on_soundcloud() -> None:
    assert redact_url("https://soundcloud.com/some-user/likes") == f"https://soundcloud.com/{MASK}"


def test_redact_url_masks_id_like_path_parts_on_other_hosts() -> None:
    url = "https://api-v2.soundcloud.com/users/12345678/tracks"

    assert redact_url(url) == f"https://api-v2.soundcloud.com/users/{MASK}/tracks"


def test_redact_url_masks_token_shaped_path_parts() -> None:
    assert FAKE_TOKEN not in redact_url(f"https://api-auth.soundcloud.com/x/{FAKE_TOKEN}")


def test_redact_url_shows_only_keys_of_a_key_value_fragment() -> None:
    url = f"https://soundcloud.com/signin#access_token={FAKE_TOKEN}&scope=x"

    assert redact_url(url) == "https://soundcloud.com/signin#access_token&scope"


def test_redact_url_masks_usernames_even_with_a_port_or_user_info() -> None:
    assert redact_url("https://soundcloud.com:443/some-user/likes") == (
        f"https://soundcloud.com:443/{MASK}"
    )
    assert redact_url("https://me:pw@soundcloud.com/signin") == "https://soundcloud.com/signin"


def test_redact_url_masks_a_bare_query_piece() -> None:
    assert redact_url("https://soundcloud.com/signin?someuser") == (
        f"https://soundcloud.com/signin?{MASK}"
    )


def test_redact_url_prints_about_blank_as_it_is_and_an_empty_url_as_empty() -> None:
    assert redact_url("about:blank") == "about:blank"
    assert redact_url("") == ""
    assert redact_url("data:text/plain,secret") == f"data:{MASK}"


def test_redact_url_masks_a_plain_fragment() -> None:
    assert (
        redact_url("https://soundcloud.com/signin#abc") == f"https://soundcloud.com/signin#{MASK}"
    )


def test_mask_json_replaces_leaves_with_type_names() -> None:
    masked = mask_json({"username": "someone", "followers_count": 12, "city": None, "pro": True})

    assert masked == {"username": "str", "followers_count": "int", "city": "null", "pro": "bool"}


def test_mask_json_keeps_only_the_listed_paths() -> None:
    body = {"consumer_subscription": {"product": {"id": "consumer-free", "name": "Free"}}}

    masked = mask_json(body, frozenset({"consumer_subscription.product.id"}))

    assert masked == {"consumer_subscription": {"product": {"id": "consumer-free", "name": "str"}}}


def test_mask_json_shows_one_element_of_a_list() -> None:
    masked = mask_json({"visuals": [{"url": "a"}, {"url": "b"}], "tags": []})

    assert masked == {"visuals": [{"url": "str"}], "tags": []}
