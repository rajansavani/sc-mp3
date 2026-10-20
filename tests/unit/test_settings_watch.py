"""Tests for the settings store that tells its watchers when settings are saved."""

import logging
import threading

import pytest

from scmp3.app.settings_watch import WatchedSettingsStore
from scmp3.core import paths
from scmp3.core.models import Settings
from scmp3.core.settings import InvalidSettings


@pytest.fixture
def store() -> WatchedSettingsStore:
    return WatchedSettingsStore(paths.config_dir() / "settings.json")


def test_every_watcher_hears_about_each_save(store: WatchedSettingsStore) -> None:
    heard: list[tuple[str, Settings, int]] = []
    store.watch(lambda settings, number: heard.append(("first", settings, number)))
    store.watch(lambda settings, number: heard.append(("second", settings, number)))
    saved = store.update({"concurrent_downloads": 3})
    assert heard == [("first", saved, 1), ("second", saved, 1)]
    assert saved.concurrent_downloads == 3


def test_each_save_gets_the_next_number(store: WatchedSettingsStore) -> None:
    heard: list[tuple[int, int]] = []
    store.watch(lambda settings, number: heard.append((number, settings.concurrent_downloads)))
    for downloads in (1, 2, 3):
        store.update({"concurrent_downloads": downloads})
    assert heard == [(1, 1), (2, 2), (3, 3)]


def test_saves_on_many_threads_are_numbered_in_the_order_they_were_saved(
    store: WatchedSettingsStore,
) -> None:
    numbers: dict[int, int] = {}
    lock = threading.Lock()

    def remember(settings: Settings, number: int) -> None:
        with lock:
            numbers[number] = settings.concurrent_downloads

    store.watch(remember)
    threads = [
        threading.Thread(target=store.update, args=({"concurrent_downloads": 1 + n % 4},))
        for n in range(12)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(numbers) == list(range(1, 13))
    # The highest number is the save that's on disk now.
    assert numbers[12] == store.load().concurrent_downloads


def test_a_save_is_numbered_before_the_next_one_can_start() -> None:
    writing = threading.Event()
    finish = threading.Event()

    class SlowStore(WatchedSettingsStore):
        def save(self, settings: Settings) -> None:
            if settings.concurrent_downloads == 1:
                writing.set()
                finish.wait(10)
            super().save(settings)

    store = SlowStore(paths.config_dir() / "settings.json")
    numbers: list[tuple[int, int]] = []
    store.watch(lambda settings, number: numbers.append((number, settings.concurrent_downloads)))
    first = threading.Thread(target=store.update, args=({"concurrent_downloads": 1},))
    second = threading.Thread(target=store.update, args=({"concurrent_downloads": 2},))
    first.start()
    try:
        assert writing.wait(10)
        # While the first save is on its way to disk, its number is already held for it.
        assert store._numbering.locked()
        second.start()
    finally:
        finish.set()
        first.join(10)
        if second.ident is not None:
            second.join(10)
    assert sorted(numbers) == [(1, 1), (2, 2)]
    assert store.load().concurrent_downloads == 2


def test_a_refused_patch_tells_no_one(store: WatchedSettingsStore) -> None:
    heard: list[Settings] = []
    store.watch(lambda settings, number: heard.append(settings))
    with pytest.raises(InvalidSettings):
        store.update({"concurrent_downloads": 9})
    assert heard == []


def test_a_failing_watcher_is_logged_and_the_save_still_counts(
    store: WatchedSettingsStore, caplog: pytest.LogCaptureFixture
) -> None:
    heard: list[Settings] = []

    def broken(settings: Settings, number: int) -> None:
        raise RuntimeError("broken watcher")

    store.watch(broken)
    store.watch(lambda settings, number: heard.append(settings))
    with caplog.at_level(logging.ERROR, logger="scmp3.app.settings_watch"):
        saved = store.update({"title_style": "feat_in_title"})
    assert heard == [saved]
    assert store.load().title_style == "feat_in_title"
    assert "A settings watcher failed" in caplog.text
