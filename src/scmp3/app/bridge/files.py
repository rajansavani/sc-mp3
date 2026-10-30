"""Bridge calls that open file dialogs, hand over dropped files, show files in the file manager,
and open Spotify and its folder for local files."""

import logging
import os
from pathlib import Path
from typing import Any

from scmp3.app import messages
from scmp3.app.context import AppContext
from scmp3.app.desktop import DialogKind
from scmp3.core.errors import ScmpError

INTERNAL = "internal"
FILE_MISSING = "file_missing"
SPOTIFY_MISSING = "spotify_missing"
SPOTIFY_FOLDER_MISSING = "spotify_folder_missing"
SPOTIFY_LINK = "spotify:"
MAX_PATH_CHARACTERS = 8_000

log = logging.getLogger(__name__)


class FilesApi:
    """The file and folder calls. Every dialog goes through `Desktop.choose`."""

    _context: AppContext

    def _snapshot_part(self) -> dict[str, Any]:
        """Files have nothing to show at startup."""
        return {}

    def pick_folder(self) -> dict[str, Any]:
        """Ask for a folder, such as the library folder. No path means it was cancelled."""
        start = self._context.settings.load().library_dir
        paths = self._choose("folder", directory=start)
        if paths is None:
            return {"error": INTERNAL}
        return {"path": paths[0] if paths else None}

    def pick_audio_files(self) -> dict[str, Any]:
        """Ask for MP3 or M4A files to re-tag."""
        paths = self._choose("open", multiple=True, file_types=(messages.AUDIO_FILES,))
        if paths is None:
            return {"error": INTERNAL}
        return {"paths": paths}

    def pick_image(self) -> dict[str, Any]:
        """Ask for an image to use as the cover. No path means it was cancelled."""
        paths = self._choose("open", file_types=(messages.IMAGE_FILES,))
        if paths is None:
            return {"error": INTERNAL}
        return {"path": paths[0] if paths else None}

    def take_dropped_paths(self) -> dict[str, Any]:
        """The files of a drop the page got without them, as WebKitGTK sends drops."""
        return {"paths": self._context.drag_paths.take()}

    def _choose(
        self,
        kind: DialogKind,
        *,
        directory: Path | None = None,
        multiple: bool = False,
        file_types: tuple[str, ...] = (),
    ) -> list[str] | None:
        """The paths a dialog returned, or None when the dialog couldn't be shown."""
        desktop = self._context.desktop
        try:
            return desktop.choose(
                kind, directory=directory, multiple=multiple, file_types=file_types
            )
        except ScmpError as error:
            log.warning("A file dialog couldn't be shown: %s", error)
            return None

    def reveal(self, path: str) -> dict[str, Any]:
        """Show a saved file in Explorer, Finder or the Linux file manager."""
        if not isinstance(path, str) or len(path) > MAX_PATH_CHARACTERS or not os.path.isabs(path):
            log.warning("The UI asked to show a file without giving its full path")
            return {"error": INTERNAL}
        if not self._context.desktop.reveal(Path(path)):
            return {"error": FILE_MISSING}
        return {}

    def open_spotify(self) -> dict[str, Any]:
        """Open the Spotify app."""
        if not self._context.desktop.open_link(SPOTIFY_LINK):
            return {"error": SPOTIFY_MISSING}
        return {}

    def open_local_files_folder(self) -> dict[str, Any]:
        """Show Spotify's local-files.bnk in the file manager, so it can be deleted."""
        location = self._context.find_local_files()
        if location is None or not self._context.desktop.reveal(location):
            return {"error": SPOTIFY_FOLDER_MISSING}
        return {}
