"""Tests for a set's events: the first carries every track, later ones only the tracks that
changed, and one that never reached the window makes the next one whole again."""

import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

from scmp3.app.events import EventBus
from scmp3.app.set_events import SET_TRACKS, SentTracks


def load_set_job_fakes() -> ModuleType:
    """tests/fixtures/set_job_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_set_job_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "set_job_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


sets = load_set_job_fakes()
WAIT = 5.0


def set_view(*tracks: dict[str, Any]) -> dict[str, Any]:
    return {"id": "set", "kind": "set", "state": "needs_review", "children": list(tracks)}


def test_the_first_event_carries_the_whole_set_and_later_ones_only_changed_tracks() -> None:
    sent = SentTracks()
    one, two = {"id": "1"}, {"id": "2"}
    assert sent.event(set_view(one, two)) == {"type": "job", "job": set_view(one, two)}
    newer = {"id": "2", "title": "New"}
    event = sent.event(set_view(one, newer))
    assert event == {
        "type": SET_TRACKS,
        "job": {"id": "set", "kind": "set", "state": "needs_review"},
        "tracks": [[1, newer]],
        "count": 2,
    }


def test_a_change_in_the_number_of_tracks_sends_the_whole_set() -> None:
    sent = SentTracks()
    sent.event(set_view())
    tracks = ({"id": "1"}, {"id": "2"})
    assert sent.event(set_view(*tracks))["type"] == "job"


def test_after_a_lost_event_the_next_one_is_whole() -> None:
    sent = SentTracks()
    one, two = {"id": "1"}, {"id": "2"}
    sent.event(set_view(one, two))
    sent.event(set_view(one, {"id": "2", "title": "Lost"}))
    sent.forget()
    assert sent.event(set_view(one, two))["type"] == "job"


class FailingOnceWindow:
    """A window whose first script fails, as one closing or reloading would."""

    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.failed = 0
        self._changed = threading.Condition()

    def evaluate_js(self, script: str) -> None:
        with self._changed:
            self._changed.notify_all()
            if self.failed == 0:
                self.failed += 1
                raise RuntimeError("the window went away")
            self.scripts.append(script)

    def wait_for(self, done: Any) -> None:
        with self._changed:
            assert self._changed.wait_for(lambda: done(self), WAIT)


def test_a_sets_event_lost_on_the_way_makes_its_next_event_whole(tmp_path: Path) -> None:
    jobs = sets.make_set_harness(tmp_path / "library")
    bus = EventBus()
    window = FailingOnceWindow()
    try:
        set_id = jobs.add(sets.ALBUM)
        jobs.pools.run("resolve")
        jobs.pools.run("resolve")
        second, third = (track["id"] for track in jobs.job(set_id)["children"][1:3])
        # The UI got the whole set already, so the next event carries one track, and is lost.
        jobs.manager._flow._events = bus
        bus.attach(window)
        jobs.manager.update_fields(second, {"title": "Lost"})
        window.wait_for(lambda w: w.failed == 1)
        jobs.manager.update_fields(third, {"title": "Third"})
        window.wait_for(lambda w: len(w.scripts) == 1)
        # The whole set, so the track whose change was lost arrives too.
        assert '"type": "job"' in window.scripts[0]
        assert '"title": "Lost"' in window.scripts[0]
        assert '"title": "Third"' in window.scripts[0]
    finally:
        bus.close()
        jobs.manager.shut_down()
