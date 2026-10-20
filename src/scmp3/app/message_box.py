"""Native Windows message boxes, for what the app says when its web UI can't be shown."""

import ctypes
from collections.abc import Callable

# Windows MessageBox flags, and the answer for its Yes button.
MB_OK = 0x00
MB_YESNO = 0x04
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
IDYES = 6

# Shows text with a title and flags, and returns the button pressed.
MessageBox = Callable[[str, str, int], int]


def windows_message_box(text: str, title: str, flags: int) -> int:
    """Show a native Windows message box and return the button pressed."""
    # Native, because the web UI is what can't be shown, and a windowed build has no stderr.
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)
