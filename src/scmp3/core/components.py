"""Downloaded components, such as Deno for YouTube: installing, listing and removing them.

Each version lives in `<data>/components/<name>/<version>/` next to its manifest.json. An install
downloads into a new temp folder, checks the SHA-256, unpacks, and only then renames the folder
into place and writes the manifest, so a failed install leaves nothing behind.
"""

import logging
import os
import shutil
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from scmp3.core import fetch, net, paths, releases, runner
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.fetch import DownloadProgress, disk_errors, os_reason
from scmp3.core.github import Clock
from scmp3.core.log import safe_url
from scmp3.core.manifests import Manifest, read_manifest, safe_part, write_manifest
from scmp3.core.net import Opener
from scmp3.core.releases import ComponentSpec, Download

DENO = "deno"
# Installs in progress work in a folder starting with this, which listing skips.
WORK_PREFIX = ".install-"
# Seconds `deno --version` may take. An antivirus scan can hold up a new binary's first run.
CHECK_TIMEOUT = 60

# Raises ScmpError when an unpacked file doesn't work, before it's moved into place.
type Check = Callable[[Path], None]

log = logging.getLogger(__name__)


class Runner(Protocol):
    """Runs a program to the end, as `runner.run` does."""

    def __call__(
        self, command: Sequence[str], *, timeout: float, error_kind: ErrorKind
    ) -> None: ...


class Components:
    """The components folder, where each downloaded version gets a folder and a manifest."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        opener: Opener = net.open_url,
        clock: Clock = time.time,
        run: Runner = runner.run,
        target: str | None = None,
    ) -> None:
        self.root = root or paths.components_dir()
        self._opener = opener
        self._clock = clock
        self._run = run
        # This machine as <os>-<arch>, or None for an OS or CPU we don't know.
        self._target = target or paths.platform_tag()
        # One install or removal at a time, so a second click waits and then finds it installed.
        self._lock = threading.RLock()
        # How many installs of each component are running, read without waiting on the lock.
        self._installing: Counter[str] = Counter()
        self._installing_lock = threading.Lock()

    def installed(self, name: str) -> list[Manifest]:
        """Every installed version of a component, oldest install first."""
        folder = self.root / safe_part(name)
        if not folder.is_dir():
            return []
        manifests = []
        for version_dir in folder.iterdir():
            manifest = read_manifest(version_dir)
            if manifest is not None:
                manifests.append(manifest)
        return sorted(manifests, key=lambda manifest: manifest.installed_at)

    def active(self, name: str) -> Manifest | None:
        """The version in use: the newest good install whose file is still there."""
        for manifest in reversed(self.installed(name)):
            if manifest.status == "good" and self.file_path(manifest).is_file():
                return manifest
        return None

    def active_path(self, name: str) -> Path | None:
        """The file of the version in use, such as Deno's binary, or None when there's none."""
        manifest = self.active(name)
        if manifest is None:
            return None
        return self.file_path(manifest)

    def file_path(self, manifest: Manifest) -> Path:
        return self._version_dir(manifest.name, manifest.version) / manifest.file

    def installing(self, name: str) -> bool:
        """Whether an install of this component is running right now."""
        with self._installing_lock:
            return self._installing[name] > 0

    @contextmanager
    def _counted_install(self, name: str) -> Iterator[None]:
        with self._installing_lock:
            self._installing[name] += 1
        try:
            yield
        finally:
            with self._installing_lock:
                self._installing[name] -= 1

    def remove(self, name: str, version: str | None = None) -> None:
        """Delete one installed version of a component, or every version when none is given."""
        folder = self.root / safe_part(name)
        if version is not None:
            folder = self._version_dir(name, version)
        with self._lock:
            try:
                shutil.rmtree(folder)
            except FileNotFoundError:
                return
            except OSError as error:
                detail = f"can't remove {folder.name}: {os_reason(error)}"
                raise ScmpError(ErrorKind.disk, detail) from error

    def install_deno(self, progress: DownloadProgress | None = None) -> Manifest:
        """Install Deno, the JavaScript runtime yt-dlp needs for YouTube, unless it already is."""
        return self.ensure(releases.load_spec(DENO), progress=progress, check=self.check_runs)

    def check_runs(self, binary: Path) -> None:
        """Run the binary with --version, so one that can't run here is never installed."""
        command = [str(binary), "--version"]
        self._run(command, timeout=CHECK_TIMEOUT, error_kind=ErrorKind.bad_download)

    def ensure(
        self,
        spec: ComponentSpec,
        *,
        progress: DownloadProgress | None = None,
        check: Check | None = None,
    ) -> Manifest:
        """The active version of a component, installing the latest release when there's none."""
        with self._lock, self._counted_install(spec.name):
            active = self.active(spec.name)
            if active is not None:
                return active
            download = releases.choose_download(
                spec, self._target, opener=self._opener, clock=self._clock
            )
            return self.install(download, progress=progress, check=check)

    def install(
        self,
        download: Download,
        *,
        progress: DownloadProgress | None = None,
        check: Check | None = None,
    ) -> Manifest:
        """Download, check the SHA-256, unpack, run `check`, rename into place, write the manifest.

        Installing a version that's already there and good changes nothing.
        """
        # The file name becomes a path part too, so it can't point outside the version's folder.
        safe_part(download.file_name)
        with self._lock, self._counted_install(download.name):
            version_dir = self._version_dir(download.name, download.version)
            existing = read_manifest(version_dir)
            if existing is not None and self._is_intact(existing, download):
                return existing
            return self._install_fresh(download, version_dir, progress, check)

    def _install_fresh(
        self,
        download: Download,
        version_dir: Path,
        progress: DownloadProgress | None,
        check: Check | None,
    ) -> Manifest:
        where = safe_url(download.url)
        log.info("Installing %s %s from %s", download.name, download.version, where)
        with self._work_dir(version_dir.parent) as work:
            archive = work / "download"
            fetch.download_verified(
                download.url, download.sha256, archive, opener=self._opener, progress=progress
            )
            staged = work / "staged"
            _unpack(download, archive, staged)
            if check is not None:
                check(staged / download.file_name)
            _move_into_place(staged, version_dir, work / "replaced")
        manifest = Manifest(
            name=download.name,
            version=download.version,
            sha256=download.sha256,
            source_url=download.url,
            file=download.file_name,
            installed_at=datetime.fromtimestamp(self._clock(), UTC),
        )
        write_manifest(version_dir, manifest)
        log.info("Installed %s %s", download.name, download.version)
        return manifest

    @contextmanager
    def _work_dir(self, component_dir: Path) -> Iterator[Path]:
        """A new, empty temp folder beside the versions, so the final rename stays on one disk."""
        with disk_errors():
            component_dir.mkdir(parents=True, exist_ok=True)
            # Left by an install that was cut off, such as by closing the app mid-download.
            for leftover in component_dir.glob(f"{WORK_PREFIX}*"):
                _remove_tree(leftover)
            work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=component_dir))
        try:
            yield work
        finally:
            _remove_tree(work)
            _remove_if_empty(component_dir)

    def _version_dir(self, name: str, version: str) -> Path:
        return self.root / safe_part(name) / safe_part(version)

    def _is_intact(self, manifest: Manifest, download: Download) -> bool:
        """Whether an installed version is the good, complete copy of this download."""
        return (
            manifest.status == "good"
            and manifest.sha256 == download.sha256.lower()
            and manifest.file == download.file_name
            and self.file_path(manifest).is_file()
        )


def _unpack(download: Download, archive: Path, staged: Path) -> None:
    """Put the download's one file in the staged folder, taken out of its zip or moved as is."""
    dest = staged / download.file_name
    with disk_errors():
        staged.mkdir()
    if download.member is None:
        with disk_errors():
            os.replace(archive, dest)
        fetch.sync_file(dest)
    else:
        fetch.extract_member(archive, download.member, dest)
    if download.executable:
        fetch.make_executable(dest)


def _move_into_place(staged: Path, version_dir: Path, aside: Path) -> None:
    """Rename the staged folder to the version's, replacing an unfinished or damaged copy."""
    with disk_errors():
        if version_dir.exists():
            # Moved aside first, since a rename can't replace a folder that has files in it.
            os.rename(version_dir, aside)
        os.rename(staged, version_dir)


def _remove_tree(path: Path) -> None:
    """Delete a temp folder. What can't be deleted is logged, since the install itself is done."""

    def warn(function: Callable[..., object], name: str, error: BaseException) -> None:
        reason = os_reason(error)
        log.warning("Couldn't delete %s from the components folder: %s", Path(name).name, reason)
        log.debug("Deleting from the components folder failed", exc_info=error)

    if path.exists():
        shutil.rmtree(path, onexc=warn)


def _remove_if_empty(folder: Path) -> None:
    """Delete a component's folder that a failed first install left empty."""
    try:
        empty = not any(folder.iterdir())
    except OSError as error:
        # This runs while another error may be on its way out, so it mustn't raise.
        log.warning("Couldn't look in the components folder %s: %s", folder.name, os_reason(error))
        log.debug("Looking in the components folder failed", exc_info=error)
        return
    if empty:
        _remove_tree(folder)
