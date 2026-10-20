"""The settings store the app runs with, which tells its watchers each time settings are saved."""

import logging
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from scmp3.core.models import Settings
from scmp3.core.settings import SettingsStore

# Gets the saved settings and the save's number, which counts up from 1.
Watcher = Callable[[Settings, int], None]

log = logging.getLogger(__name__)


class WatchedSettingsStore(SettingsStore):
    """A settings store whose update() calls every watcher with the settings it saved.

    Watchers run outside the store's lock, so two saves on two threads can reach a watcher in
    either order. Each save's number tells the watcher which is newer.
    """

    def __init__(self, path: Path, **options: Any) -> None:
        super().__init__(path, **options)
        self._watchers: list[Watcher] = []
        self._numbering = threading.Lock()
        self._saves = 0

    def watch(self, watcher: Watcher) -> None:
        """Call `watcher` after each update() that saves."""
        self._watchers.append(watcher)

    def update(self, patch: Mapping[str, Any]) -> Settings:
        # Numbered under one lock with the save, so the numbers follow the order on disk.
        with self._numbering:
            saved = super().update(patch)
            self._saves += 1
            number = self._saves
        for watcher in list(self._watchers):
            try:
                watcher(saved, number)
            except Exception:
                # The settings are saved, so the call that saved them still succeeds.
                log.exception("A settings watcher failed")
        return saved
