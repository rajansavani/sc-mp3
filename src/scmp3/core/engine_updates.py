"""Updating the download engine: newer yt-dlp releases, installed beside the built-in copy.

An update is checked in a child process before it's marked good. The next start uses the newest
good update that's newer than the built-in copy, and Revert sets every update aside (TECH_SPEC §14).
"""

import importlib.metadata
import logging
import time
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

from scmp3.core import net, overlay, releases, runner
from scmp3.core.components import Components, Runner
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.fetch import DownloadProgress
from scmp3.core.github import Clock
from scmp3.core.manifests import ComponentStatus, Manifest
from scmp3.core.models import EngineChannel
from scmp3.core.net import Opener
from scmp3.core.releases import ENGINE, is_newer

ENGINE_PACKAGE = "yt-dlp"
# Seconds the child process may take to import and check an update. An antivirus scan of the
# new zip can hold up its first import.
CHECK_TIMEOUT = 60

log = logging.getLogger(__name__)


def built_in_version() -> str | None:
    """The bundled yt-dlp's version from its package metadata, which needs no import of it."""
    try:
        return importlib.metadata.version(ENGINE_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        log.warning("yt-dlp's package metadata is missing, so its version isn't known")
        return None


@dataclass(frozen=True)
class EngineUpdate:
    """What an update found: the channel's latest version, and whether it's installed now."""

    version: str
    installed: bool
    # How the latest was set aside before, bad or reverted, when that kept it from installing.
    refused: ComponentStatus | None = None


class EngineUpdates:
    """The engine updates in the components folder, and installing and setting them aside."""

    def __init__(
        self,
        components: Components,
        *,
        built_in: str | None,
        in_use: str | None = None,
        opener: Opener = net.open_url,
        clock: Clock = time.time,
        run: Runner = runner.run,
        command: Callable[[], list[str]] = overlay.own_command,
    ) -> None:
        self._components = components
        self._built_in = built_in
        # The update this process runs, whose files must stay where they are.
        self._in_use = in_use
        self._opener = opener
        self._clock = clock
        self._run = run
        self._command = command

    def next_update(self, skip: Collection[str] = ()) -> Manifest | None:
        """The update the next start uses: the newest good one, if it's newer than the built-in."""
        for manifest in reversed(self._components.installed(ENGINE)):
            if manifest.status != "good" or manifest.version in skip:
                continue
            if not self._components.file_path(manifest).is_file():
                continue
            return manifest if is_newer(manifest.version, self._built_in) else None
        return None

    def zip_path(self, manifest: Manifest) -> Path:
        """Where an installed update's zipapp is."""
        return self._components.file_path(manifest)

    def failed_update(self) -> Manifest | None:
        """The newest update, when it's one that failed to load."""
        installed = self._components.installed(ENGINE)
        if installed and installed[-1].status == "bad":
            return installed[-1]
        return None

    def update(
        self,
        channel: EngineChannel,
        *,
        again: bool = False,
        progress: DownloadProgress | None = None,
    ) -> EngineUpdate:
        """Install the channel's latest yt-dlp when it's newer than what the next start uses.

        A version that was marked bad or reverted is installed again only with `again`, as
        Update now does. The daily check leaves it alone.
        """
        download = releases.choose_engine_download(channel, opener=self._opener, clock=self._clock)
        version = download.version
        upcoming = self.next_update()
        if not is_newer(version, upcoming.version if upcoming else self._built_in):
            return EngineUpdate(version, installed=False)
        earlier = self.installed_version(version)
        if earlier is not None and earlier.status != "good" and not again:
            log.info("yt-dlp %s was set aside before, so it isn't installed again", version)
            return EngineUpdate(version, installed=False, refused=earlier.status)
        if earlier is not None and version == self._in_use:
            # This process runs it, so it's marked good again rather than replaced under it.
            self._components.set_status(earlier, "good")
            return EngineUpdate(version, installed=True)
        self._components.install(download, progress=progress, check=self._check)
        # The one the next start used to get stays too, since a CLI run may be using it now.
        upcoming_version = upcoming.version if upcoming else None
        self._remove_all_but({version, self._in_use, upcoming_version})
        return EngineUpdate(version, installed=True)

    def revert(self) -> None:
        """Set every update aside, so the next start uses the built-in copy."""
        for manifest in self._components.installed(ENGINE):
            if manifest.status != "reverted":
                self._components.set_status(manifest, "reverted")

    def _check(self, zip_path: Path) -> None:
        """Import a downloaded update in a new process, so a broken one never runs in this one."""
        command = [*self._command(), overlay.CHECK_FLAG, str(zip_path)]
        self._run(command, timeout=CHECK_TIMEOUT, error_kind=ErrorKind.bad_download)

    def installed_version(self, version: str) -> Manifest | None:
        """The manifest of one installed update, whatever its status."""
        for manifest in self._components.installed(ENGINE):
            if manifest.version == version:
                return manifest
        return None

    def _remove_all_but(self, keep: Iterable[str | None]) -> None:
        """Delete the updates no start will use again. One that can't be deleted waits for later."""
        kept = set(keep)
        for manifest in self._components.installed(ENGINE):
            if manifest.version in kept:
                continue
            try:
                self._components.remove(ENGINE, manifest.version)
            except ScmpError as error:
                log.warning("An old engine update wasn't removed: %s", error)
