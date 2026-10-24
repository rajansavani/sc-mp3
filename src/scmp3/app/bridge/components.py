"""Bridge calls for versions, update checks, engine updates, YouTube support and the logs."""

import logging
import threading
from typing import Any

from scmp3.app.components_view import components_view, youtube_support
from scmp3.app.context import AppContext
from scmp3.core import paths
from scmp3.core.components import DENO
from scmp3.core.errors import Cancelled, ErrorKind, ScmpError
from scmp3.core.log import LOG_FILE
from scmp3.core.sources.deno import find_deno, forget_broken_copy

FILE_MISSING = "file_missing"

log = logging.getLogger(__name__)


class ComponentsApi:
    """The update and component calls."""

    _context: AppContext
    # Set to stop the install that's running, which then reports YouTube support as missing.
    _youtube_cancel: threading.Event | None = None

    def _snapshot_part(self) -> dict[str, Any]:
        """The versions, what a restart would change, the app's latest release, YouTube support."""
        return {"components": components_view(self._context)}

    def check_updates(self) -> dict[str, Any]:
        """Start the update checks: sc-mp3's latest release, and the engine's when it's on.

        Returns at once. The progress and the outcome arrive in components events, under
        `updates`. A newer engine is installed and used from the next start.
        """
        self._context.updates.start_task("check")
        return {}

    def update_engine(self) -> dict[str, Any]:
        """Start installing the newest engine on the channel in Settings, used from the next start.

        Returns at once, and the outcome arrives as check_updates' does.
        """
        self._context.updates.start_task("update")
        return {}

    def revert_engine(self) -> dict[str, Any]:
        """Go back to the download engine the app shipped with, from the next start."""
        try:
            self._context.updates.revert_engine()
        except ScmpError as error:
            log.warning("The download engine wasn't reverted: %s", error)
            return {"error": error.kind.value}
        return {}

    def open_logs(self) -> dict[str, Any]:
        """Show the log file in Explorer, Finder or the Linux file manager."""
        if not self._context.desktop.reveal(paths.log_dir() / LOG_FILE):
            return {"error": FILE_MISSING}
        return {}

    def install_youtube_support(self) -> dict[str, Any]:
        """Start downloading Deno, the JavaScript runtime YouTube needs.

        Returns at once. The progress and the outcome arrive in components events, under
        `youtube_support`. Once it's there, the jobs that were waiting for it start again.
        """
        components = self._context.components
        installed = find_deno(components)
        if installed is not None:
            self._emit_youtube(youtube_support("installed", version=installed.version))
            self._context.jobs.youtube_support_ready()
            return {}
        self._emit_youtube(youtube_support("installing"))
        if components.installing(DENO):
            # A second click while an install runs doesn't start another one.
            return {}
        cancel = self._youtube_cancel = threading.Event()
        thread = threading.Thread(
            target=self._install_youtube_support,
            args=(cancel,),
            name="scmp3-youtube-support",
            daemon=True,
        )
        thread.start()
        return {}

    def cancel_youtube_support(self) -> dict[str, Any]:
        """Stop the YouTube support download. Nothing of it is kept."""
        if self._youtube_cancel is not None:
            self._youtube_cancel.set()
        return {}

    def _install_youtube_support(self, cancel: threading.Event) -> None:
        """Install Deno on its own thread, sending the progress and the outcome as events."""

        def progress(done: int, total: int | None) -> None:
            if cancel.is_set():
                # The install deletes what it downloaded on its way out.
                raise Cancelled
            self._youtube_progress(done, total)

        try:
            # A downloaded copy that doesn't run is downloaded again, rather than kept.
            forget_broken_copy(self._context.components)
            manifest = self._context.components.install_deno(progress=progress)
        except Cancelled:
            log.info("The YouTube support download was cancelled")
            self._emit_youtube(youtube_support("missing"))
            return
        except ScmpError as error:
            log.warning("YouTube support wasn't installed: %s", error)
            self._emit_youtube(youtube_support("failed", error=error.kind))
            return
        except Exception:
            log.exception("Installing YouTube support failed unexpectedly")
            self._emit_youtube(youtube_support("failed", error=ErrorKind.internal))
            return
        self._emit_youtube(youtube_support("installed", version=manifest.version))
        self._context.jobs.youtube_support_ready()

    def _youtube_progress(self, done: int, total: int | None) -> None:
        # The bus keeps only the newest components event, so sending one per chunk is fine.
        pct = round(100 * done / total, 1) if total else None
        progress = youtube_support("installing", pct=pct, downloaded=done, total=total)
        self._emit_youtube(progress)

    def _emit_youtube(self, youtube: dict[str, Any]) -> None:
        event = {"type": "components", "components": components_view(self._context, youtube)}
        self._context.events.emit(event)
