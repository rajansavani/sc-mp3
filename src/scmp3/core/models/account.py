"""The signed-in SoundCloud account and its plan."""

import logging
from dataclasses import dataclass
from typing import Literal

from scmp3.core.models.base import Model

log = logging.getLogger(__name__)

AccountTier = Literal["free", "go", "go_plus", "dj"]

# SoundCloud's consumer_subscription.product.id for each plan. Free accounts send either id.
PRODUCT_TIERS: dict[str, AccountTier] = {
    "free": "free",
    "consumer-free": "free",
    "consumer-mid-tier": "go",
    "consumer-high-tier": "go_plus",
    "consumer-high-dj-tier": "dj",
}


@dataclass
class Account(Model):
    """The signed-in SoundCloud account."""

    username: str
    permalink: str
    avatar_url: str | None = None
    tier: AccountTier = "free"


def account_tier(product_id: str | None) -> AccountTier:
    """The plan for a SoundCloud product id. An id we don't know is logged and counts as free."""
    tier = PRODUCT_TIERS.get(product_id or "")
    if tier is None:
        log.warning("Unknown SoundCloud product id %r, so the account counts as free", product_id)
        return "free"
    return tier
