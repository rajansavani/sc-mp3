"""Bridge calls for app and engine versions, updates, and YouTube support."""

import logging
import threading
from typing import Any, Literal

from scmp3 import __version__
from scmp3.app.context import AppContext
from scmp3.core.components import DENO
from scmp3.core.errors import ErrorKind, ScmpError

# What YouTube support is doing. A failure only arrives as a components event, so the snapshot
# after one says missing.
YoutubeState = Literal["missing", "installing", "installed", "failed"]

log = logging.getLogger(__name__)


def youtube_support(
    state: YoutubeState,
    *,
    version: str | None = None,
    pct: float | None = None,
    error: ErrorKind | None = None,
) -> dict[str, Any]:
    """YouTube support's status for the UI, with every key always there."""
    return {
        "state": state,
        "version": version,
        "pct": pct,
        "error": error.value if error else None,
    }


class ComponentsApi:
    """The update and component calls."""

    _context: AppContext

    def _snapshot_part(self) -> dict[str, Any]:
        """The versions, and whether YouTube support is installed. T-071 adds an updated engine."""
        return {"components": self._components_state(self._youtube_status())}

    # TODO(T-071): Implement the update calls.
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
        """Download Deno, the JavaScript runtime YouTube needs. Progress arrives as events."""
        installed = self._context.components.active(DENO)
        if installed is not None:
            self._emit_youtube(youtube_support("installed", version=installed.version))
            return {}
        self._emit_youtube(youtube_support("installing"))
        thread = threading.Thread(
            target=self._install_youtube_support, name="scmp3-youtube-support", daemon=True
        )
        thread.start()
        return {}

    def _install_youtube_support(self) -> None:
        """Install Deno on its own thread, sending the progress and the outcome as events."""
        try:
            manifest = self._context.components.install_deno(progress=self._youtube_progress)
        except ScmpError as error:
            log.warning("YouTube support wasn't installed: %s", error)
            self._emit_youtube(youtube_support("failed", error=error.kind))
            return
        except Exception:
            log.exception("Installing YouTube support failed unexpectedly")
            self._emit_youtube(youtube_support("failed", error=ErrorKind.internal))
            return
        self._emit_youtube(youtube_support("installed", version=manifest.version))

    def _youtube_progress(self, done: int, total: int | None) -> None:
        # The bus keeps only the newest components event, so sending one per chunk is fine.
        pct = round(100 * done / total, 1) if total else None
        self._emit_youtube(youtube_support("installing", pct=pct))

    def _youtube_status(self) -> dict[str, Any]:
        components = self._context.components
        installed = components.active(DENO)
        if installed is not None:
            return youtube_support("installed", version=installed.version)
        # So a page reloaded mid-install doesn't offer the install again.
        if components.installing(DENO):
            return youtube_support("installing")
        return youtube_support("missing")

    def _emit_youtube(self, youtube: dict[str, Any]) -> None:
        event = {"type": "components", "components": self._components_state(youtube)}
        self._context.events.emit(event)

    def _components_state(self, youtube: dict[str, Any]) -> dict[str, Any]:
        """The whole components object, which every components event carries."""
        return {
            "app_version": __version__,
            "engine_version": self._context.engine_version(),
            "youtube_support": youtube,
        }
