"""Asking before the window closes while tracks in the queue haven't been saved, if closing would
lose them.

queue.json brings the queue back at the next start, so closing normally asks nothing. Only when
queue.json can't be written does closing drop the tracks, and then the user decides.
"""

import ctypes
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from scmp3.app import messages
from scmp3.app.message_box import (
    IDYES,
    MB_DEFBUTTON2,
    MB_ICONWARNING,
    MB_TASKMODAL,
    MB_YESNO,
    MessageBox,
    windows_message_box,
)

# Asks a yes or no question over a native window, or None, and says whether the answer was yes.
Ask = Callable[[str, Any], bool]
# The renderer pywebview chose, such as edgechromium or gtkwebkit2, or None before it started.
Renderer = Callable[[], str | None]
# GetSystemMetrics' index for "this session is logging off or shutting down".
SM_SHUTTINGDOWN = 0x2000
# The Escape key, as AppKit names a button's key equivalent.
ESCAPE = "\x1b"

log = logging.getLogger(__name__)


class UnsavedJobs(Protocol):
    """The part of the job manager the question needs."""

    def unsaved_count(self) -> int: ...

    def keep_queue(self) -> bool: ...


@dataclass(frozen=True)
class Asking:
    """How one toolkit asks, and how it tells that the session is ending, when nobody can answer."""

    ask: Ask
    session_ending: Callable[[], bool]


def split_question(question: str) -> tuple[str, str]:
    """The question's first paragraph, which dialogs outside Windows show in bold, and the rest."""
    text, _, detail = question.partition("\n\n")
    return text, detail


def ask_on_windows(
    question: str, parent: Any = None, *, message_box: MessageBox | None = None
) -> bool:
    """Ask with a native message box over the window, with No as the default answer."""
    # The box is task-modal, so it covers the app's windows without needing one as its owner.
    # Looked up here rather than as a default, so a test that replaces it is never bypassed.
    show = message_box or windows_message_box
    flags = MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_TASKMODAL
    return show(question, messages.UNSAVED_TITLE, flags) == IDYES


def windows_session_ending() -> bool:
    """Whether Windows is logging off or shutting down."""
    return bool(ctypes.windll.user32.GetSystemMetrics(SM_SHUTTINGDOWN))


def ask_with_gtk(question: str, parent: Any = None) -> bool:
    """Ask with a GTK dialog over `parent`, or the active window, with No as the default."""
    # pywebview's GTK backend has already required GTK 3, so this gets the same version.
    from gi.repository import Gtk

    text, detail = split_question(question)
    if parent is None:
        parent = next(
            (window for window in Gtk.Window.list_toplevels() if window.is_active()), None
        )
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.YES_NO,
        title=messages.UNSAVED_TITLE,
        text=text,
        secondary_text=detail,
    )
    dialog.set_default_response(Gtk.ResponseType.NO)
    try:
        # pywebview calls the closing handler on GTK's thread, and run() keeps GTK's loop going.
        return dialog.run() == Gtk.ResponseType.YES
    finally:
        dialog.destroy()


def ask_with_qt(question: str, parent: Any = None) -> bool:
    """Ask with a Qt message box over `parent`, or the active window, with No as the default."""
    from qtpy.QtWidgets import QApplication, QMessageBox

    text, detail = split_question(question)
    box = QMessageBox(parent if parent is not None else QApplication.activeWindow())
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(messages.UNSAVED_TITLE)
    box.setText(text)
    box.setInformativeText(detail)
    yes = box.addButton(QMessageBox.StandardButton.Yes)
    no = box.addButton(QMessageBox.StandardButton.No)
    box.setDefaultButton(no)
    # pywebview calls the closing handler on Qt's thread, and exec() keeps Qt's loop going.
    box.exec()
    return box.clickedButton() == yes


def ask_on_macos(question: str, parent: Any = None) -> bool:
    """Ask with a macOS alert, where No sits on the right and Escape answers it."""
    import AppKit

    # runModal() blocks the whole app, so the alert needs no window to belong to.
    text, detail = split_question(question)
    alert = AppKit.NSAlert.alloc().init()
    alert.setAlertStyle_(AppKit.NSAlertStyleWarning)
    alert.setMessageText_(text)
    alert.setInformativeText_(detail)
    # The first button added sits on the right, so No goes in first.
    no = alert.addButtonWithTitle_(messages.NO)
    # NSAlert gives Escape only to a button titled Cancel, so No takes it, in place of Return.
    no.setKeyEquivalent_(ESCAPE)
    alert.addButtonWithTitle_(messages.YES)
    # pywebview calls the closing handler on the main thread, and runModal() keeps its loop going.
    return alert.runModal() == AppKit.NSAlertSecondButtonReturn


def session_goes_on() -> bool:
    """For toolkits that can't tell a close from logging off, which then ask as usual."""
    return False


# How each of pywebview's renderers asks. Any other closes the window without asking.
ASKERS: dict[str, Asking] = {
    "edgechromium": Asking(ask_on_windows, windows_session_ending),
    "gtkwebkit2": Asking(ask_with_gtk, session_goes_on),
    "qtwebengine": Asking(ask_with_qt, session_goes_on),
    "wkwebview": Asking(ask_on_macos, session_goes_on),
}


def asking_for(renderer: str | None) -> Asking | None:
    """How the renderer asks, or None for one that has no way to."""
    return ASKERS.get(renderer) if renderer else None


def pywebview_renderer() -> str | None:
    """The renderer pywebview chose when it started."""
    # Imported here, since the app imports pywebview only once its logging is set up.
    import webview

    return webview.renderer


class CloseGuard:
    """pywebview's closing handler, which keeps the window open if the user says so."""

    def __init__(self, jobs: UnsavedJobs, find_asking: Callable[[], Asking | None]) -> None:
        self._jobs = jobs
        # Looked up at each close, since pywebview picks its renderer only once it starts.
        self._find_asking = find_asking

    def closing(self, window: Any = None) -> bool:
        """Whether the window may close. pywebview cancels the close on False.

        pywebview passes the window, since the parameter is named `window`.
        """
        count = self._jobs.unsaved_count()
        if count == 0:
            return True
        asking = self._find_asking()
        if asking is None:
            return True
        # Written now, while the window can still stay open if it fails.
        if self._jobs.keep_queue():
            return True
        if asking.session_ending():
            # A question would hold up logging off, and a save placing its file gets 5 s anyway.
            log.info("Windows is ending the session, so the window closed with %d unsaved", count)
            return True
        if asking.ask(messages.unsaved(count), getattr(window, "native", None)):
            log.info("The window closed with %d tracks unsaved", count)
            return True
        log.info("The window stayed open, since %d tracks haven't been saved", count)
        return False


def close_guard(jobs: UnsavedJobs, renderer: Renderer = pywebview_renderer) -> CloseGuard:
    """The closing handler, which asks the way pywebview's renderer does."""
    return CloseGuard(jobs, lambda: asking_for(renderer()))
