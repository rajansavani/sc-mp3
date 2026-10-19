"""Bridge calls for signing in to SoundCloud and out again."""

from typing import Any


class AccountApi:
    """The SoundCloud account calls."""

    def _snapshot_part(self) -> dict[str, Any]:
        """The signed-in account, or None."""
        # TODO(T-041): Return the account stored in the keychain.
        return {"account": None}

    # TODO(T-041): Implement these with core/auth.py and the sign-in window.
    def sign_in_window(self) -> dict[str, Any]:
        """Open SoundCloud's login page in an app window."""
        return {"error": "not_implemented"}

    def sign_in_firefox(self) -> dict[str, Any]:
        """Take the sign-in from Firefox, if it's logged in to SoundCloud."""
        return {"error": "not_implemented"}

    def sign_in_token(self, token: str) -> dict[str, Any]:
        """Sign in with a token the user pasted."""
        return {"error": "not_implemented"}

    def sign_out(self) -> dict[str, Any]:
        """Forget the account and delete its token from the keychain."""
        return {"error": "not_implemented"}
