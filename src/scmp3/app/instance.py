"""One copy of the app at a time, since two would race on the same library and queue (T-016)."""

import logging
import sys

from scmp3.app import messages
from scmp3.app.message_box import MB_ICONINFORMATION, MB_OK, MessageBox, windows_message_box
from scmp3.core import paths
from scmp3.core.filelock import FileLock, LockTimeout, LockUnavailable

# Held by the running copy of the app, in the data folder.
INSTANCE_LOCK = "sc-mp3.lock"

log = logging.getLogger(__name__)


def instance_lock() -> FileLock:
    """The lock the running copy holds. A timeout of 0 means a second copy finds out at once."""
    return FileLock(paths.data_dir() / INSTANCE_LOCK, timeout=0)


def another_copy_is_open(lock: FileLock) -> bool:
    """Take the lock for this copy of the app. True when another copy holds it already."""
    try:
        lock.acquire()
    except LockTimeout:
        return True
    except LockUnavailable as error:
        # Refusing to start would be worse than the small chance of two copies.
        log.warning("Can't tell whether sc-mp3 is open already: %s", error)
    return False


def say_already_open(message_box: MessageBox | None = None, platform: str = sys.platform) -> None:
    """Tell the user the app is open already, in a message box on Windows."""
    log.info("sc-mp3 is open already, so this copy closes")
    if platform != "win32":
        print(messages.ALREADY_OPEN, file=sys.stderr)
        return
    show = message_box or windows_message_box
    show(messages.ALREADY_OPEN, messages.ALREADY_OPEN_TITLE, MB_OK | MB_ICONINFORMATION)
