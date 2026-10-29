"""Tests for the close question's GTK, Qt and macOS dialogs, against stand-ins for each toolkit."""

import sys
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar

import pytest

from scmp3.app import closing, messages

QUESTION = "2 tracks haven't been saved. Close anyway?\n\nClosing removes them from the queue."
TEXT = "2 tracks haven't been saved. Close anyway?"
DETAIL = "Closing removes them from the queue."


def install_module(monkeypatch: pytest.MonkeyPatch, name: str, **members: Any) -> None:
    """Make `import name` find a stand-in module with these members."""
    module = ModuleType(name)
    for key, value in members.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


class GtkDialog:
    """Stands in for Gtk.MessageDialog, answering with the response in `response`."""

    response = "no"
    made: ClassVar[list["GtkDialog"]] = []

    def __init__(self, **properties: Any) -> None:
        self.properties = properties
        self.default: str | None = None
        self.destroyed = False
        GtkDialog.made.append(self)

    def set_default_response(self, response: str) -> None:
        self.default = response

    def run(self) -> str:
        return GtkDialog.response

    def destroy(self) -> None:
        self.destroyed = True


class GtkWindow:
    def __init__(self, active: bool) -> None:
        self.active = active

    def is_active(self) -> bool:
        return self.active


@pytest.fixture
def gtk(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A stand-in for gi.repository.Gtk, with an inactive window and the active main window."""
    GtkDialog.made = []
    main_window = GtkWindow(active=True)
    windows = [GtkWindow(active=False), main_window]
    toolkit = SimpleNamespace(
        MessageDialog=GtkDialog,
        Window=SimpleNamespace(list_toplevels=lambda: windows),
        MessageType=SimpleNamespace(WARNING="warning"),
        ButtonsType=SimpleNamespace(YES_NO="yes_no"),
        ResponseType=SimpleNamespace(YES="yes", NO="no"),
        main_window=main_window,
    )
    install_module(monkeypatch, "gi")
    install_module(monkeypatch, "gi.repository", Gtk=toolkit)
    return toolkit


# GTK answers DELETE_EVENT when the dialog is closed with Escape or its close button.
@pytest.mark.parametrize(
    ("response", "closes"), [("yes", True), ("no", False), ("delete_event", False)]
)
def test_gtk_asks_yes_or_no_over_the_window_with_no_as_the_default(
    gtk: SimpleNamespace, response: str, closes: bool
) -> None:
    GtkDialog.response = response
    assert closing.ask_with_gtk(QUESTION) is closes
    [dialog] = GtkDialog.made
    assert dialog.properties == {
        "transient_for": gtk.main_window,
        "modal": True,
        "message_type": "warning",
        "buttons": "yes_no",
        "title": messages.UNSAVED_TITLE,
        "text": TEXT,
        "secondary_text": DETAIL,
    }
    assert dialog.default == "no"
    assert dialog.destroyed


def test_gtk_asks_over_the_closing_window_even_when_another_is_active(
    gtk: SimpleNamespace,
) -> None:
    closing_window = GtkWindow(active=False)
    closing.ask_with_gtk(QUESTION, closing_window)
    [dialog] = GtkDialog.made
    assert dialog.properties["transient_for"] is closing_window


class QtButton:
    def __init__(self, name: str) -> None:
        self.name = name


class QtBox:
    """Stands in for QMessageBox, clicking the button named in `click`."""

    click = "no"
    made: ClassVar[list["QtBox"]] = []
    Icon = SimpleNamespace(Warning="warning")
    StandardButton = SimpleNamespace(Yes="yes", No="no")

    def __init__(self, parent: object) -> None:
        self.parent = parent
        self.shown: dict[str, str] = {}
        self.buttons: dict[str, QtButton] = {}
        self.default: QtButton | None = None
        self.clicked: QtButton | None = None
        QtBox.made.append(self)

    def setIcon(self, icon: str) -> None:
        self.shown["icon"] = icon

    def setWindowTitle(self, title: str) -> None:
        self.shown["title"] = title

    def setText(self, text: str) -> None:
        self.shown["text"] = text

    def setInformativeText(self, text: str) -> None:
        self.shown["informative"] = text

    def addButton(self, standard: str) -> QtButton:
        self.buttons[standard] = QtButton(standard)
        return self.buttons[standard]

    def setDefaultButton(self, button: QtButton) -> None:
        self.default = button

    def exec(self) -> int:
        self.clicked = self.buttons.get(QtBox.click)
        return 0

    def clickedButton(self) -> QtButton | None:
        return self.clicked


@pytest.mark.parametrize(("click", "closes"), [("yes", True), ("no", False), ("none", False)])
def test_qt_asks_yes_or_no_over_the_window_with_no_as_the_default(
    monkeypatch: pytest.MonkeyPatch, click: str, closes: bool
) -> None:
    QtBox.made = []
    QtBox.click = click
    main_window = object()
    application = SimpleNamespace(activeWindow=lambda: main_window)
    install_module(monkeypatch, "qtpy")
    install_module(monkeypatch, "qtpy.QtWidgets", QApplication=application, QMessageBox=QtBox)
    assert closing.ask_with_qt(QUESTION) is closes
    [box] = QtBox.made
    assert box.parent is main_window
    assert box.shown == {
        "icon": "warning",
        "title": messages.UNSAVED_TITLE,
        "text": TEXT,
        "informative": DETAIL,
    }
    assert box.default is box.buttons["no"]


def test_qt_asks_over_the_closing_window_even_when_another_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    QtBox.made = []
    closing_window = object()
    application = SimpleNamespace(activeWindow=lambda: object())
    install_module(monkeypatch, "qtpy")
    install_module(monkeypatch, "qtpy.QtWidgets", QApplication=application, QMessageBox=QtBox)
    closing.ask_with_qt(QUESTION, closing_window)
    [box] = QtBox.made
    assert box.parent is closing_window


class MacButton:
    def __init__(self, alert: "MacAlert", title: str) -> None:
        self.alert = alert
        self.title = title

    def setKeyEquivalent_(self, key: str) -> None:
        self.alert.calls.append(("key", (self.title, key)))


class MacAlert:
    """Stands in for NSAlert, returning the code in `press`."""

    press = 1000
    made: ClassVar[list["MacAlert"]] = []

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        MacAlert.made.append(self)

    @classmethod
    def alloc(cls) -> SimpleNamespace:
        return SimpleNamespace(init=cls)

    def setAlertStyle_(self, style: object) -> None:
        self.calls.append(("style", style))

    def setMessageText_(self, text: str) -> None:
        self.calls.append(("text", text))

    def setInformativeText_(self, text: str) -> None:
        self.calls.append(("informative", text))

    def addButtonWithTitle_(self, title: str) -> MacButton:
        self.calls.append(("button", title))
        return MacButton(self, title)

    def runModal(self) -> int:
        return MacAlert.press


# NSAlert answers 1000 for the first button added, and 1001 for the second.
@pytest.mark.parametrize(("press", "closes"), [(1001, True), (1000, False)])
def test_macos_puts_no_first_and_gives_it_the_escape_key(
    monkeypatch: pytest.MonkeyPatch, press: int, closes: bool
) -> None:
    MacAlert.made = []
    MacAlert.press = press
    install_module(
        monkeypatch,
        "AppKit",
        NSAlert=MacAlert,
        NSAlertStyleWarning="warning",
        NSAlertSecondButtonReturn=1001,
    )
    assert closing.ask_on_macos(QUESTION) is closes
    [alert] = MacAlert.made
    assert alert.calls == [
        ("style", "warning"),
        ("text", TEXT),
        ("informative", DETAIL),
        ("button", "No"),
        ("key", ("No", closing.ESCAPE)),
        ("button", "Yes"),
    ]
