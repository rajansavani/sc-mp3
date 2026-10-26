"""Checks the window's two tokens through core.auth: /me, one renewal, and /me with the new token.

Everything runs in a temporary SCMP3_HOME with an in-memory keyring, so nothing reaches the
system's Credential Manager or the person's sc-mp3 settings.
"""

import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scmp3.core import auth, engine, paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.token_store import TokenStore
from scmp3.core.tokens import read_claims


class MemoryKeyring:
    """A keyring backend that keeps its entries in a dict, for this process only."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.entries.pop((service, username), None)


@dataclass
class AccountResult:
    username: str | None = None
    tier: str | None = None
    renewed: bool = False
    refresh_changed: bool | None = None
    new_token_hours: float | None = None
    me_with_new_token: bool = False
    stored_in: str | None = None  # where the temporary store put the pair: keyring or file
    error: str | None = None


def check_account(
    access: str, refresh: str, scratch: Path, say: Callable[[str], None]
) -> AccountResult:
    """Run the checks, then delete the temporary home, even after an error."""
    home = Path(tempfile.mkdtemp(prefix="home-", dir=scratch))
    old_home = os.environ.get(paths.HOME_ENV)
    os.environ[paths.HOME_ENV] = str(home)
    result = AccountResult()
    try:
        _run_checks(access, refresh, result, say)
    except ScmpError as error:
        result.error = str(error)
    finally:
        _restore_home(old_home)
        shutil.rmtree(home, ignore_errors=True)
        say(f"temporary sc-mp3 home deleted: {not home.exists()}")
    return result


def _run_checks(
    access: str, refresh: str, result: AccountResult, say: Callable[[str], None]
) -> None:
    store = TokenStore(backend=MemoryKeyring())
    ydl = engine.make_ydl()
    say("signing in through core.auth.sign_in (checks /me, stores in the in-memory keyring)")
    account = auth.sign_in(access, refresh, ydl, store=store)
    result.username = account.username
    result.tier = account.tier
    result.stored_in = store.location
    stored = store.load()
    if stored is None:
        raise ScmpError(ErrorKind.auth_expired, "the temporary store lost the sign-in")
    say("renewing once through core.auth.renew")
    new = auth.renew(stored, ydl, store=store)
    result.renewed = True
    result.refresh_changed = new.refresh != stored.refresh
    result.new_token_hours = (read_claims(new.access).exp - time.time()) / 3600
    say("checking /me with the renewed access token through core.auth.validate")
    auth.validate(new.access, ydl)
    result.me_with_new_token = True
    auth.sign_out(store=store)


def _restore_home(old_home: str | None) -> None:
    if old_home is None:
        os.environ.pop(paths.HOME_ENV, None)
        return
    os.environ[paths.HOME_ENV] = old_home
