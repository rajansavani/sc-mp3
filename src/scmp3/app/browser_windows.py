"""The visible windows of a browser process: their titles, and asking them to close.

The sign-in window watches its titles to see when SoundCloud has signed the person in, since a
browser with a DevTools port can't be used for Google's sign-in. Windows only for now; elsewhere
there are no titles and nothing to close, so the person closes the window themselves.
"""

import sys
from collections.abc import Callable

WM_CLOSE = 0x0010
TITLE_LENGTH = 512  # characters
# True where the titles can be read, so the sign-in window can close by itself.
CAN_READ_TITLES = sys.platform == "win32"


def _visible_windows(pids: set[int]) -> list[int]:
    """The handles of the visible top-level windows these processes own."""
    if not CAN_READ_TITLES:
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    windows: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd: int, _: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    return windows


def window_titles(pids: list[int]) -> list[str]:
    """The titles of these processes' visible windows, such as "Settings on SoundCloud - Brave"."""
    windows = _visible_windows(set(pids))
    if not windows:
        return []
    import ctypes

    user32 = ctypes.windll.user32
    titles: list[str] = []
    for hwnd in windows:
        buffer = ctypes.create_unicode_buffer(TITLE_LENGTH)
        user32.GetWindowTextW(hwnd, buffer, TITLE_LENGTH)
        if buffer.value:
            titles.append(buffer.value)
    return titles


def close_windows(pid: int) -> int:
    """Ask each visible window of one browser process to close, as its X does. Returns how many.

    Unlike stopping the process, this lets the browser save its cookies on the way out.
    """
    windows = _visible_windows({pid})
    if not windows:
        return 0
    import ctypes

    post: Callable[[int, int, int, int], int] = ctypes.windll.user32.PostMessageW
    for hwnd in windows:
        post(hwnd, WM_CLOSE, 0, 0)
    return len(windows)
