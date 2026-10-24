"""Which yt-dlp this process uses, settled at start-up before anything imports it.

The newest good engine update goes first on sys.path and is imported here. One that fails before
its code runs gives way to an older one or the built-in copy; one that fails after needs a new
process (TECH_SPEC §14). overlay.py has the mechanics.
"""

import logging
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scmp3.core import overlay
from scmp3.core.components import Components
from scmp3.core.engine_updates import EngineUpdates, built_in_version
from scmp3.core.errors import ScmpError
from scmp3.core.manifests import Manifest

# Set for a process started again after an update failed to load: the versions it must skip,
# separated by commas. Each restart adds one, so restarting can't go on forever.
SKIP_ENV = "SCMP3_SKIP_ENGINE_UPDATES"
# And why each one failed, since the process it failed in had no log yet.
FAILURE_ENV = "SCMP3_ENGINE_FAILURE"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EngineStart:
    """How this process got its yt-dlp, settled at start-up before anything imports it."""

    # The version of the update in use, or None for the built-in copy.
    update: str | None = None
    # An update that didn't load at this start, so an older copy runs instead.
    failed: str | None = None
    # Why each update that didn't load failed, as "<version>: <reason>" parts, for the log.
    failure: str | None = None
    # It failed after its code ran, so only a new process can leave it out.
    restart: bool = False


def start_engine(
    components: Components | None = None, *, environ: Mapping[str, str] = os.environ
) -> EngineStart:
    """Import the newest good engine update, before anything else can import yt-dlp.

    With no update that loads, nothing is imported here, and the built-in copy loads later.
    """
    overlay.turn_off_plugins()
    skipped = skipped_updates(environ)
    failed = skipped[-1] if skipped else None
    failure = (environ.get(FAILURE_ENV) or None) if skipped else None
    if overlay.engine_imported():
        return EngineStart(failed=failed, failure=failure)
    components = components or Components()
    updates = EngineUpdates(components, built_in=built_in_version())
    while (update := updates.next_update(skip=skipped)) is not None:
        zip_path = updates.zip_path(update)
        problem = overlay.put_first(zip_path)
        if problem is None:
            return _import_update(components, update, zip_path, failed, failure)
        # None of its code ran, so an older update can still be tried in this process.
        failed = update.version
        failure = _with_reason(failure, update.version, _set_aside(components, update, problem))
        skipped = (*skipped, update.version)
    return EngineStart(failed=failed, failure=failure)


def _import_update(
    components: Components,
    update: Manifest,
    zip_path: Path,
    failed: str | None,
    failure: str | None,
) -> EngineStart:
    """Import an update that's first on sys.path. A failure means starting a new process."""
    try:
        version = overlay.import_engine(zip_path)
    except Exception as error:
        reason = _set_aside(components, update, overlay.describe(error, zip_path))
        failure = _with_reason(failure, update.version, reason)
        return EngineStart(failed=update.version, failure=failure, restart=True)
    return EngineStart(update=version, failed=failed, failure=failure)


def _set_aside(components: Components, update: Manifest, reason: str) -> str:
    """Mark an update bad, and return the reason, saying so when marking it failed too."""
    try:
        components.set_status(update, "bad")
    except ScmpError as error:
        return f"{reason}, and it couldn't be marked bad: {error}"
    return reason


def _with_reason(failure: str | None, version: str, reason: str) -> str:
    """The reasons so far, plus why this version failed."""
    part = f"{version}: {reason}"
    return f"{failure}; {part}" if failure else part


def skipped_updates(environ: Mapping[str, str]) -> tuple[str, ...]:
    """The update versions an earlier process of this run found broken."""
    return tuple(version for version in environ.get(SKIP_ENV, "").split(",") if version)


def restart_app(
    args: Sequence[str],
    started: EngineStart,
    *,
    environ: Mapping[str, str] = os.environ,
    run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> int:
    """Start sc-mp3 again without the update that failed, wait for it, and return its exit code."""
    skipped = [*skipped_updates(environ), started.failed or ""]
    env = {**environ, SKIP_ENV: ",".join(skipped), FAILURE_ENV: started.failure or ""}
    return run([*overlay.own_command(), *args], env=env, check=False).returncode


def log_start(started: EngineStart) -> None:
    """Log the updates that didn't load at start-up, which happens before logging is set up."""
    if started.failed is None:
        return
    using = f"update {started.update}" if started.update else "built-in copy"
    reasons = started.failure or f"{started.failed}: no reason was given"
    log.warning("Download engine updates didn't load, so the %s is used: %s", using, reasons)
