"""Bridge calls for the options menu and first-run setup."""

from typing import Any

from scmp3.app.context import AppContext


class SettingsApi:
    """The settings calls."""

    _context: AppContext

    def _snapshot_part(self) -> dict[str, Any]:
        """The settings, and whether first-run setup still has to run."""
        settings = self._context.settings.load()
        return {"settings": settings.to_dict(), "first_run": not settings.first_run_done}

    # TODO(T-024): Implement these with SettingsStore.update and the suggestion engine.
    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Change some settings and save them."""
        return {"error": "not_implemented"}

    def preview_render(
        self, sample: dict[str, Any] | None = None, style: str | None = None
    ) -> dict[str, Any]:
        """The title, artist and filename a sample track would get, for live previews."""
        return {"error": "not_implemented"}
