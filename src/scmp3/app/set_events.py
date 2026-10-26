"""The events that tell the UI about a set. The first carries every track; later ones carry only
the tracks whose dicts changed, so a set of hundreds doesn't send every track for each change.

A track's dict is built once per change and kept with its snapshot, so a track that didn't change
has the very same dict as in the last event, and `is` tells them apart.
"""

from typing import Any

# The event for a set whose tracks are mostly as the UI last got them. web/store.js applies it.
SET_TRACKS = "set_tracks"


class SentTracks:
    """The track dicts of one set as the UI last got them.

    Only the event bus's thread builds events, so only that thread reads and writes this.
    """

    def __init__(self) -> None:
        self._children: list[dict[str, Any]] | None = None

    def event(self, view: dict[str, Any]) -> dict[str, Any]:
        """The event for the set's dict: all of it the first time or when its tracks change in
        number, and otherwise the set's own fields with only the tracks that changed."""
        children = view["children"]
        before, self._children = self._children, children
        if before is None or len(before) != len(children):
            return {"type": "job", "job": view}
        changed = [
            [index, child]
            for index, (child, old) in enumerate(zip(children, before, strict=True))
            if child is not old
        ]
        fields = {key: value for key, value in view.items() if key != "children"}
        return {"type": SET_TRACKS, "job": fields, "tracks": changed, "count": len(children)}

    def forget(self) -> None:
        """Start over, since the last event didn't reach the UI, so the next one is whole."""
        self._children = None
