"""Native Windows message boxes, for what the app says when its web UI can't be shown."""

import ctypes
from collections.abc import Callable

# Windows MessageBox flags, and the answers for its Yes and No buttons.
MB_OK = 0x00
MB_YESNO = 0x04
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
# The second button, No, is the one Enter presses.
MB_DEFBUTTON2 = 0x100
# The box keeps the app's own windows from taking clicks while it's up.
MB_TASKMODAL = 0x2000
IDYES = 6
IDNO = 7

# Shows text with a title and flags, and returns the button pressed.
MessageBox = Callable[[str, str, int], int]


def windows_message_box(text: str, title: str, flags: int) -> int:
    """Show a native Windows message box and return the button pressed."""
    # Native, because the web UI is what can't be shown, and a windowed build has no stderr.
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)
