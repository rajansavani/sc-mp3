"""Tests for the queue's bridge calls: argument checks, answers, speed, and the codes they send."""

import importlib.util
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scmp3.app import job_editing
from scmp3.app.bridge import Api
from scmp3.app.bridge import files as files_module
from scmp3.app.bridge import jobs as bridge_jobs
from scmp3.app.context import AppContext
from scmp3.app.drops import SAME_NAME_DROP
from scmp3.app.events import EventBus
from scmp3.app.input_reasons import ALREADY_IN_QUEUE, REASON_CODES
from scmp3.app.job_covers import COVER_PROBLEMS
from scmp3.app.job_fields import NOT_A_TRACK, NOT_A_YEAR, TOTAL_BEFORE_TRACK
from scmp3.app.jobs import JobManager
from scmp3.app.main import WEB_DIR
from scmp3.app.workers import TaskPool
from scmp3.core import paths
from scmp3.core.errors import ErrorKind
from scmp3.core.history import History
from scmp3.core.models import CropBox, HistoryEntry, JobState
from scmp3.core.settings import SettingsStore


def load_job_fakes() -> ModuleType:
    """tests/fixtures/job_fakes.py, which pytest's importlib mode can't import by name."""
    name = "scmp3_test_fixture_job_fakes"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "fixtures" / "job_fakes.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


fakes = load_job_fakes()
TRACK = fakes.TRACK
# The longest a bridge call may take, since pywebview waits for it before the UI hears back.
CALL_BUDGET = 0.05


class Clipboard:
    """Stands in for the system clipboard, which no test reads."""

    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.reads = 0

    def __call__(self) -> str | None:
        self.reads += 1
        return self.text


def api_on(
    store: SettingsStore, events: Any, manager: JobManager, clipboard: Clipboard | None = None
) -> Api:
    """An Api on this job manager."""
    context = AppContext(
        settings=store,
        history=History(paths.data_dir() / "history.db"),
        events=events,
        engine_version=lambda: "2026.08.19",
        make_jobs=lambda context: manager,
        clipboard=clipboard or Clipboard(),
    )
    return Api(context)


@pytest.fixture
def harness(tmp_path: Path) -> Any:
    return fakes.make_harness(tmp_path / "library")


@pytest.fixture
def api(harness: Any) -> Api:
    return api_on(harness.store, harness.events, harness.manager)


def resolved(api: Api, harness: Any) -> str:
    (job_id,) = api.add_inputs(TRACK)["added"]
    harness.pools.run("resolve")
    return job_id


# Arguments


BAD_CALLS: list[tuple[str, tuple[Any, ...]]] = [
    ("add_inputs", (None,)),
    ("add_inputs", (["ok", 3],)),
    ("add_inputs", ("x" * 2_000_001,)),
    ("update_fields", (None, {"title": "x"})),
    ("update_fields", ("job", {})),
    ("update_fields", ("job", {"track_total": "3"})),
    ("update_fields", ("job", {"title": 3})),
    ("update_fields", ("job", ["title"])),
    ("update_fields", ("job", {"title": "x" * 2_001})),
    ("reset_field", ("job", "comment")),
    ("reset_field", (3, "title")),
    ("set_artists", ("job", "Kaia", [])),
    ("set_artists", ("job", ["Kaia"], None)),
    ("set_artists", ("job", ["Kaia", 3], [])),
    ("split_artist_names", (None,)),
    ("split_artist_names", ("x" * 20_001,)),
    ("set_artists", ("job", ["x" * 2_001], [])),
    ("set_artists", ("job", ["Kaia"], ["Lou V"] * 200)),
    ("set_artists", (None, ["Kaia"], [])),
    ("reset_artists", (3,)),
    ("toggle_change", ("job", "change", "yes")),
    ("toggle_change", ("job", None, True)),
    ("set_style", ("job", "loud")),
    ("set_artwork", ("job", "source")),
    ("set_artwork", ("", {"kind": "source"})),
    ("save", ("job",)),
    ("save", ([1],)),
    ("dismiss_cover_problem", (None,)),
    ("save", (["job"], "yes")),
    ("skip", (None,)),
    ("retry", ("x" * 65,)),
    ("retry", ("job", "yes")),
    ("cancel", (["job"],)),
    ("remove", ({},)),
    ("remove_jobs", ("job",)),
    ("remove_jobs", ([3],)),
    ("answer_collision", ("job", "rename")),
    ("answer_collision", ("job", "replace", "yes")),
    ("history", (3,)),
    ("history", ("", 0)),
    ("history", ("", 501)),
    ("history", ("", True)),
]


@pytest.mark.parametrize(("name", "args"), BAD_CALLS, ids=lambda value: str(value)[:30])
def test_arguments_the_ui_never_sends_are_answered_as_internal(
    api: Api, name: str, args: tuple[Any, ...], caplog: pytest.LogCaptureFixture
) -> None:
    assert getattr(api, name)(*args) == {"error": "internal"}
    assert f"The UI called {name} with arguments it can't use" in caplog.text


@pytest.mark.parametrize(
    ("artwork", "crop"),
    [
        ({"kind": "none"}, None),
        ({"kind": "url", "value": "http://example.com/a.jpg"}, None),
        ({"kind": "url", "value": "file:///C:/a.jpg"}, None),
        ({"kind": "url"}, None),
        ({"kind": "data_url", "value": "https://example.com/a.jpg"}, None),
        ({"kind": "file", "value": "cover.png"}, None),
        ({"kind": "source"}, {"left": 0, "top": 0, "width": 1}),
        ({"kind": "source"}, {"left": 0.5, "top": 0, "width": 0.6, "height": 0.5}),
        ({"kind": "source"}, {"left": 0, "top": 0, "width": 0, "height": 0.5}),
        ({"kind": "source"}, {"left": -0.1, "top": 0, "width": 0.5, "height": 0.5}),
        ({"kind": "source"}, {"left": True, "top": 0, "width": 0.5, "height": 0.5}),
        ({"kind": "source"}, {"left": "0", "top": 0, "width": 0.5, "height": 0.5}),
    ],
)
def test_a_cover_that_cant_be_used_is_refused(
    api: Api, harness: Any, artwork: dict[str, Any], crop: Any
) -> None:
    job_id = resolved(api, harness)
    assert api.set_artwork(job_id, artwork, crop) == {"error": "invalid_artwork"}


def test_a_cleanup_with_a_long_id_can_be_toggled(api: Api, harness: Any) -> None:
    # A change's id holds the text it acts on, so all-caps titles give long ones.
    title = "MIDNIGHT DRIVE ON THE COAST HIGHWAY WITH THE WINDOWS DOWN (EXTENDED)"
    harness.steps.sources[TRACK] = fakes.source_for(TRACK, title)
    job_id = resolved(api, harness)
    (caps,) = [c for c in harness.job(job_id)["suggestion"]["changes"] if c["kind"] == "fix_caps"]
    assert len(caps["id"]) > 64
    assert api.toggle_change(job_id, caps["id"], False) == {}
    assert harness.job(job_id)["tags"]["title"].startswith("MIDNIGHT DRIVE")


def test_a_cleanup_the_job_doesnt_have_is_refused(api: Api, harness: Any) -> None:
    job_id = resolved(api, harness)
    assert api.toggle_change(job_id, "fix_caps:" + "X" * 500, False) == {"error": "internal"}
    assert api.toggle_change(job_id, "", False) == {"error": "internal"}


def test_an_artist_list_is_passed_on_without_the_spaces_around_names(
    api: Api, harness: Any
) -> None:
    job_id = resolved(api, harness)
    answer = api.set_artists(job_id, [" Mara ", "  "], ["Kaia", "Lou V\u200b"])
    assert answer == {"artists": {"main": ["Mara"], "featured": ["Kaia", "Lou V"]}}
    job = harness.job(job_id)
    assert job["suggestion"]["main_artists"] == ["Mara"]
    assert job["suggestion"]["featured"] == ["Kaia", "Lou V"]
    assert api.reset_artists(job_id) == {}
    assert harness.job(job_id)["artists_edited"] is False


def test_pasted_names_are_split_in_python(api: Api) -> None:
    answer = api.split_artist_names("gunk, zayok, edyn\nTyler, The Creator")
    assert answer == {"names": ["gunk", "zayok", "edyn", "Tyler, The Creator"]}
    assert len(api.split_artist_names(", ".join(f"Name {n}" for n in range(300)))["names"]) == 200


def test_a_crop_is_passed_on_as_fractions(api: Api, harness: Any) -> None:
    job_id = resolved(api, harness)
    crop = {"left": 0.25, "top": 0, "width": 0.5, "height": 1}
    assert api.set_artwork(job_id, {"kind": "source", "value": "ignored"}, crop) == {}
    artwork = harness.job(job_id)["artwork"]
    assert artwork == {"kind": "source", "value": None, "crop": CropBox(0.25, 0, 0.5, 1).to_dict()}


def test_dropped_paths_are_read_one_per_line(api: Api, harness: Any) -> None:
    answer = api.add_inputs([TRACK, fakes.OTHER])
    assert len(answer["added"]) == 2


# Answers


def test_the_snapshot_holds_the_jobs_as_the_ui_gets_them(api: Api, harness: Any) -> None:
    job_id = resolved(api, harness)
    (job,) = api.snapshot()["jobs"]
    assert job["id"] == job_id
    assert job["tags"]["title"] == "Midnight Drive"


def test_every_job_call_reaches_the_job_manager(api: Api, harness: Any) -> None:
    job_id = resolved(api, harness)
    assert api.update_fields(job_id, {"title": "New"}) == {}
    assert api.reset_field(job_id, "title") == {}
    change = harness.job(job_id)["suggestion"]["changes"][0]
    assert api.toggle_change(job_id, change["id"], False) == {}
    assert api.set_style(job_id, "all_in_title") == {}
    assert api.set_artwork(job_id, {"kind": "url", "value": "https://example.com/a.jpg"}) == {}
    assert api.save([job_id]) == {}
    assert api.cancel(job_id) == {}
    assert api.skip(job_id) == {}
    assert api.retry(job_id) == {}
    assert api.answer_collision(job_id, "replace") == {}
    assert api.remove(job_id) == {}
    assert api.snapshot()["jobs"] == []


def test_a_refused_field_comes_back_with_its_reason(api: Api, harness: Any) -> None:
    job_id = resolved(api, harness)
    assert api.update_fields(job_id, {"year": "soon"}) == {
        "error": "invalid_fields",
        "fields": {"year": {"reason": "NOT_A_YEAR"}},
    }


def test_creating_history_db_is_paid_before_the_first_history_call(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Creating the file took over 100 ms on a Windows CI runner, against about 1 ms to open it.
    open_once = History._open_once

    def slow_when_creating(self: History) -> Any:
        if not self.path.exists():
            time.sleep(0.2)
        return open_once(self)

    monkeypatch.setattr(History, "_open_once", slow_when_creating)
    api._context.open_stores()
    assert timed(lambda: api.history("drive", 10)) < CALL_BUDGET


def test_history_lists_the_latest_saves_or_the_ones_that_match(api: Api) -> None:
    history = api._context.history
    for title in ("Midnight Drive", "Night Bus"):
        history.add(
            HistoryEntry(
                source_id=None,
                url_norm=None,
                saved_path=Path(f"Kaia - {title}.mp3"),
                title=title,
                artist="Kaia",
                album=title,
                format="mp3",
            )
        )
    latest = api.history()["entries"]
    assert [entry["title"] for entry in latest] == ["Night Bus", "Midnight Drive"]
    assert [entry["title"] for entry in api.history("midnight", 5)["entries"]] == ["Midnight Drive"]


@pytest.mark.parametrize(
    "call",
    [
        lambda api: api.set_album_mode("job", "both"),
        lambda api: api.set_album_mode(None, "album"),
        lambda api: api.set_included("job", "track", "yes"),
        lambda api: api.set_included("job", "x" * 65, True),
    ],
)
def test_the_sets_calls_refuse_arguments_the_ui_never_sends(api: Api, call: Any) -> None:
    assert call(api) == {"error": "internal"}


def test_the_sets_calls_answer_for_a_job_that_isnt_queued(api: Api) -> None:
    assert api.set_album_mode("job", "album") == {"error": "unknown_job"}
    assert api.set_included("job", "track", False) == {"error": "unknown_job"}


# The clipboard offer


@pytest.fixture
def clipboard() -> Clipboard:
    return Clipboard()


@pytest.fixture
def offering_api(harness: Any, clipboard: Clipboard) -> Api:
    return api_on(harness.store, harness.events, harness.manager, clipboard)


def test_a_track_link_on_the_clipboard_is_offered(offering_api: Api, clipboard: Clipboard) -> None:
    clipboard.text = f"check this out {TRACK}?si=abc"
    assert offering_api.clipboard_link() == {"link": TRACK}


def test_a_link_already_queued_isnt_offered(
    offering_api: Api, clipboard: Clipboard, harness: Any
) -> None:
    clipboard.text = TRACK
    offering_api.add_inputs(TRACK)
    assert offering_api.clipboard_link() == {"link": None}
    # Still not once it's resolved, when the queue knows it by the source's own link too.
    harness.pools.run("resolve")
    assert offering_api.clipboard_link() == {"link": None}


@pytest.mark.parametrize("text", [None, "", "no links here", "https://soundcloud.com/kaia"])
def test_a_clipboard_without_a_track_link_offers_nothing(
    offering_api: Api, clipboard: Clipboard, text: str | None
) -> None:
    clipboard.text = text
    assert offering_api.clipboard_link() == {"link": None}


def test_the_clipboard_isnt_read_with_the_offer_turned_off(
    offering_api: Api, clipboard: Clipboard, harness: Any
) -> None:
    harness.store.update({"offer_clipboard_links": False})
    clipboard.text = TRACK
    assert offering_api.clipboard_link() == {"link": None}
    assert clipboard.reads == 0


# Diagnostics


def test_diagnostics_give_the_text_for_a_bug_report(api: Api, harness: Any) -> None:
    text = api.diagnostics()["text"]
    assert text.startswith("sc-mp3 diagnostics\nApp: sc-mp3 ")
    assert "Download engine: yt-dlp 2026.08.19" in text
    library = Path(harness.store.load().library_dir)
    assert f"  library_dir: {library.name}" in text
    assert str(library.parent) not in text


# Speed


def timed(call: Callable[[], Any]) -> float:
    start = time.perf_counter()
    call()
    return time.perf_counter() - start


def test_every_call_returns_at_once_while_the_pipeline_is_slow(tmp_path: Path) -> None:
    store = SettingsStore(paths.config_dir() / "settings.json")
    store.update({"library_dir": str(tmp_path / "library")})
    steps = fakes.FakeSteps(delay=0.5)
    events = fakes.RecordingBus()
    manager = JobManager(store, events, steps=steps, covers=fakes.FakeCovers(), make_pool=TaskPool)
    api = api_on(store, events, manager, Clipboard(f"{TRACK} and {fakes.OTHER}"))
    # As the app does before its window loads: history.db is created and the pools' threads
    # are started, so no call pays for either.
    api._context.open_stores()
    manager.start_up()
    durations: dict[str, float] = {}

    def measure(name: str, call: Callable[[], Any]) -> Any:
        start = time.perf_counter()
        answer = call()
        durations[name] = max(durations.get(name, 0.0), time.perf_counter() - start)
        return answer

    first, second = measure("add_inputs", lambda: api.add_inputs(f"{TRACK}\n{fakes.OTHER}"))[
        "added"
    ]
    measure("snapshot", api.snapshot)
    # Resolving takes half a second, so wait for both to reach review.
    deadline = time.monotonic() + 10
    while any(job["state"] != "needs_review" for job in manager.views()):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    # Downloads take half a second too, and run while the calls below are made.
    change = manager.views()[0]["suggestion"]["changes"][0]["id"]
    measure("update_fields", lambda: api.update_fields(first, {"title": "New"}))
    measure("reset_field", lambda: api.reset_field(first, "title"))
    measure("toggle_change", lambda: api.toggle_change(first, change, False))
    measure("set_artists", lambda: api.set_artists(first, ["Lou V"], ["Kaia"]))
    measure("reset_artists", lambda: api.reset_artists(first))
    measure("split_artist_names", lambda: api.split_artist_names("gunk, zayok"))
    measure("set_style", lambda: api.set_style(first, "feat_in_title"))
    link = {"kind": "url", "value": "https://example.com/cover.jpg"}
    measure("set_artwork", lambda: api.set_artwork(first, link))
    measure("dismiss_cover_problem", lambda: api.dismiss_cover_problem(first))
    measure("save", lambda: api.save([first, second]))
    measure("cancel", lambda: api.cancel(second))
    measure("history", lambda: api.history("drive", 10))
    measure("clipboard_link", api.clipboard_link)
    measure("diagnostics", api.diagnostics)
    measure("snapshot", api.snapshot)
    # The first job's save is now finalizing for half a second.
    deadline = time.monotonic() + 10
    while ("finalize", first) not in steps.calls:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    measure("skip", lambda: api.skip(first))
    measure("retry", lambda: api.retry(first))
    measure("set_album_mode", lambda: api.set_album_mode(first, "album"))
    measure("set_included", lambda: api.set_included(first, second, False))
    measure("answer_collision", lambda: api.answer_collision(first, "replace"))
    measure("remove", lambda: api.remove(second))
    measure("remove_jobs", lambda: api.remove_jobs([second, "gone"]))
    assert manager.wait_until_idle(10)
    manager.shut_down()
    slow = {
        name: round(seconds, 3) for name, seconds in durations.items() if seconds >= CALL_BUDGET
    }
    assert slow == {}
    job_calls = set(bridge_jobs.JobsApi.__dict__)
    assert set(durations) == {name for name in job_calls if not name.startswith("_")} | {"snapshot"}


FULL_QUEUE = 2_000  # the paste cap
# With a full queue, most calls must fit the budget, and none may take more than this. A pause for
# garbage collection or another busy process can stretch any one call, so the slowest isn't held
# to the budget itself.
SLOWEST_CALL = 0.25


class SentScripts:
    """A window that keeps the scripts the event bus sends it."""

    def __init__(self) -> None:
        self.scripts: list[str] = []
        self._lock = threading.Lock()

    def evaluate_js(self, script: str) -> None:
        with self._lock:
            self.scripts.append(script)

    def mentions(self, text: str) -> int:
        with self._lock:
            return sum(script.count(text) for script in self.scripts)


def calls_during(work: Callable[[], Any], call: Callable[[], Any]) -> list[float]:
    """How long each `call` took while `work` ran on another thread."""
    worker = threading.Thread(target=work)
    worker.start()
    durations = []
    while worker.is_alive() or not durations:
        durations.append(timed(call))
    worker.join(30)
    return durations


def test_every_call_stays_quick_with_a_full_queue(tmp_path: Path) -> None:
    store = SettingsStore(paths.config_dir() / "settings.json")
    store.update({"library_dir": str(tmp_path / "library")})
    links = [f"https://soundcloud.com/kaia/track-{number}" for number in range(FULL_QUEUE)]
    steps = fakes.FakeSteps()
    for number, link in enumerate(links):
        steps.sources[link] = fakes.source_for(link, f"Track {number} [FREE DL]")
    covers = fakes.FakeCovers()
    covers.source_works = False
    pools = fakes.ManualPools()
    bus = EventBus()
    manager = JobManager(store, bus, steps=steps, covers=covers, make_pool=pools)
    clipboard = Clipboard("\n".join([*links[:500], "https://soundcloud.com/kaia/not-queued"]))
    api = api_on(store, bus, manager, clipboard)
    api._context.open_stores()
    samples: list[tuple[str, float]] = []

    def measure(name: str, call: Callable[[], Any]) -> Any:
        start = time.perf_counter()
        answer = call()
        samples.append((name, time.perf_counter() - start))
        return answer

    # The paste takes the lock a few jobs at a time, so a call made meanwhile never waits long.
    pasted: dict[str, Any] = {}
    waits = calls_during(
        lambda: pasted.update(api.add_inputs("\n".join(links))), lambda: manager.is_queued(TRACK)
    )
    samples += [("a call during the paste", seconds) for seconds in waits]
    ids = pasted["added"]
    assert len(ids) == FULL_QUEUE
    pools.run("resolve")
    pools.run("misc")
    # The bus builds every job's dict on its own thread and sends it, as it does in the app.
    window = SentScripts()
    bus.attach(window)
    deadline = time.monotonic() + 30
    while window.mentions('"state": "needs_review"') < FULL_QUEUE:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    first, second, third = ids[:3]
    change = manager.views()[0]["suggestion"]["changes"][0]["id"]
    measure("snapshot", api.snapshot)
    measure("add_inputs", lambda: api.add_inputs(f"{TRACK}\n{fakes.OTHER}"))
    measure("update_fields", lambda: api.update_fields(first, {"title": "New"}))
    measure("reset_field", lambda: api.reset_field(first, "title"))
    measure("toggle_change", lambda: api.toggle_change(first, change, False))
    measure("set_artists", lambda: api.set_artists(first, ["Lou V"], ["Kaia"]))
    measure("reset_artists", lambda: api.reset_artists(first))
    measure("split_artist_names", lambda: api.split_artist_names("gunk, zayok"))
    measure("set_style", lambda: api.set_style(first, "feat_in_title"))
    link = {"kind": "url", "value": "https://example.com/cover.jpg"}
    measure("set_artwork", lambda: api.set_artwork(first, link))
    measure("dismiss_cover_problem", lambda: api.dismiss_cover_problem(first))
    measure("history", lambda: api.history("drive", 10))
    assert measure("clipboard_link", api.clipboard_link) == {
        "link": "https://soundcloud.com/kaia/not-queued"
    }
    measure("diagnostics", api.diagnostics)
    # Save all, at the paste cap.
    measure("save", lambda: api.save(ids))
    measure("cancel", lambda: api.cancel(second))
    measure("skip", lambda: api.skip(first))
    measure("retry", lambda: api.retry(first))
    measure("answer_collision", lambda: api.answer_collision(first, "replace"))
    measure("remove", lambda: api.remove(second))
    # Clear finished, for half the queue in one call.
    measure("remove_jobs", lambda: api.remove_jobs(ids[FULL_QUEUE // 2 :]))
    # A settings change rebuilds every job's suggestion on a worker, outside the lock.
    saved = store.update({"title_style": "feat_in_title"})
    measure("a settings change", lambda: manager.settings_changed(saved))
    waits = calls_during(
        lambda: pools.run("misc"), lambda: api.update_fields(third, {"title": "Typed"})
    )
    samples += [("a call during a settings refresh", seconds) for seconds in waits]
    bus.close()
    manager.shut_down()
    times = sorted(seconds for _, seconds in samples)
    slowest = max(samples, key=lambda sample: sample[1])
    assert times[int(len(times) * 0.95)] < CALL_BUDGET, sorted(samples, key=lambda s: -s[1])[:10]
    assert slowest[1] < SLOWEST_CALL, slowest


# Codes the UI has text for


def keys_in_block(text: str, name: str) -> set[str]:
    """The keys one level inside a `name: {` or `const name = {` block, on one line or below."""
    header = rf"^( *)(?:export )?(?:const )?{re.escape(name)}(?:: | = )\{{(.*)$"
    start = re.search(header, text, re.MULTILINE)
    assert start, f"no {name} block"
    indent, rest = start.groups()
    if "}" in rest:
        return set(re.findall(r"(\w+):", rest))
    end = text.index(f"\n{indent}}}", start.end())
    block = text[start.end() : end]
    return set(re.findall(rf"^{indent}  (\w+):", block, re.MULTILINE))


def web_file(*parts: str) -> str:
    return WEB_DIR.joinpath(*parts).read_text(encoding="utf-8")


def test_every_error_kind_and_job_state_has_text_in_the_ui() -> None:
    errors = web_file("strings", "errors.js")
    assert keys_in_block(errors, "errors") >= {kind.value for kind in ErrorKind}
    # job-view.js turns each state into its status line from strings/main.js.
    assert keys_in_block(web_file("components", "job-view.js"), "STATUS") == {
        state.value for state in JobState
    }


def test_every_rejection_code_has_text_in_the_ui() -> None:
    texts = keys_in_block(web_file("strings", "main.js"), "rejectReasons")
    assert {*REASON_CODES.values(), ALREADY_IN_QUEUE, SAME_NAME_DROP} <= texts


def test_every_field_refusal_has_text_in_the_ui() -> None:
    texts = keys_in_block(web_file("strings", "main.js"), "fieldErrors")
    assert {NOT_A_YEAR, NOT_A_TRACK, TOTAL_BEFORE_TRACK} <= texts


def test_every_cover_problem_has_text_in_the_ui() -> None:
    assert set(COVER_PROBLEMS) <= keys_in_block(web_file("strings", "main.js"), "coverProblems")


def test_every_error_a_call_answers_with_has_text_in_the_ui() -> None:
    codes = {
        job_editing.UNKNOWN_JOB,
        job_editing.NOT_EDITABLE,
        job_editing.INTERNAL,
        bridge_jobs.INVALID_ARTWORK,
        files_module.FILE_MISSING,
        files_module.SPOTIFY_MISSING,
        files_module.SPOTIFY_FOLDER_MISSING,
        files_module.INTERNAL,
        "not_implemented",
    }
    assert codes <= keys_in_block(web_file("strings", "shell.js"), "errors")


def test_the_lock_is_free_while_a_step_runs(harness: Any) -> None:
    # Steps run outside the lock, so a call on another thread gets it at once.
    job_id = harness.add_and_resolve()
    times: list[float] = []

    def read() -> None:
        times.append(timed(harness.manager.views))

    reader = threading.Thread(target=read)
    harness.steps.during_fetch = reader.start
    harness.pools.run("download")
    reader.join(5)
    assert times and times[0] < CALL_BUDGET
    assert harness.job(job_id)["staged_audio"]


def test_every_event_the_job_manager_sends_has_a_handler_in_the_ui() -> None:
    modules = Path(job_editing.__file__).parent.glob("job*.py")
    code = "\n".join(path.read_text(encoding="utf-8") for path in modules)
    sent = set(re.findall(r'"type": "(\w+)"', code))
    assert sent == {"job", "progress", "job_removed", "rejected"}
    assert sent <= keys_in_block(web_file("store.js"), "EVENT_HANDLERS")


def test_a_rejected_event_is_told_like_a_rejection_when_adding() -> None:
    store = web_file("store.js")
    handler = re.search(r"^  rejected: \(current, event\) => \{\n(.*?)\n  \},", store, re.M | re.S)
    assert handler, "no rejected handler"
    assert "reportRejected(event.rejected)" in handler.group(1)
    # add_inputs' answers go through the same function, so both read the same.
    assert "reportRejected(result.rejected" in web_file("components", "inputs.js")
