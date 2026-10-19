"""The collaborators the bridge shares, so a ticket that adds one adds a field here."""

import importlib.metadata
import logging
from collections.abc import Callable
from dataclasses import dataclass

from scmp3.app.events import EventBus
from scmp3.core import history, settings
from scmp3.core.history import History
from scmp3.core.settings import SettingsStore

ENGINE_PACKAGE = "yt-dlp"

log = logging.getLogger(__name__)


def bundled_engine_version() -> str | None:
    """The installed yt-dlp's version from its package metadata, so startup doesn't import it."""
    try:
        return importlib.metadata.version(ENGINE_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        log.warning("yt-dlp's package metadata is missing, so its version isn't known")
        return None


@dataclass(frozen=True)
class AppContext:
    """What the bridge's mixins use. `main.run()` builds one, and tests build their own."""

    settings: SettingsStore
    history: History
    events: EventBus
    engine_version: Callable[[], str | None] = bundled_engine_version

    @classmethod
    def from_app_folders(cls) -> "AppContext":
        """The context the app runs with, on the files in the app's own folders."""
        return cls(
            settings=SettingsStore(settings.default_path()),
            history=History(history.default_path()),
            events=EventBus(),
        )
