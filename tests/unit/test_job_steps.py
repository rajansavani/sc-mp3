"""Tests for the job steps the app runs on the real pipeline."""

import threading
from pathlib import Path
from typing import Any

import pytest

from scmp3.app import job_steps
from scmp3.app.job_steps import PipelineSteps
from scmp3.core import paths
from scmp3.core.errors import ErrorKind, ScmpError


def test_a_download_without_ffmpeg_fails_before_anything_is_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_ffmpeg() -> Path:
        raise ScmpError(ErrorKind.convert_failed, "not found", reason="ffmpeg_missing")

    fetched: list[Any] = []
    monkeypatch.setattr(paths, "ffmpeg_exe", no_ffmpeg)
    monkeypatch.setattr(job_steps.pipeline, "fetch", lambda *args, **kwargs: fetched.append(args))
    steps = PipelineSteps(tools=None)  # type: ignore[arg-type]
    with pytest.raises(ScmpError) as raised:
        steps.fetch(None, on_progress=lambda progress: None, cancel=threading.Event())  # type: ignore[arg-type]
    assert raised.value.reason == "ffmpeg_missing"
    assert fetched == []
