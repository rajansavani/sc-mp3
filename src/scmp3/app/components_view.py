"""The components object the UI gets in the snapshot and in every components event.

It holds the versions, what an engine update or a restart would change, the app's latest
release, and YouTube support's status. Python sends codes and versions; the UI has the text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from scmp3 import __version__
from scmp3.app.update_checks import AppUpdate, TaskState
from scmp3.core.components import DENO, Components
from scmp3.core.engine_updates import EngineUpdate
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.releases import engine_channel_of, version_key

if TYPE_CHECKING:
    from scmp3.app.context import AppContext

# What YouTube support is doing. A failure only arrives as a components event, so the snapshot
# after one says missing.
YoutubeState = Literal["missing", "installing", "installed", "failed"]
# Where the engine in use came from: the copy inside the app, or a downloaded update.
EngineSource = Literal["built_in", "updated"]


def youtube_support(
    state: YoutubeState,
    *,
    version: str | None = None,
    pct: float | None = None,
    error: ErrorKind | None = None,
) -> dict[str, Any]:
    """YouTube support's status for the UI, with every key always there."""
    return {
        "state": state,
        "version": version,
        "pct": pct,
        "error": error.value if error else None,
    }


def youtube_status(components: Components) -> dict[str, Any]:
    """YouTube support's status as the components folder shows it."""
    installed = components.active(DENO)
    if installed is not None:
        return youtube_support("installed", version=installed.version)
    # So a page reloaded mid-install doesn't offer the install again.
    if components.installing(DENO):
        return youtube_support("installing")
    return youtube_support("missing")


def components_view(context: AppContext, youtube: dict[str, Any] | None = None) -> dict[str, Any]:
    """The whole components object, as the snapshot and every components event carry it.

    `youtube` replaces YouTube support's status while an install runs, which the folder can't show.
    """
    engine = engine_view(context)
    running = engine["version"]
    return {
        "app_version": __version__,
        "engine_version": running,
        "engine": engine,
        "app_update": app_update_view(context),
        "updates": task_view(context.updates.task_state()),
        "youtube_support": youtube or youtube_status(context.components),
    }


def engine_view(context: AppContext) -> dict[str, Any]:
    """The engine in use, the one a restart would switch to, and an update that didn't load."""
    started = context.engine_start
    updates = context.updates.engine
    built_in = context.engine_version()
    source: EngineSource = "updated" if started.update else "built_in"
    version = started.update or built_in
    upcoming = updates.next_update()
    next_source: EngineSource = "updated" if upcoming else "built_in"
    next_version = upcoming.version if upcoming else built_in
    same = next_source == source and _same_version(next_version, version)
    failed = updates.failed_update()
    running_update = updates.installed_version(started.update) if started.update else None
    return {
        "version": version,
        "source": source,
        # The channel the running update came from. The built-in copy has none.
        "channel": engine_channel_of(running_update.source_url) if running_update else None,
        "built_in_version": built_in,
        "restart_to": None if same else {"version": next_version, "source": next_source},
        # Until a newer update installs or Revert sets it aside.
        "failed_version": failed.version if failed else None,
        # Only at the start where it happened, so the UI tells about it once.
        "failed_now": started.failed,
    }


def app_update_view(context: AppContext) -> dict[str, Any]:
    """What's known about sc-mp3's latest release, and its page when it's newer."""
    update = context.updates.app_update()
    return {"state": update.state, "version": update.version, "url": update.url}


def task_view(state: TaskState) -> dict[str, Any]:
    """Check now or Update now: the one running, its download's progress, the last outcome."""
    last = state.last
    outcome = None
    if last is not None:
        outcome = {
            "id": last.id,
            "task": last.task,
            "app": None if last.app is None else app_answer(last.app),
            "engine": None if last.engine is None else engine_answer(last.engine),
        }
    return {"running": state.running, "pct": state.pct, "last": outcome}


def app_answer(found: AppUpdate | ScmpError) -> dict[str, Any]:
    if isinstance(found, ScmpError):
        return {"error": found.kind.value}
    return {"state": found.state, "version": found.version, "url": found.url}


def engine_answer(found: EngineUpdate | ScmpError) -> dict[str, Any]:
    if isinstance(found, ScmpError):
        return {"error": found.kind.value}
    return {"version": found.version, "installed": found.installed, "refused": found.refused}


def _same_version(first: str | None, second: str | None) -> bool:
    """Whether two spellings name one version, as 2026.8.19 and 2026.08.19 do."""
    if first is None or second is None:
        return first == second
    return version_key(first) == version_key(second)
