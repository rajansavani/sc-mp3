"""Tests of events from Python reaching the UI, in a hidden app window on Windows.

Each runs in a child process, since pywebview can start only once per process.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).parents[1] / "fixtures" / "ui_rejected_link.py"
REFETCH_PROBE = Path(__file__).parents[1] / "fixtures" / "ui_set_refetch.py"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="needs a WebView2 window, which only Windows CI has"
)


def test_a_link_rejected_after_resolving_shows_the_same_toast_as_one_rejected_at_once() -> None:
    # The window closes itself within 300 s. On a quiet machine it takes about 3 s, but with every
    # core busy, WebView2 has taken up to 110 s to show the UI.
    result = subprocess.run(
        [sys.executable, str(PROBE)],
        capture_output=True,
        text=True,
        timeout=330,
        check=False,
    )
    lines = result.stdout.strip().splitlines()
    assert lines, result.stderr[-2000:]
    answer = json.loads(lines[-1])
    assert "error" not in answer, answer
    toast = "https://on.soundcloud.com/AbCdEfGh wasn't added. This short link leads to a SoundCloud"
    assert toast in answer["text"]
    # The job left the queue before the toast came.
    assert "Nothing queued yet" in answer["text"]


def test_changes_that_arrive_while_the_page_fetches_the_queue_again_are_kept() -> None:
    result = subprocess.run(
        [sys.executable, str(REFETCH_PROBE)],
        capture_output=True,
        text=True,
        timeout=330,
        check=False,
    )
    lines = result.stdout.strip().splitlines()
    assert lines, result.stderr[-2000:]
    answer = json.loads(lines[-1])
    assert "error" not in answer, answer
    # The change came while the snapshot was out, so it's applied on top of the snapshot.
    assert answer["titles"] == ["Track 1", "Held change", "Track 3", "Track 4", "Track 5"]
    # The page's first snapshot, one for each of the three misfits, and never two out at once.
    assert answer["snapshots"] == 4
    assert answer["most_out_at_once"] == 1
