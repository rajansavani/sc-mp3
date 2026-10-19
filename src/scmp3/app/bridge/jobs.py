"""Bridge calls for the queue: adding, editing, saving and sets, plus history and diagnostics."""

from typing import Any


class JobsApi:
    """The queue's calls. Each returns right away; progress arrives as events."""

    # TODO(T-022): Implement these calls and the queue's snapshot with the job manager.
    def _snapshot_part(self) -> dict[str, Any]:
        """The queue's part of `snapshot()`."""
        return {"jobs": []}

    # Inputs

    def add_inputs(self, text_or_paths: str | list[str]) -> dict[str, Any]:
        """Queue pasted text or dropped paths. Returns the new job ids and what was rejected."""
        return {"error": "not_implemented"}

    # Editing

    def update_fields(self, job_id: str, patch: dict[str, str]) -> dict[str, Any]:
        """Set fields the user typed by hand, which win over the suggestion."""
        return {"error": "not_implemented"}

    def reset_field(self, job_id: str, field: str) -> dict[str, Any]:
        """Drop a hand edit, so the field follows the suggestion again."""
        return {"error": "not_implemented"}

    def toggle_change(self, job_id: str, change_id: str, enabled: bool) -> dict[str, Any]:
        """Turn one automatic cleanup on or off."""
        return {"error": "not_implemented"}

    def set_style(self, job_id: str, style_id: str) -> dict[str, Any]:
        """Use a different title style for this track."""
        return {"error": "not_implemented"}

    def set_artwork(
        self, job_id: str, artwork: dict[str, str], crop: dict[str, float] | None = None
    ) -> dict[str, Any]:
        """Replace the cover with a file, a URL or a pasted image, with an optional square crop."""
        return {"error": "not_implemented"}

    # Actions

    def save(self, job_ids: list[str]) -> dict[str, Any]:
        """Tag the jobs and move them into the library."""
        return {"error": "not_implemented"}

    def skip(self, job_id: str) -> dict[str, Any]:
        """Leave a job unsaved and delete its staged audio."""
        return {"error": "not_implemented"}

    def retry(self, job_id: str) -> dict[str, Any]:
        """Start a failed job again."""
        return {"error": "not_implemented"}

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Stop a job's download or conversion."""
        return {"error": "not_implemented"}

    def remove(self, job_id: str) -> dict[str, Any]:
        """Take a job out of the queue."""
        return {"error": "not_implemented"}

    # Sets

    def set_album_mode(self, job_id: str, mode: str) -> dict[str, Any]:
        """Save a set as one album or as separate singles."""
        return {"error": "not_implemented"}

    def set_included(self, job_id: str, child_id: str, included: bool) -> dict[str, Any]:
        """Choose whether one track of a set gets saved."""
        return {"error": "not_implemented"}

    # Misc

    def history(self, query: str = "", limit: int = 50) -> dict[str, Any]:
        """Search the files saved before."""
        return {"error": "not_implemented"}

    def diagnostics(self) -> dict[str, Any]:
        """Versions, settings and recent log lines, with secrets masked, for a bug report."""
        return {"error": "not_implemented"}
