"""Tests for noticing yt-dlp's "Continuing as guest" warning."""

import logging
import threading

from scmp3.core import engine
from scmp3.core.guest_warning import GuestWarningWatcher

GUEST_LINE = "[soundcloud] Provided authorization token is invalid. Continuing as guest"


def test_the_watcher_notices_yt_dlps_guest_warning() -> None:
    with engine.make_ydl() as ydl, GuestWarningWatcher() as watcher:
        ydl.report_warning(GUEST_LINE)
    assert watcher.saw_guest_warning


def test_the_watcher_ignores_other_warnings() -> None:
    with GuestWarningWatcher() as watcher:
        engine.YtDlpLogger().warning("[soundcloud] 123: Original download format isn't available")
        engine.YtDlpLogger().debug(GUEST_LINE)
        logging.getLogger("scmp3.other").warning(GUEST_LINE)
    assert not watcher.saw_guest_warning


def test_the_watcher_ignores_other_threads() -> None:
    with GuestWarningWatcher() as watcher:
        other = threading.Thread(target=engine.YtDlpLogger().warning, args=(GUEST_LINE,))
        other.start()
        other.join(timeout=5)
    assert not watcher.saw_guest_warning


def test_the_watcher_stops_watching_when_its_block_ends() -> None:
    with GuestWarningWatcher() as watcher:
        pass
    engine.YtDlpLogger().warning(GUEST_LINE)
    assert not watcher.saw_guest_warning
    assert watcher not in logging.getLogger(engine.YTDLP_LOGGER).handlers
