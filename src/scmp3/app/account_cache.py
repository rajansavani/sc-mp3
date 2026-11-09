"""Remembers the signed-in account and when SoundCloud last confirmed it, in <config>/account.json.

So the top bar names the account at startup without asking SoundCloud more than once a day. It
holds no token: those stay in the token store.
"""

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

from scmp3.core import paths
from scmp3.core.errors import ScmpError
from scmp3.core.models import Account, AccountTier

FILE_NAME = "account.json"
TIERS = frozenset(get_args(AccountTier))

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedAccount:
    """The account as /me last described it, and when, in seconds since the epoch."""

    account: Account
    checked_at: float


class AccountCache:
    """account.json in the config folder. A file that can't be read counts as no file."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.config_dir() / FILE_NAME

    def load(self) -> CachedAccount | None:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            log.warning("Can't read %s: %s", FILE_NAME, error.strerror or type(error).__name__)
            return None
        cached = _from_json(text)
        if cached is None:
            log.warning("%s doesn't hold an account, so it's ignored", FILE_NAME)
        return cached

    def save(self, account: Account, checked_at: float) -> None:
        """Remember the account. A failure is only logged, since /me is asked again next time."""
        text = json.dumps({"account": account.to_dict(), "checked_at": checked_at})
        try:
            paths.write_text_atomically(self._path, text)
        except ScmpError as error:
            log.warning("The account wasn't remembered for the next start: %s", error)

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError as error:
            # Harmless: with no sign-in stored, the file is never read.
            log.warning("Can't delete %s: %s", FILE_NAME, error.strerror or type(error).__name__)


def _from_json(text: str) -> CachedAccount | None:
    try:
        stored = json.loads(text)
    except ValueError:
        return None
    if not isinstance(stored, dict) or not isinstance(stored.get("account"), dict):
        return None
    checked_at = stored.get("checked_at")
    if isinstance(checked_at, bool) or not isinstance(checked_at, int | float):
        return None
    try:
        account = Account.from_dict(stored["account"])
    except (TypeError, ValueError):
        return None
    names = (account.username, account.permalink)
    if not all(isinstance(name, str) and name for name in names) or account.tier not in TIERS:
        return None
    if account.avatar_url is not None and not isinstance(account.avatar_url, str):
        return None
    try:
        checked = float(checked_at)
    except OverflowError:
        # An integer too big for a float is no time at all, as a damaged file would hold.
        return None
    return CachedAccount(account, checked if math.isfinite(checked) else 0.0)
