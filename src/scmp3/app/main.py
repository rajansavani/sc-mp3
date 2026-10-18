"""Opens the sc-mp3 window."""

import sys

APP_NOT_BUILT = "The app isn't built yet."


def run() -> int:
    """Open the main window and return the exit code; for now it only reports that it's missing."""
    print(APP_NOT_BUILT, file=sys.stderr)
    return 1
