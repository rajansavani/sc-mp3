"""The files dropped on the window, read from GTK, since WebKitGTK gives the page none of them.

WebKitGTK asks a drag for its text/uri-list as soon as the drag comes over the window, before any
drop, and GTK says when a drag is dropped. pywebview 6.2.1 reads only the drag's text/plain, keeps
the paths URL-encoded, and adds each drag's to the last, so its list isn't used here.
"""

import logging
import os
import socket
import threading
from collections.abc import Callable, Iterable
from typing import Any, Protocol
from urllib.parse import unquote_to_bytes

from scmp3.app.closing import Renderer, pywebview_renderer

GTK_RENDERER = "gtkwebkit2"
URI_LIST = "text/uri-list"
FILE_SCHEME = "file:"
# The host names a file: URI may give for this computer, besides its own name.
LOCAL_HOSTS = ("", "localhost")

log = logging.getLogger(__name__)


class DragRecorder(Protocol):
    """What GTK's drag signals report to: a drag's URIs, and its drop."""

    def record(self, drag: object, uris: Iterable[str]) -> None: ...

    def drop(self, drag: object) -> None: ...


# Starts reporting the drags over a window to the recorder, given the window's native GTK window.
WatchDrags = Callable[[Any, DragRecorder], None]


def is_this_computer(host: str, own_name: str) -> bool:
    """Whether a file: URI's host names this computer: no host, localhost or its own name."""
    host = host.lower()
    return host in LOCAL_HOSTS or host == own_name.lower()


def local_path(uri: str, own_name: str | None = None) -> str | None:
    """The path a file: URI names on this computer, or None for any other URI.

    Parsed by hand, since a URL parser would cut a raw # or ? out of the path.
    """
    text = uri.strip()
    if not text.lower().startswith(FILE_SCHEME):
        return None
    rest = text[len(FILE_SCHEME) :]
    host = ""
    if rest.startswith("//"):
        host, slash, path = rest[2:].partition("/")
        rest = slash + path
    if not rest.startswith("/"):
        return None
    if not is_this_computer(host, own_name if own_name is not None else socket.gethostname()):
        return None
    # Decoded as bytes, so a name that isn't UTF-8 comes out as the file system has it.
    return os.fsdecode(unquote_to_bytes(rest))


def local_paths(uris: Iterable[str]) -> list[str]:
    """The local files among the URIs, in their order, each once."""
    paths: list[str] = []
    for uri in uris:
        path = local_path(uri)
        if path and path not in paths:
            paths.append(path)
    return paths


def find_widget(widget: Any, kind: type) -> Any | None:
    """The first widget of `kind` in a GTK widget and its children, at any depth."""
    if isinstance(widget, kind):
        return widget
    for child in widget.get_children() if hasattr(widget, "get_children") else ():
        found = find_widget(child, kind)
        if found is not None:
            return found
    return None


def drag_uris(data: Any) -> list[str] | None:
    """The URIs in GTK's selection data for a drag, or None when it holds another target."""
    if data.get_target().name() != URI_LIST:
        return None
    return list(data.get_uris() or ())


def watch_gtk_drags(native: Any, recorder: DragRecorder) -> None:
    """Report each drag's URIs and each drop on the WebKit view in pywebview's GTK window."""
    # pywebview's GTK backend has already picked the GTK and WebKit2 versions.
    from gi.repository import GLib, WebKit2

    def on_data(_view: Any, drag: Any, _x: int, _y: int, data: Any, *_rest: Any) -> None:
        uris = drag_uris(data)
        if uris is not None:
            recorder.record(drag, uris)

    def on_drop(_view: Any, drag: Any, *_rest: Any) -> bool:
        recorder.drop(drag)
        # False lets WebKit handle the drop as usual.
        return False

    def connect() -> bool:
        view = find_widget(native, WebKit2.WebView)
        if view is None:
            log.warning("Dropped files can't be read, since the window has no WebKit view")
        else:
            view.connect("drag-data-received", on_data)
            view.connect("drag-drop", on_drop)
        # GLib runs it once.
        return False

    # GTK's widgets belong to its own thread.
    GLib.idle_add(connect)


class DragPaths:
    """The files of the last drop on the window, kept until a drop handler takes them.

    A drag's files count only once GTK says that same drag was dropped on the window, so a drag
    let go somewhere else, or a later drag with no files, never hands them to a drop.
    """

    def __init__(
        self, renderer: Renderer = pywebview_renderer, watch_drags: WatchDrags = watch_gtk_drags
    ) -> None:
        self._renderer = renderer
        self._watch_drags = watch_drags
        self._lock = threading.Lock()
        # The drag over the window and its files, until it's dropped or another drag comes.
        self._drag: object | None = None
        self._drag_paths: list[str] = []
        self._dropped: list[str] = []
        self._watching = False

    def watch(self, window: Any) -> None:
        """Start reading drags over the window, once, when pywebview shows it with WebKitGTK."""
        if self._renderer() != GTK_RENDERER:
            return
        # The UI can load more than once, and each load calls this.
        with self._lock:
            started, self._watching = self._watching, True
        if not started:
            self._watch_drags(window.native, self)

    def record(self, drag: object, uris: Iterable[str]) -> None:
        """Keep a drag's files, in place of an earlier drag's, until it's dropped."""
        paths = local_paths(uris)
        with self._lock:
            self._drag, self._drag_paths = drag, paths

    def drop(self, drag: object) -> None:
        """A drag was dropped on the window: its files are the drop's, or none if it had none."""
        with self._lock:
            # The same drag object GTK passed when it gave the drag's URIs.
            self._dropped = self._drag_paths if drag is self._drag else []
            self._drag, self._drag_paths = None, []

    def take(self) -> list[str]:
        """The files of the drop just made, which no later drop gets again."""
        with self._lock:
            paths, self._dropped = self._dropped, []
        return paths
