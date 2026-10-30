"""The screens on macOS, read through AppKit, and the moves of pywebview's Cocoa window.

Cocoa measures y up from the bottom of the first screen, the one with the menu bar. The place is
kept with y measured down from that screen's top instead, which is how pywebview 6.2.1's Cocoa
window takes it when it's placed against the first screen. Its moved event flips y against the
screen the window is on now, so the frame's position is read from the window itself.
"""

from collections.abc import Sequence
from typing import Any

from scmp3.app.window_fit import Area, Display
from scmp3.app.window_place import ReportMove


def flipped(rect: Any, top: float) -> Area:
    """A Cocoa rectangle as an area measured down from the first screen's top, `top` points up."""
    return Area(
        round(rect.origin.x),
        round(top - rect.origin.y - rect.size.height),
        round(rect.size.width),
        round(rect.size.height),
    )


def cocoa_displays(_screens: Sequence[Any], screens: Any = None) -> list[Display]:
    """The screens from AppKit, the first being the one with the menu bar."""
    if screens is None:
        import AppKit

        screens = AppKit.NSScreen.screens()
    found = list(screens)
    if not found:
        return []
    top = found[0].frame().size.height
    # visibleFrame leaves out the menu bar and the Dock.
    return [
        Display(flipped(screen.frame(), top), flipped(screen.visibleFrame(), top), index == 0)
        for index, screen in enumerate(found)
    ]


def watch_cocoa_moves(window: Any, report: ReportMove) -> None:
    """Report the frame's position, read from the window, each time pywebview says it moved."""

    def on_moved() -> None:
        # The screen the window was placed against, whose height pywebview flips y with.
        screen, native = window.screen, window.native
        if screen is None or native is None:
            return
        frame = native.frame()
        report(frame.origin.x, screen.height - frame.origin.y - frame.size.height)

    window.events.moved += on_moved
