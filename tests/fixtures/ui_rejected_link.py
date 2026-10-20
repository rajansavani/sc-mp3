"""Runs the UI in a hidden app window, adds a short link that turns out to be a set, and prints
the page's text once the job has left the queue, as JSON. test_ui_events.py runs it in a child
process, since pywebview can start only once per process. It closes itself within 300 seconds.

WebView2 can take well over 20 seconds to start on a busy machine, which is as long as
pywebview's own calls wait for the page. So this waits for the UI itself, with room to spare,
before it calls into the page.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import webview
from webview.util import get_app_root

from scmp3.app import main
from scmp3.app.bridge import Api
from scmp3.app.context import AppContext
from scmp3.app.events import EventBus
from scmp3.app.job_covers import CoverReader
from scmp3.app.jobs import JobManager
from scmp3.core import history, pipeline, settings
from scmp3.core.history import History
from scmp3.core.settings import SettingsStore
from scmp3.core.sources import soundcloud
from scmp3.core.urls import UnsupportedInput

SHORT_LINK = "https://on.soundcloud.com/AbCdEfGh"
# How long the UI may take to load, and then the toast to show, on a busy machine.
UI_WAIT = 240.0
WAIT = 20.0
GIVE_UP_AFTER = 300.0


class SetBehindShortLink:
    """Pipeline steps where every link resolves to a set, which can't be saved yet."""

    def prepare(self, parsed: Any, *, settings: Any, job_id: str) -> Any:
        raise UnsupportedInput(pipeline.SETS_NOT_SUPPORTED)

    def fetch(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("nothing downloads")

    def finalize(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("nothing saves")

    def discard(self, prepared: Any) -> None:
        pass


def make_jobs(context: AppContext) -> JobManager:
    # No link resolves, so no cover is ever read.
    covers = CoverReader(soundcloud.make_ydl)
    return JobManager(context.settings, context.events, steps=SetBehindShortLink(), covers=covers)


def wait_for_text(window: webview.Window, wanted: str) -> str:
    deadline = time.monotonic() + WAIT
    text = ""
    while time.monotonic() < deadline:
        text = str(window.evaluate_js("document.body.innerText"))
        if wanted in text:
            return text
        time.sleep(0.1)
    return text


def drive(window: webview.Window, ui_ready: threading.Event, result: dict[str, Any]) -> None:
    start = time.monotonic()
    try:
        if not ui_ready.wait(UI_WAIT):
            result["error"] = f"the UI didn't load within {UI_WAIT:.0f} s"
            return
        result["ui_loaded_after"] = round(time.monotonic() - start, 1)
        wait_for_text(window, "Paste a SoundCloud")
        # Through the page's bridge, as the paste field adds links.
        window.evaluate_js(f"window.pywebview.api.add_inputs({json.dumps(SHORT_LINK)}); 0")
        result["text"] = wait_for_text(window, "wasn't added")
    except Exception as error:
        result["error"] = repr(error)
    finally:
        window.destroy()


def watch_for_the_ui(window: webview.Window, events: EventBus) -> threading.Event:
    """Load the page as the app does, retrying a failed first load, and say when the UI is up."""
    page_watch = main.PageWatch(events)
    ui_ready = threading.Event()

    def loaded(window: webview.Window) -> None:
        page_watch.loaded(window)
        if not page_watch.failed and main.shows_the_ui(window):
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
    context.settings.update({"first_run_done": True, "offer_clipboard_links": False})
    page = main.page_location(main.INDEX_PAGE, Path(get_app_root()))
    main.start_page_server(webview.http.start_global_server, page)
    window = webview.create_window("sc-mp3 test", page, js_api=Api(context), hidden=True)
    ui_ready = watch_for_the_ui(window, context.events)
    result: dict[str, Any] = {}
    threading.Thread(target=give_up, daemon=True).start()
    threading.Thread(target=drive, args=(window, ui_ready, result), daemon=True).start()
    webview.start(http_server=True, private_mode=True)
    context.events.close()
    context.jobs.shut_down()
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run())
