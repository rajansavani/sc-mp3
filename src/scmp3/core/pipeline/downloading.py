"""The pipeline's second stage: download the audio into the job's staging folder and convert it to
the saved format."""

from __future__ import annotations

import functools
import threading
from pathlib import Path
from typing import Any

from scmp3.core import convert, download
from scmp3.core.conversion_choice import choose_conversion, original_holds_up
from scmp3.core.errors import Cancelled
from scmp3.core.models import FormatOption
from scmp3.core.pipeline.common import Prepared, Staged, expected_duration, only_scmp_errors
from scmp3.core.sources.soundcloud_formats import choose_format

# Downloads are named source.*, which download() clears first, so converted files get this name.
STAGED_STEM = "audio"


def fetch(
    prepared: Prepared,
    *,
    on_progress: download.ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Staged:
    """Download the audio into the job's staging folder and convert it to the saved format.

    A lossy original is kept only when its bitrate holds up against the best stream. Raises
    Cancelled when `cancel` is set, and ScmpError for every failure, including a preview.
    `on_progress` runs inside yt-dlp, so whatever it raises comes out as ScmpError(internal).
    """
    with only_scmp_errors(where="data"):
        choice = choose_format(prepared.source, signed_in=bool(prepared.token))
        staging = download.new_staging(prepared.job_id)
        downloaded = choice.format
        path = _download(prepared, downloaded, staging, on_progress, cancel)
        fallback = choice.fallback
        if choice.needs_bitrate_check and fallback and not original_holds_up(path, fallback):
            downloaded = fallback
            path = _download(prepared, downloaded, staging, on_progress, cancel)
        conversion = choose_conversion(prepared.settings.audio_format, convert.probe(path).codec)
        staged_path = staging / f"{STAGED_STEM}.{conversion.fmt}"
        conversion.run(path, staged_path, cancel=cancel)
        if cancel is not None and cancel.is_set():
            raise Cancelled
        # Checked here as well as in finalize, so a preview fails before anyone reviews it.
        audio = convert.validate(staged_path, expected_duration(prepared.source))
        return Staged(staged_path, conversion.fmt, audio, downloaded)


def _download(
    prepared: Prepared,
    fmt: FormatOption,
    staging: Path,
    on_progress: download.ProgressCallback | None,
    cancel: threading.Event | None,
) -> Path:
    """Download one format of the track and return the file's path."""
    return download.download(
        prepared.info,
        fmt.format_id,
        staging,
        ydl_factory=functools.partial(prepared.tools.make_ydl, prepared.token),
        on_progress=on_progress,
        cancel=cancel,
        reextract=functools.partial(_extract_again, prepared),
    )


def _extract_again(prepared: Prepared) -> dict[str, Any]:
    """yt-dlp's info for the track, extracted again because its stream links may have expired."""
    with prepared.tools.make_ydl(prepared.token) as ydl:
        info = ydl.extract_info(prepared.source.url, download=False)
    # Kept, so a second download or a retry starts from the fresh links too.
    prepared.info = info
    return info
