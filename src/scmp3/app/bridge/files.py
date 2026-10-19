"""Bridge calls that open file dialogs, show files in the file manager, and open Spotify."""

from typing import Any


class FilesApi:
    """The file and folder calls."""

    def _snapshot_part(self) -> dict[str, Any]:
        """Files have nothing to show at startup."""
        return {}

    # TODO(T-022): Implement these, with every dialog going through one helper.
    def pick_folder(self) -> dict[str, Any]:
        """Ask for a folder, such as the library folder."""
        return {"error": "not_implemented"}

    def pick_audio_files(self) -> dict[str, Any]:
        """Ask for MP3 or M4A files to re-tag."""
        return {"error": "not_implemented"}

    def pick_image(self) -> dict[str, Any]:
        """Ask for an image to use as the cover."""
        return {"error": "not_implemented"}

    def reveal(self, path: str) -> dict[str, Any]:
        """Show a saved file in Explorer, Finder or the Linux file manager."""
        return {"error": "not_implemented"}

    def open_spotify(self) -> dict[str, Any]:
        """Open the Spotify app."""
        return {"error": "not_implemented"}
