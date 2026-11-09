"""Tests for account.json, which names the signed-in account at startup."""

import json

import pytest

from scmp3.app.account_cache import FILE_NAME, AccountCache
from scmp3.core import paths
from scmp3.core.models import Account

KAIA = Account(username="Kaia", permalink="kaia-music", avatar_url=None, tier="go")


def test_a_saved_account_comes_back_with_when_it_was_checked() -> None:
    cache = AccountCache()
    cache.save(KAIA, 1_800_000_000.0)
    cached = cache.load()
    assert cached is not None
    assert cached.account == KAIA
    assert cached.checked_at == 1_800_000_000.0


def test_no_file_means_no_account() -> None:
    assert AccountCache().load() is None


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({"account": {"username": "Kaia"}, "checked_at": 1}),
        json.dumps({"account": {**KAIA.to_dict(), "tier": "platinum"}, "checked_at": 1}),
        json.dumps({"account": {**KAIA.to_dict(), "username": ""}, "checked_at": 1}),
        json.dumps({"account": KAIA.to_dict(), "checked_at": True}),
        json.dumps({"account": KAIA.to_dict()}),
        # An integer too big for a float, as a damaged file might hold.
        json.dumps({"account": KAIA.to_dict(), "checked_at": 10**400}),
    ],
)
def test_a_file_that_doesnt_hold_an_account_counts_as_none(text: str) -> None:
    (paths.config_dir() / FILE_NAME).parent.mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / FILE_NAME).write_text(text, encoding="utf-8")
    assert AccountCache().load() is None


def test_clearing_deletes_the_file_and_doesnt_mind_if_it_was_never_there() -> None:
    cache = AccountCache()
    cache.clear()
    cache.save(KAIA, 1.0)
    cache.clear()
    assert not (paths.config_dir() / FILE_NAME).exists()


def test_the_file_holds_no_token() -> None:
    AccountCache().save(KAIA, 1.0)
    stored = json.loads((paths.config_dir() / FILE_NAME).read_text(encoding="utf-8"))
    assert set(stored) == {"account", "checked_at"}
    assert set(stored["account"]) == {"username", "permalink", "avatar_url", "tier"}
