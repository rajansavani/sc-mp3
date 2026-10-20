"""The collaborators the bridge shares, so a ticket that adds one adds a field here."""

import importlib.metadata
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from scmp3.app.desktop import Desktop
from scmp3.app.events import EventBus
from scmp3.app.jobs import JobManager
from scmp3.app.settings_watch import WatchedSettingsStore
from scmp3.core import history, settings
from scmp3.core.components import Components
from scmp3.core.errors import ScmpError
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


def app_job_manager(context: "AppContext") -> JobManager:
    """The job manager on the real pipeline, following the settings when they're saved."""
    manager = JobManager.for_app(context.settings, context.history, context.events)
    if isinstance(context.settings, WatchedSettingsStore):
        context.settings.watch(manager.settings_changed)
    return manager


@dataclass(frozen=True)
class AppContext:
    """What the bridge's mixins use. `main.run()` builds one, and tests build their own."""

    settings: SettingsStore
    history: History
    events: EventBus
    engine_version: Callable[[], str | None] = bundled_engine_version
    # Downloaded helpers such as Deno, in the app's components folder.
    components: Components = field(default_factory=Components)
    # File dialogs, the file manager and opening Spotify.
    desktop: Desktop = field(default_factory=Desktop)
    # Builds the job manager from the fields above. Tests pass one with a fake pipeline.
    make_jobs: Callable[["AppContext"], JobManager] = field(default=app_job_manager, repr=False)
    jobs: JobManager = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Frozen dataclasses set their own fields this way too.
        object.__setattr__(self, "jobs", self.make_jobs(self))

    def open_stores(self) -> None:
        """Open history.db now, before the window loads, creating it on a first run.

        Creating it takes much longer than opening it, over 100 ms on a slow disk, so the first
        bridge call that reads it would otherwise be slow.
        """
        try:
            self.history.recent(1)
        except ScmpError as error:
            # The app works without it; saves just can't warn about repeats.
            log.warning("The history couldn't be opened: %s", error)

    @classmethod
    def from_app_folders(cls) -> "AppContext":
        """The context the app runs with, on the files in the app's own folders."""
        return cls(
            settings=WatchedSettingsStore(settings.default_path()),
            history=History(history.default_path()),
            events=EventBus(),
        )
