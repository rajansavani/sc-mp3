"""Tests for the /me summary and the yt-dlp logger, with made-up data and no network."""

from account_checks import RecordingLogger, dig, summarize_me
from token_box import clean_token

FAKE_TOKEN = "2-123456-987654321-AbCdEfGhIjKlMn"
FAKE_ME = {
    "username": "someone",
    "permalink": "someone",
    "avatar_url": "https://i1.sndcdn.com/avatars-x-large.jpg",
    "followers_count": 3,
    "consumer_subscription": {"product": {"id": "consumer-free", "name": "Free"}},
    "creator_subscription": {"product": {"id": "free"}},
}


def test_summarize_me_masks_personal_values() -> None:
    shape, _ = summarize_me(FAKE_ME)

    assert shape["username"] == "str"
    assert shape["followers_count"] == "int"
    assert shape["consumer_subscription"]["product"]["name"] == "str"


def test_summarize_me_keeps_the_subscription_tiers() -> None:
    shape, tiers = summarize_me(FAKE_ME)

    assert tiers == {
        "consumer_subscription.product.id": "consumer-free",
        "creator_subscription.product.id": "free",
    }
    assert shape["consumer_subscription"]["product"]["id"] == "consumer-free"


def test_dig_returns_none_where_the_path_stops() -> None:
    assert dig({"consumer_subscription": None}, "consumer_subscription.product.id") is None


def test_recording_logger_masks_the_token_in_messages() -> None:
    logger = RecordingLogger([FAKE_TOKEN])

    logger.warning(f"request failed with password {FAKE_TOKEN}")

    assert logger.lines == ["warning: request failed with password ***"]


def test_clean_token_strips_spaces_quotes_and_prefixes() -> None:
    for pasted in [
        f" {FAKE_TOKEN} ",
        f'"{FAKE_TOKEN}"',
        f"oauth_token={FAKE_TOKEN}",
        f"OAuth {FAKE_TOKEN}",
    ]:
        assert clean_token(pasted) == FAKE_TOKEN


def test_clean_token_leaves_an_empty_paste_empty() -> None:
    assert clean_token("   ") == ""
