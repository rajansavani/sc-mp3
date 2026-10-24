"""The update checks: once a day in the background, and when Settings asks for one.

One looks up sc-mp3's latest release on GitHub, and the other installs a newer download engine.
Settings can turn each one off, and the app makes no other request of its own to GitHub.
"""

import itertools
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from scmp3 import __version__
from scmp3.core import github, net, paths
from scmp3.core.engine_updates import EngineUpdate, EngineUpdates
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.fetch import DownloadProgress, os_reason
from scmp3.core.github import Clock
from scmp3.core.net import Opener
from scmp3.core.releases import is_newer
from scmp3.core.settings import SettingsStore

# sc-mp3's own releases, where the app update check looks.
APP_REPO = "rajansavani/sc-mp3"

DAY = 24 * 60 * 60.0
# After a check that didn't finish, such as one made offline, the next try is an hour later.
RETRY_AFTER = 60 * 60.0
# The first check waits this long after start-up, so the window and the queue come first.
FIRST_CHECK_DELAY = 15.0
STATE_FILE = "update-checks.json"

# The checks, by the name the state file keeps each one's last finished check under.
CheckKind = Literal["app", "engine"]
# unknown: never checked. current: this is the latest release. available: a newer one is out.
AppUpdateState = Literal["unknown", "current", "available"]
# What Settings can start: Check now, or Update now.
TaskName = Literal["check", "update"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppUpdate:
    """What's known about sc-mp3's latest release, with its page when it's newer than this one."""

    state: AppUpdateState
    version: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class CheckResults:
    """What Check now found. A part is the error it ended with, or None when it's turned off."""

    app: AppUpdate | ScmpError
    engine: EngineUpdate | ScmpError | None


@dataclass(frozen=True)
class TaskOutcome:
    """What the last Check now or Update now found. `id` tells one outcome from the next."""

    id: int
    task: TaskName
    app: AppUpdate | ScmpError | None
    engine: EngineUpdate | ScmpError | None


@dataclass(frozen=True)
class TaskState:
    """The Settings task that's running, its download's progress, and the last one's outcome."""

    running: TaskName | None = None
    pct: float | None = None
    last: TaskOutcome | None = None


class UpdateChecks:
    """Runs the update checks, remembers when each one last finished, and reports changes."""

    def __init__(
        self,
        settings: SettingsStore,
        engine: EngineUpdates,
        *,
        app_version: str = __version__,
        opener: Opener = net.open_url,
        clock: Clock = time.time,
        state_path: Path | None = None,
        on_change: Callable[[], None] = lambda: None,
        first_delay: float = FIRST_CHECK_DELAY,
    ) -> None:
        self.engine = engine
        self._settings = settings
        self._app_version = app_version
        self._opener = opener
        self._clock = clock
        self._state_path = state_path or paths.cache_dir() / STATE_FILE
        # Called after anything that can change what Settings shows.
        self._on_change = on_change
        self._first_delay = first_delay
        # One check at a time, so two never ask GitHub the same question.
        self._checking = threading.Lock()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        # The Settings task, which the UI follows through components events.
        self._task_lock = threading.Lock()
        self._task = TaskState()
        self._outcome_ids = itertools.count(1)

    def start(self) -> None:
        """Start the daily checks on a thread of their own."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_daily, name="scmp3-update-checks", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the daily checks. A check that's running finishes on its own."""
        self._stopped.set()

    def _run_daily(self) -> None:
        delay = self._first_delay
        while not self._stopped.wait(delay):
            delay = self.run_due()

    def run_due(self) -> float:
        """Run each check that's turned on and due, and return the seconds until one is due."""
        settings = self._settings.load()
        waits = [DAY]
        if settings.check_app_updates:
            waits.append(self._run_if_due("app", self.check_app))
        if settings.check_engine_updates:
            waits.append(self._run_if_due("engine", self.check_engine))
        return min(waits)

    def _run_if_due(self, kind: CheckKind, check: Callable[[], object]) -> float:
        now = self._clock()
        last = self._last_checked(kind)
        # A last check in the future means the clock was turned back, so it counts as due.
        if last is not None and now - DAY < last <= now:
            return last + DAY - now
        try:
            check()
        except ScmpError as error:
            log.info("The daily %s update check didn't finish: %s", kind, error)
            return RETRY_AFTER
        except Exception:
            log.exception("The daily %s update check failed unexpectedly", kind)
            return RETRY_AFTER
        return DAY

    def task_state(self) -> TaskState:
        """The Settings task that's running, and the last one's outcome."""
        with self._task_lock:
            return self._task

    def start_task(self, task: TaskName) -> None:
        """Run Check now or Update now on a thread of its own, unless one is running already.

        Its progress and its outcome reach the UI as changes, through `on_change`.
        """
        with self._task_lock:
            if self._task.running is not None:
                return
            self._task = TaskState(running=task, last=self._task.last)
        self._on_change()
        thread = threading.Thread(
            target=self._run_task, args=(task,), name="scmp3-update-task", daemon=True
        )
        thread.start()

    def _run_task(self, task: TaskName) -> None:
        app: AppUpdate | ScmpError | None = None
        engine: EngineUpdate | ScmpError | None
        try:
            if task == "check":
                results = self.check_now(progress=self._task_progress)
                app, engine = results.app, results.engine
            else:
                engine = _attempt(self._update_now)
        except Exception:
            log.exception("The %s update task failed unexpectedly", task)
            engine = ScmpError(ErrorKind.internal, f"the {task} update task failed")
            app = engine if task == "check" else None
        outcome = TaskOutcome(next(self._outcome_ids), task, app, engine)
        with self._task_lock:
            self._task = TaskState(last=outcome)
        self._on_change()

    def _update_now(self) -> EngineUpdate:
        """Update now installs a version that was set aside before, too."""
        return self.check_engine(again=True, progress=self._task_progress)

    def _task_progress(self, done: int, total: int | None) -> None:
        pct = round(100 * done / total, 1) if total else None
        with self._task_lock:
            self._task = replace(self._task, pct=pct)
        self._on_change()

    def check_now(self, *, progress: DownloadProgress | None = None) -> CheckResults:
        """Run both checks now: the app's always, and the engine's when engine checks are on."""
        app = _attempt(self.check_app)
        engine = None
        if self._settings.load().check_engine_updates:
            engine = _attempt(lambda: self.check_engine(progress=progress))
        return CheckResults(app=app, engine=engine)

    def check_app(self) -> AppUpdate:
        """Ask GitHub for sc-mp3's latest release, and say whether it's newer."""
        with self._checking:
            try:
                github.github_latest(APP_REPO, opener=self._opener, clock=self._clock)
            except ScmpError as error:
                # GitHub answers 404 while the repo has no release yet.
                if error.kind is not ErrorKind.unavailable:
                    raise
                log.info("sc-mp3 has no release on GitHub yet")
            self._record_checked("app")
        self._on_change()
        return self.app_update()

    def check_engine(
        self, *, again: bool = False, progress: DownloadProgress | None = None
    ) -> EngineUpdate:
        """Install the newest engine on the channel in Settings, when it's newer than ours.

        `again` installs a version that was marked bad or reverted, as Update now does.
        """
        channel = self._settings.load().engine_channel
        with self._checking:
            try:
                found = self.engine.update(channel, again=again, progress=progress)
            finally:
                # A failed install may have changed what's installed too.
                self._on_change()
            self._record_checked("engine")
        return found

    def revert_engine(self) -> None:
        """Set every engine update aside, so the next start uses the built-in copy."""
        with self._checking:
            self.engine.revert()
        self._on_change()

    def app_update(self) -> AppUpdate:
        """What's known about sc-mp3's latest release, from GitHub's last answer."""
        release = github.cached_release(APP_REPO)
        if release is None:
            checked = self._last_checked("app") is not None
            return AppUpdate("current" if checked else "unknown")
        if is_newer(release.version, self._app_version):
            return AppUpdate("available", release.version, release.html_url)
        return AppUpdate("current", release.version)

    def _last_checked(self, kind: CheckKind) -> float | None:
        value = self._read_state().get(kind)
        return float(value) if isinstance(value, int | float) else None

    def _record_checked(self, kind: CheckKind) -> None:
        """Remember when a check finished. Failing to only means an early check next time."""
        state = {**self._read_state(), kind: self._clock()}
        try:
            paths.write_text_atomically(self._state_path, json.dumps(state))
        except ScmpError as error:
            log.warning("Couldn't remember when the %s update check ran: %s", kind, error)

    def _read_state(self) -> dict[str, object]:
        try:
            data = json.loads(self._state_path.read_bytes())
        except FileNotFoundError:
            return {}
        except OSError as error:
            log.warning("The update check times can't be read: %s", os_reason(error))
            return {}
        except ValueError:
            log.warning("The update check times are damaged, so they're ignored")
            return {}
        return data if isinstance(data, dict) else {}


def _attempt[T](check: Callable[[], T]) -> T | ScmpError:
    """A check's answer, or the error it ended with."""
    try:
        return check()
    except ScmpError as error:
        log.info("An update check didn't finish: %s", error)
        return error
