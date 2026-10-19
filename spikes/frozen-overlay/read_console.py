"""Save the text of the console this runs in to a file, to see what a windowed app printed there."""

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ_WRITE = 0x3
OPEN_EXISTING = 3


class COORD(ctypes.Structure):
    _fields_ = (("X", wintypes.SHORT), ("Y", wintypes.SHORT))


class SMALL_RECT(ctypes.Structure):
    _fields_ = (
        ("Left", wintypes.SHORT),
        ("Top", wintypes.SHORT),
        ("Right", wintypes.SHORT),
        ("Bottom", wintypes.SHORT),
    )


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = (
        ("dwSize", COORD),
        ("dwCursorPosition", COORD),
        ("wAttributes", wintypes.WORD),
        ("srWindow", SMALL_RECT),
        ("dwMaximumWindowSize", COORD),
    )


def read_screen() -> str:
    """Return the console's text from the top of the buffer down to the cursor."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.ReadConsoleOutputCharacterW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        COORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    handle = kernel32.CreateFileW(
        "CONOUT$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
    )
    info = CONSOLE_SCREEN_BUFFER_INFO()
    if not kernel32.GetConsoleScreenBufferInfo(wintypes.HANDLE(handle), ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "GetConsoleScreenBufferInfo failed")
    width = info.dwSize.X
    length = width * (info.dwCursorPosition.Y + 1)
    buffer = ctypes.create_unicode_buffer(length)
    read = wintypes.DWORD()
    kernel32.ReadConsoleOutputCharacterW(handle, buffer, length, COORD(0, 0), ctypes.byref(read))
    text = buffer[: read.value]
    rows = [text[start : start + width].rstrip() for start in range(0, len(text), width)]
    return "\n".join(rows).strip()


def main() -> int:
    """Wait a moment for late output, then save the console text to the path given."""
    target = Path(sys.argv[1])
    time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 0)
    target.write_text(read_screen(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
