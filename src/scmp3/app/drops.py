"""Audio files dropped on the window, whose full paths only a Python drop handler gets.

pywebview 6.2.1 puts a dropped file's path only on the event a Python DOMEventHandler gets, never
on the page's own File (TECH_SPEC §16), so the handler sits on the page's body. WebKitGTK sends
that event with no files at all, so on GTK their paths come from the drag (drag_paths.py).
"""

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import unquote

SAME_NAME_DROP = "SAME_NAME_DROP"
FULL_PATH = "pywebviewFullPath"
URI_LIST = "text/uri-list"
# WebView2 calls the second of two dropped files with the same name "song (1).mp3".
RENAMED_TWIN = re.compile(r" \(\d+\)(?=\.[^.]*$|$)")

# A path pywebview collected for a drop, as it keeps them: the file's name, then its full path.
DroppedPath = tuple[str, str]
# Takes the paths pywebview collected but matched to no file, leaving none behind.
LeftoverPaths = Callable[[], list[DroppedPath]]

log = logging.getLogger(__name__)


class InputQueue(Protocol):
    """The part of the job manager a drop adds files through."""

    def add_inputs(self, text: str) -> dict[str, Any]: ...


class EventSink(Protocol):
    """The part of the event bus a drop tells the UI through."""

    def emit(self, event: Mapping[str, Any]) -> None: ...


class DraggedFiles(Protocol):
    """The files of the drag over the window, for webviews that give the page none."""

    def watch(self, window: Any) -> None: ...

    def take(self) -> list[str]: ...


def take_pywebview_leftovers() -> list[DroppedPath]:
    """The paths pywebview kept without matching them to a file, which it never clears itself."""
    # pywebview keeps them in a private list, and offers no other way to reach them.
    from webview.dom import _dnd_state

    leftovers = [(str(name), str(path)) for name, path in _dnd_state["paths"]]
    _dnd_state["paths"].clear()
    return leftovers


class FileDrops:
    """Adds dropped audio files to the queue, telling the UI about any it turned down."""

    def __init__(
        self,
        jobs: InputQueue,
        events: EventSink,
        dragged: DraggedFiles,
        leftover_paths: LeftoverPaths = take_pywebview_leftovers,
    ) -> None:
        self._jobs = jobs
        self._events = events
        self._dragged = dragged
        self._leftover_paths = leftover_paths

    def bind(self, window: Any) -> None:
        """Listen for drops on the window's page, once it shows the UI."""
        # Imported here, as only the app window needs pywebview.
        from webview.dom import DOMEventHandler

        try:
            # The body never re-renders, and the cover keeps image drops to itself.
            window.dom.body.on("drop", DOMEventHandler(self.dropped))
            self._dragged.watch(window)
        except Exception:
            # The window works without it, apart from dropped files.
            log.exception("Dropped files can't be added, since the drop handler wasn't set up")

    def dropped(self, event: Mapping[str, Any]) -> None:
        """Handle one drop on the page, which pywebview runs on a thread of its own."""
        try:
            # Taken for every drop, so no path from this drop is left for the next one.
            leftovers = self._leftover_paths()
            dragged = self._dragged.take()
            files = _dropped_files(event)
            if files:
                self._add(files, leftovers)
            elif _carries_uris(event):
                # WebKitGTK sent the drop without its files, so the drag's own paths stand in.
                self.add_paths(dragged)
        except Exception:
            log.exception("A drop on the window couldn't be handled")

    def _add(self, files: Sequence[Mapping[str, Any]], leftovers: list[DroppedPath]) -> None:
        files = [_with_own_path(file, leftovers) for file in files]
        if _has_twins(files, leftovers):
            log.info("A drop held two files with the same name, so it was refused")
            self._reject([{"input": "", "reason": SAME_NAME_DROP}])
            return
        if leftovers:
            # Collected for drops elsewhere, such as an image on the cover, on macOS and Linux.
            log.debug("Dropped %d paths that belong to no file in this drop", len(leftovers))
        paths = [file[FULL_PATH] for file in files if isinstance(file.get(FULL_PATH), str)]
        if len(paths) < len(files):
            # Such as an image dragged out of a browser, which has no file on disk.
            log.info("%d dropped files came without their path", len(files) - len(paths))
        self.add_paths(paths)

    def add_paths(self, paths: Sequence[str]) -> None:
        """Add files by their full paths, such as those the app was opened with, like a drop."""
        if not paths:
            return
        # One path a line, so spaces inside a path don't split it.
        answer = self._jobs.add_inputs("\n".join(paths))
        self._reject(answer.get("rejected") or [])

    def _reject(self, rejected: list[dict[str, Any]]) -> None:
        """Show what wasn't added, the same way as a rejection when pasting."""
        if rejected:
            self._events.emit({"type": "rejected", "rejected": rejected})


def _with_own_path(file: Mapping[str, Any], leftovers: list[DroppedPath]) -> Mapping[str, Any]:
    """The file with the leftover path of its exact name, which pywebview missed.

    pywebview URL-decodes the collected name before comparing, so "my%20song.mp3" finds no match.
    """
    if isinstance(file.get(FULL_PATH), str):
        return file
    for leftover in leftovers:
        if leftover[0] == file.get("name"):
            leftovers.remove(leftover)
            return {**file, FULL_PATH: leftover[1]}
    return file


def _has_twins(files: Sequence[Mapping[str, Any]], leftovers: list[DroppedPath]) -> bool:
    """Whether two of the files could have each other's paths, since pywebview matches by name.

    Names are compared URL-decoded, as pywebview compares them. A path left over with the name of
    a dropped file, or of one WebView2 renamed, is the other half of a pair.
    """
    names = [unquote(str(file.get("name", ""))) for file in files]
    if len(set(names)) < len(names):
        return True
    originals = {RENAMED_TWIN.sub("", name) for name in names}
    return any(unquote(name) in originals for name, _path in leftovers)


def _transfer(event: Mapping[str, Any]) -> Mapping[str, Any]:
    """The drop's dataTransfer as pywebview serializes it, or an empty one."""
    transfer = event.get("dataTransfer")
    return transfer if isinstance(transfer, Mapping) else {}


def _dropped_files(event: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    """The files in a drop event as pywebview serializes it, or none."""
    files = _transfer(event).get("files")
    if not isinstance(files, list):
        return []
    return [file for file in files if isinstance(file, Mapping)]


def _carries_uris(event: Mapping[str, Any]) -> bool:
    """Whether the drop carried a uri-list, as a file drag always does."""
    types = _transfer(event).get("types")
    return isinstance(types, list) and URI_LIST in types
