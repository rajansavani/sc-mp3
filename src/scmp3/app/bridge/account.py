"""Bridge calls for signing in to SoundCloud and out again.

Each call returns at once. What happens next arrives in `account` events, from AccountState.
"""

import logging
from typing import Any

from scmp3.app.context import AppContext
from scmp3.core.errors import ErrorKind

log = logging.getLogger(__name__)


class AccountApi:
    """The SoundCloud account calls."""

    _context: AppContext

    def _snapshot_part(self) -> dict[str, Any]:
        """Who is signed in, read from the token store the first time."""
        account = self._context.account
        account.load_once()
        return {"account": account.view()}

    def sign_in_window(self) -> dict[str, Any]:
        """Open SoundCloud's sign-in page in a separate browser window, and sign in from it."""
        self._context.account.sign_in_window()
        return {}

    def finish_sign_in(self) -> dict[str, Any]:
        """Close the sign-in window normally, then read the sign-in from it."""
        self._context.account.finish_sign_in()
        return {}

    def cancel_sign_in(self) -> dict[str, Any]:
        """Close the sign-in window without signing in."""
        self._context.account.cancel_sign_in()
        return {}

    def sign_in_from_browser(self, access_token: str, refresh_token: str) -> dict[str, Any]:
        """Sign in with the oauth_token and oauth_refresh_token values copied from a browser."""
        if not (isinstance(access_token, str) and isinstance(refresh_token, str)):
            # The values themselves stay out of the log, whatever they are.
            log.warning("sign_in_from_browser needs two strings")
            return {"error": ErrorKind.internal.value}
        self._context.account.sign_in_from_browser(access_token, refresh_token)
        return {}

    def sign_out(self) -> dict[str, Any]:
        """Delete the stored sign-in from the keychain, or the file it's in."""
        self._context.account.sign_out()
        return {}
