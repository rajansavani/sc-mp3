"""Runs the UI in a hidden app window with a set in the queue, sends the page a set event that
doesn't fit the set it has, and while the page fetches the queue again, sends a change to one
track. Prints the track's title as the page ends up with it, and how many snapshots were asked
for, as JSON. test_ui_events.py runs it in a child process, since pywebview can start only once
per process. It closes itself within 300 seconds.
"""

import copy
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import webview
from webview.util import get_app_root

from scmp3.app import main, page_loading
from scmp3.app.bridge import Api
from scmp3.app.context import AppContext
from scmp3.app.events import EventBus
from scmp3.app.jobs import JobManager
from scmp3.app.workers import TaskPool
from scmp3.core import history, paths, settings
from scmp3.core.history import History
from scmp3.core.settings import SettingsStore


def load_set_job_fakes() -> Any:
    name = "scmp3_test_fixture_set_job_fakes"
    path = Path(__file__).parent / "set_job_fakes.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sets = load_set_job_fakes()

# How long the page's second snapshot takes, so the change arrives while it's out.
REFETCH_DELAY = 1.5
# How long the UI may take to load, and then each step, on a busy machine.
UI_WAIT = 240.0
WAIT = 20.0
GIVE_UP_AFTER = 300.0

# Keeps the page's store where the checks below can read it.
EXPOSE_STORE = "import('./store.js').then((store) => { window.__store = store; }); 0"
TRACK_TITLES = (
    "window.__store ? JSON.stringify("
    "window.__store.getState().jobs[0]?.children.map((track) => track.tags?.title ?? null)) : null"
)


class SlowRefetchApi(Api):
    """The bridge, where every snapshot after the page's first takes a moment to come back, after
    it's taken. It counts the snapshots, and the most out at once."""

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._counts_lock = threading.Lock()
        self._snapshot_calls = 0
        self._out = 0
        self._most_out = 0

    def snapshot(self) -> dict[str, Any]:
        with self._counts_lock:
            self._snapshot_calls += 1
            later = self._snapshot_calls > 1
            self._out += 1
            self._most_out = max(self._most_out, self._out)
        try:
            answer = super().snapshot()
            if later:
                time.sleep(REFETCH_DELAY)
            return answer
        finally:
            with self._counts_lock:
                self._out -= 1


def make_jobs(context: AppContext) -> JobManager:
    steps = sets.SetSteps(progress=())
    sets.add_set(steps, sets.ALBUM, sets.playlist_for(sets.ALBUM, 5))
    covers = sets.fakes.FakeCovers()
    return JobManager(
        context.settings, context.events, steps=steps, covers=covers, make_pool=TaskPool
    )


def wait_until(check: Any, timeout: float = WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


def emit(window: webview.Window, event: dict[str, Any]) -> None:
    window.evaluate_js(f"window.scmp3.emit({json.dumps(event)}); 0")


def drive(
    window: webview.Window,
    context: AppContext,
    api: SlowRefetchApi,
    ui_ready: threading.Event,
    result: dict[str, Any],
) -> None:
    jobs = context.jobs
    try:
        if not ui_ready.wait(UI_WAIT):
            result["error"] = f"the UI didn't load within {UI_WAIT:.0f} s"
            return
        window.evaluate_js(EXPOSE_STORE)
        window.evaluate_js(f"window.pywebview.api.add_inputs({json.dumps(sets.ALBUM)}); 0")
        titles = [f"Track {n}" for n in range(1, 6)]

        def ready() -> bool:
            return json.loads(window.evaluate_js(TRACK_TITLES) or "[]") == titles

        if not wait_until(ready) or not jobs.wait_until_idle(WAIT):
            result["error"] = "the set's tracks never reached the page"
            return
        view = jobs.views()[0]
        fields = {key: value for key, value in view.items() if key != "children"}
        changed = copy.deepcopy(view["children"][1])
        changed["tags"]["title"] = "Held change"
        misfit = {"type": "set_tracks", "job": fields, "tracks": [], "count": 99}
        # A second misfit while a fetch is out waits for it, and fetches only after it.
        emit(window, misfit)
        emit(window, misfit)
        time.sleep(REFETCH_DELAY * 2 + 1)
        # A change that comes while the fetch is out goes on top of the snapshot.
        emit(window, misfit)
        emit(window, {"type": "set_tracks", "job": fields, "tracks": [[1, changed]], "count": 5})
        time.sleep(REFETCH_DELAY + 1)
        result["titles"] = json.loads(window.evaluate_js(TRACK_TITLES))
        result["snapshots"] = api._snapshot_calls
        result["most_out_at_once"] = api._most_out
    except Exception as error:
        result["error"] = repr(error)
    finally:
        window.destroy()


def watch_for_the_ui(window: webview.Window, events: EventBus) -> threading.Event:
    """Load the page as the app does, retrying a failed first load, and say when the UI is up."""
    page_watch = page_loading.PageWatch(events)
    ui_ready = threading.Event()

    def loaded(window: webview.Window) -> None:
        page_watch.loaded(window)
        if not page_watch.failed and page_loading.shows_the_ui(window):
            ui_ready.set()

    window.events.loaded += loaded
    return ui_ready


def give_up() -> None:
    time.sleep(GIVE_UP_AFTER)
    print(json.dumps({"error": "the window didn't finish in time"}), flush=True)
    os._exit(3)


def run() -> int:
    main.register_web_types()
    main.route_pywebview_logging()
    context = AppContext(
        settings=SettingsStore(settings.default_path()),
        history=History(history.default_path()),
        events=EventBus(),
        make_jobs=make_jobs,
    )
    context.settings.update(
        {
            "first_run_done": True,
            "offer_clipboard_links": False,
            "download_while_reviewing": False,
            "library_dir": str(paths.data_dir() / "library"),
        }
    )
    context.jobs.start_up()
    api = SlowRefetchApi(context)
    page = page_loading.page_location(main.INDEX_PAGE, Path(get_app_root()))
    page_loading.start_page_server(webview.http.start_global_server, page)
    window = webview.create_window("sc-mp3 test", page, js_api=api, hidden=True)
    ui_ready = watch_for_the_ui(window, context.events)
    result: dict[str, Any] = {}
    threading.Thread(target=give_up, daemon=True).start()
    threading.Thread(
        target=drive, args=(window, context, api, ui_ready, result), daemon=True
    ).start()
    webview.start(http_server=True, private_mode=True)
    context.events.close()
    context.jobs.shut_down()
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run())
