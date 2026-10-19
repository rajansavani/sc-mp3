"""Bridge calls for app and engine versions, updates, and YouTube support."""

from typing import Any

from scmp3 import __version__
from scmp3.app.context import AppContext


class ComponentsApi:
    """The update and component calls."""

    _context: AppContext

    def _snapshot_part(self) -> dict[str, Any]:
        """The app's version and the bundled engine's. T-071 sends an updated engine's later."""
        engine_version = self._context.engine_version()
        return {"components": {"app_version": __version__, "engine_version": engine_version}}

    # TODO(T-060): Implement install_youtube_support, and T-071 the update calls.
    def check_updates(self) -> dict[str, Any]:
        """Look for a newer app and a newer download engine."""
        return {"error": "not_implemented"}

    def update_engine(self) -> dict[str, Any]:
        """Download and switch to the newest download engine."""
        return {"error": "not_implemented"}

    def revert_engine(self) -> dict[str, Any]:
        """Go back to the download engine the app shipped with."""
        return {"error": "not_implemented"}

    def install_youtube_support(self) -> dict[str, Any]:
        """Download the JavaScript runtime that YouTube needs."""
        return {"error": "not_implemented"}
