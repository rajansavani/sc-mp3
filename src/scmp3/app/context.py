"""The collaborators the bridge shares, so a ticket that adds one adds a field here."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from scmp3.app.clipboard import ClipboardReader, read_clipboard
from scmp3.app.components_view import components_view
from scmp3.app.desktop import Desktop
from scmp3.app.drag_paths import DragPaths
from scmp3.app.events import EventBus
from scmp3.app.jobs import JobManager
from scmp3.app.settings_watch import WatchedSettingsStore
from scmp3.app.spotify_files import local_files_location
from scmp3.app.update_checks import UpdateChecks
from scmp3.core import history, settings
from scmp3.core.components import Components
from scmp3.core.engine_start import EngineStart
from scmp3.core.engine_updates import EngineUpdates, built_in_version
from scmp3.core.errors import ScmpError
from scmp3.core.history import History
from scmp3.core.settings import SettingsStore

log = logging.getLogger(__name__)


def app_job_manager(context: "AppContext") -> JobManager:
    """The job manager on the real pipeline, following the settings when they're saved."""
    manager = JobManager.for_app(context.settings, context.history, context.events)
    if isinstance(context.settings, WatchedSettingsStore):
        context.settings.watch(manager.settings_changed)
    return manager


def app_update_checks(context: "AppContext") -> UpdateChecks:
    """The update checks on the context's engine and settings, telling the UI what changed."""
    engine = EngineUpdates(
        context.components,
        built_in=context.engine_version(),
        in_use=context.engine_start.update,
    )

    def tell_the_ui() -> None:
        context.events.emit({"type": "components", "components": components_view(context)})

    return UpdateChecks(context.settings, engine, on_change=tell_the_ui)


@dataclass(frozen=True)
class AppContext:
    """What the bridge's mixins use. `main.run()` builds one, and tests build their own."""

    settings: SettingsStore
    history: History
    events: EventBus
    # The built-in yt-dlp's version.
    engine_version: Callable[[], str | None] = built_in_version
    # Which yt-dlp this process uses, which __main__ settles before anything imports it.
    engine_start: EngineStart = field(default_factory=EngineStart)
    # Downloaded helpers such as Deno and engine updates, in the app's components folder.
    components: Components = field(default_factory=Components)
    # File dialogs, the file manager and opening Spotify.
    desktop: Desktop = field(default_factory=Desktop)
    # The files of a drag over the window, which WebKitGTK keeps from the page.
    drag_paths: DragPaths = field(default_factory=DragPaths)
    # Reads the system clipboard, for the offer to add a copied link.
    clipboard: ClipboardReader = read_clipboard
    # Finds Spotify's local-files.bnk, or its folder, for the Spotify help.
    find_local_files: Callable[[], Path | None] = local_files_location
    # Builds the job manager from the fields above. Tests pass one with a fake pipeline.
    make_jobs: Callable[["AppContext"], JobManager] = field(default=app_job_manager, repr=False)
    jobs: JobManager = field(init=False, repr=False)
    # Builds the update checks from the fields above. Tests pass ones with a fake GitHub.
    make_updates: Callable[["AppContext"], UpdateChecks] = field(
        default=app_update_checks, repr=False
    )
    updates: UpdateChecks = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Frozen dataclasses set their own fields this way too.
        object.__setattr__(self, "jobs", self.make_jobs(self))
        object.__setattr__(self, "updates", self.make_updates(self))

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
    def from_app_folders(cls, engine_start: EngineStart | None = None) -> "AppContext":
        """The context the app runs with, on the files in the app's own folders."""
        return cls(
            settings=WatchedSettingsStore(settings.default_path()),
            history=History(history.default_path()),
            events=EventBus(),
            engine_start=engine_start or EngineStart(),
        )
