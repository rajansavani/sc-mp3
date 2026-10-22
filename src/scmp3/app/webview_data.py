"""The folder WebView2 keeps the window's data in, in sc-mp3's cache, deleted around each window.

The window runs in private mode, so nothing in the folder is worth keeping.
"""

import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from scmp3.core import paths

# In the cache rather than pywebview's own pick, a new temp folder per window that it often can't
# delete, since WebView2's processes still hold it as the window closes.
FOLDER_NAME = "webview"
# How often, and how far apart, deleting is tried while WebView2's processes are still exiting.
DELETE_TRIES = 10
DELETE_PAUSE = 0.3  # seconds
# The start of pywebview's warning when its own delete comes too early.
EARLY_DELETE_WARNING = "Failed to delete user data folder"

log = logging.getLogger(__name__)


def data_folder() -> Path:
    """Where the window's WebView2 data goes."""
    return paths.cache_dir() / FOLDER_NAME


def remove_data_folder(
    folder: Path, *, sleep: Callable[[float], None] = time.sleep, tries: int = DELETE_TRIES
) -> bool:
    """Delete the folder, trying again while WebView2's processes still hold it. True once gone."""
    for attempt in range(1, tries + 1):
        try:
            shutil.rmtree(folder)
            return True
        except FileNotFoundError:
            return True
        except OSError as error:
            if attempt == tries:
                # The next window's start deletes it, once nothing holds it any more.
                log.debug("WebView2's data folder is still in use: %s", error.strerror or error)
                return False
        sleep(DELETE_PAUSE)
    return False


class EarlyDeleteFilter(logging.Filter):
    """Drops pywebview's warning about deleting the folder too early, since it's deleted later."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith(EARLY_DELETE_WARNING)
