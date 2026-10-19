"""Give a windowed app the console of the terminal that started it, so print() shows up."""

import ctypes
import sys
from ctypes import wintypes
from typing import Any

ATTACH_PARENT_PROCESS = 0xFFFFFFFF


def attach_parent_console() -> dict[str, Any]:
    """Point a missing sys.stdout or sys.stderr at the parent's console, and say what happened."""
    missing = [name for name in ("stdout", "stderr") if getattr(sys, name) is None]
    if not missing:
        # The parent passed a pipe or a file, so print() already reaches it.
        return {"attached": False, "reason": "streams already set"}
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AttachConsole.argtypes = [wintypes.DWORD]
    kernel32.AttachConsole.restype = wintypes.BOOL
    if not kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
        return {
            "attached": False,
            "reason": f"AttachConsole failed, error {ctypes.get_last_error()}",
        }
    console = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    for name in missing:
        setattr(sys, name, console)
    return {"attached": True, "streams": missing}
