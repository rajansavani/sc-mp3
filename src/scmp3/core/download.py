"""Downloading a track's audio into its job's staging folder, and managing those folders.

yt-dlp reports progress through a hook, which is also where a cancel request stops it.
"""

from __future__ import annotations

import gc
import logging
import re
import shutil
import threading
import time
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Protocol

from scmp3.core import paths
from scmp3.core.engine import ProgressHook, load_yt_dlp
from scmp3.core.errors import Cancelled, ErrorKind, ScmpError
from scmp3.core.models import Progress

if TYPE_CHECKING:
    from yt_dlp import YoutubeDL

# Stream URLs expire, so info older than this is extracted again before downloading.
STALE_AFTER = 10 * 60  # seconds
SOURCE_STEM = "source"
DEFAULT_PURGE_DAYS = 7

# Job ids become folder names, and cleanup deletes the folder, so only plain names are allowed.
JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

ProgressCallback = Callable[[Progress], None]
Reextract = Callable[[], dict[str, Any]]
Clock = Callable[[], float]

log = logging.getLogger(__name__)


class YdlFactory(Protocol):
    """Builds a YoutubeDL with the standard options, like `engine.make_ydl`."""

    def __call__(
        self,
        *,
        progress_hook: ProgressHook | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> YoutubeDL: ...


def download(
    info: dict[str, Any],
    format_id: str,
    staging_dir: Path,
    *,
    ydl_factory: YdlFactory,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    reextract: Reextract | None = None,
    clock: Clock = time.time,
) -> Path:
    """Download one format of an extracted track into `staging_dir` and return the file's path.

    It first deletes every source.* file an earlier download left in the folder, so callers
    should give their own files other names.

    Raises Cancelled when `cancel` is set, and then leaves no source.* file behind. Work yt-dlp
    does on its own can't be interrupted: the HLS fixup with ffmpeg, and the ffmpeg download
    for playlists its HLS downloader can't handle. A cancel during that work takes effect when
    the work ends. Other yt-dlp errors reach the caller unchanged.
    """
    _stop_if_cancelled(cancel)
    if _is_stale(info, clock()):
        info = _fresh_info(info, reextract)
        _stop_if_cancelled(cancel)
    _remove_leftovers(staging_dir)

    yt_dlp = load_yt_dlp()
    # Drops what an earlier format choice left in the info, as yt-dlp's --load-info-json does.
    clean_info = yt_dlp.YoutubeDL.sanitize_info(dict(info), remove_private_keys=True)
    options = {
        "format": format_id,
        "outtmpl": source_template(staging_dir),
        # A second guard next to the clear above: never hand back an existing file as this one.
        "overwrites": True,
        # A fragment that keeps failing fails the download, instead of leaving a gap in the audio.
        "skip_unavailable_fragments": False,
    }
    hook = _progress_hook(on_progress, cancel, yt_dlp.utils.DownloadCancelled)
    cancelled = False
    try:
        with ydl_factory(progress_hook=hook, extra=options) as ydl:
            result = ydl.process_ie_result(clean_info, download=True)
    except yt_dlp.utils.DownloadCancelled:
        # Cancelled is raised after this block, which lets go of yt-dlp's frames and open files.
        cancelled = True

    # A cancel that arrives after the last progress update still counts.
    if cancelled or _is_set(cancel):
        _end_cancelled(staging_dir)
    return downloaded_path(result)


def source_template(staging_dir: Path) -> str:
    """yt-dlp's output template for the downloaded file: <staging>/source.<ext>."""
    # A % in the folder's own path would otherwise start a template field.
    folder = str(staging_dir).replace("%", "%%")
    return str(Path(folder) / f"{SOURCE_STEM}.%(ext)s")


def downloaded_path(result: Mapping[str, Any]) -> Path:
    """The file yt-dlp says it saved, from `requested_downloads`. No folder is searched."""
    downloads = result.get("requested_downloads") or []
    filepath = downloads[0].get("filepath") if downloads else None
    if not filepath:
        raise ScmpError(ErrorKind.engine_outdated, "yt-dlp didn't report the downloaded file")
    path = Path(filepath)
    if not path.is_file():
        raise ScmpError(ErrorKind.engine_outdated, f"yt-dlp reported {path.name}, but it's missing")
    return path


def _is_set(cancel: threading.Event | None) -> bool:
    """Whether the user has asked to stop."""
    return cancel is not None and cancel.is_set()


def _stop_if_cancelled(cancel: threading.Event | None) -> None:
    """Raise Cancelled when the user has asked to stop."""
    if _is_set(cancel):
        raise Cancelled


def _remove_leftovers(staging_dir: Path) -> None:
    """Delete what an earlier download left: source.* with its .part, .ytdl and fragment files.

    yt-dlp would otherwise resume a partial file into the next download, even of another format.
    """
    for leftover in sorted(staging_dir.glob(f"{SOURCE_STEM}.*")):
        try:
            leftover.unlink(missing_ok=True)
        except OSError as error:
            reason = error.strerror or error
            raise ScmpError(
                ErrorKind.disk, f"can't delete {leftover.name} from the staging folder: {reason}"
            ) from error


def _end_cancelled(staging_dir: Path) -> NoReturn:
    """Delete what a cancelled download left in the folder, then raise Cancelled."""
    # yt-dlp's fragment downloader keeps its files open until its objects are collected.
    gc.collect()
    try:
        _remove_leftovers(staging_dir)
    except ScmpError as error:
        # The next download in this folder clears it again.
        log.warning("After a cancel, %s", error.detail)
    raise Cancelled


def _is_stale(info: Mapping[str, Any], now: float) -> bool:
    """Whether the info's stream URLs may have expired."""
    # yt-dlp sets epoch when it extracts. Without it the age is unknown, so assume the worst.
    extracted_at = info.get("epoch")
    if not isinstance(extracted_at, int | float):
        return True
    return now - extracted_at > STALE_AFTER


def _fresh_info(info: dict[str, Any], reextract: Reextract | None) -> dict[str, Any]:
    """Info extracted again, or the old info with a warning when there's no way to refresh it."""
    if reextract is None:
        log.warning("The track info may be too old to download, and it can't be fetched again")
        return info
    log.info("Fetching the track info again, since its stream links may have expired")
    return reextract()


def _progress_hook(
    on_progress: ProgressCallback | None,
    cancel: threading.Event | None,
    cancelled_error: type[Exception],
) -> ProgressHook:
    """A yt-dlp progress hook that reports progress and stops the download on cancel."""

    def hook(status: dict[str, Any]) -> None:
        if cancel is not None and cancel.is_set():
            # yt-dlp lets this exception through to process_ie_result's caller.
            raise cancelled_error()
        if on_progress is None:
            return
        if status.get("status") in ("downloading", "finished"):
            on_progress(progress_from(status))

    return hook


def progress_from(status: Mapping[str, Any]) -> Progress:
    """Turn one of yt-dlp's progress dicts into a Progress."""
    downloaded = _int_or_none(status.get("downloaded_bytes"))
    # HLS downloads only have an estimate, from the fragments so far.
    total = _int_or_none(status.get("total_bytes") or status.get("total_bytes_estimate"))
    return Progress(
        pct=_percent(status, downloaded, total),
        speed=status.get("speed"),
        eta=status.get("eta"),
        downloaded=downloaded,
        total=total,
    )


def _percent(status: Mapping[str, Any], downloaded: int | None, total: int | None) -> float | None:
    """How far the download is, from 0 to 100, or None while that's unknown."""
    if status.get("status") == "finished":
        return 100.0
    if downloaded is not None and total:
        return min(100.0, 100.0 * downloaded / total)
    fragment_index = status.get("fragment_index")
    fragment_count = status.get("fragment_count")
    if fragment_index is not None and fragment_count:
        return min(100.0, 100.0 * fragment_index / fragment_count)
    return None


def _int_or_none(value: Any) -> int | None:
    """A byte count as an int. yt-dlp gives estimates as floats."""
    return int(value) if isinstance(value, int | float) else None


def new_staging(job_id: str) -> Path:
    """Create the job's staging folder, <cache>/staging/<job_id>, and return it."""
    folder = _staging_folder(job_id)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ScmpError(
            ErrorKind.disk, f"can't create the staging folder: {error.strerror or error}"
        ) from error
    return folder


def cleanup(job_id: str) -> bool:
    """Delete the job's staging folder. Returns False, with a warning, when it can't be deleted."""
    return _remove_folder(_staging_folder(job_id))


def purge_old(
    days: float = DEFAULT_PURGE_DAYS,
    keep: Collection[str] = frozenset(),
    *,
    clock: Clock = time.time,
) -> list[str]:
    """Delete staging folders not changed for `days` days, except the jobs in `keep`.

    Returns the job ids whose folders were deleted.
    """
    root = paths.staging_dir()
    if not root.is_dir():
        return []
    cutoff = clock() - days * 24 * 60 * 60
    purged: list[str] = []
    for folder in sorted(root.iterdir()):
        if folder.name in keep or not folder.is_dir():
            continue
        if _modified_at(folder) >= cutoff:
            continue
        if _remove_folder(folder):
            purged.append(folder.name)
    if purged:
        log.info("Deleted %d old staging folders", len(purged))
    return purged


def _staging_folder(job_id: str) -> Path:
    """The staging folder for a job id, after checking the id is a plain folder name."""
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError(f"Not a usable job id for a folder name: {job_id!r}")
    return paths.staging_dir() / job_id


def _modified_at(folder: Path) -> float:
    """When the folder last changed. Adding, renaming or removing a file in it counts."""
    try:
        return folder.stat().st_mtime
    except FileNotFoundError:
        # A folder that vanished meanwhile has nothing left to purge.
        return float("inf")


def _remove_folder(folder: Path) -> bool:
    """Delete a folder and everything in it. A folder that's already gone counts as deleted."""
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        return True
    except OSError as error:
        # The job's file may still be open, for example in a player. A later purge retries.
        reason = error.strerror or error
        log.warning("Couldn't delete the staging folder %s: %s", folder.name, reason)
        return False
    return True
