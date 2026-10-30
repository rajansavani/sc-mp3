"""What the ffmpeg build checks share: the sample length, outcomes, and running ffmpeg."""

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from scmp3.core import convert

SECONDS = 3.0
# How far a converted file's length may be from the sample's, in seconds. AAC's encoder delay
# alone adds up to about 0.08 s.
LENGTH_TOLERANCE = 0.15
# How many lines of ffmpeg's error output a failed command shows.
ERROR_LINES = 6

# Makes dst from src with the given ffmpeg.
type Runner = Callable[[Path, Path, Path], None]


@dataclass(frozen=True)
class Outcome:
    """One check's result: whether it passed, and what it found or what went wrong."""

    passed: bool
    detail: str
    seconds: float = 0.0


@dataclass(frozen=True)
class NamedOutcome:
    """An outcome with the source and the check it belongs to, for the report's tables."""

    source: str
    check: str
    outcome: Outcome


class CommandFailed(Exception):
    """ffmpeg exited with an error; the message holds the end of its output."""


def run_ffmpeg(command: Sequence[str]) -> None:
    """Run ffmpeg, raising CommandFailed with the end of its output when it fails."""
    result = subprocess.run(command, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-ERROR_LINES:]
        raise CommandFailed(f"exit code {result.returncode}: " + " / ".join(tail))


def length_problems(path: Path) -> list[str]:
    length = convert.probe(path).duration
    if abs(length - SECONDS) > LENGTH_TOLERANCE:
        return [f"{length:.2f} s long, not {SECONDS:.0f} s"]
    return []


def aac_m4a_problems(path: Path) -> list[str]:
    """What's wrong with a file that should be SECONDS of AAC in an M4A."""
    codec = convert.probe(path).codec
    problems = length_problems(path)
    if codec != "aac":
        problems.append(f"holds {codec}, not AAC")
    return problems


def run_and_check(
    run: Runner, check: Callable[[Path], list[str]], ffmpeg: Path, src: Path, dst: Path
) -> Outcome:
    """Make dst from src, check it, and describe what it holds."""
    start = time.perf_counter()
    try:
        run(ffmpeg, src, dst)
        elapsed = time.perf_counter() - start
        problems = check(dst)
        info = convert.probe(dst)
    except Exception as error:
        # Any failure here is a finding for the report, not a reason to stop checking.
        return Outcome(False, f"{type(error).__name__}: {error}", time.perf_counter() - start)
    if problems:
        return Outcome(False, "; ".join(problems), elapsed)
    return Outcome(True, f"{info.codec}, {info.bitrate} kbps, {info.duration:.2f} s", elapsed)


def outcome_table(results: Sequence[NamedOutcome], source_heading: str) -> list[str]:
    """Markdown rows for a list of outcomes."""
    rows = [f"| {source_heading} | Check | Result | Time |", "|---|---|---|---|"]
    for result in results:
        outcome = result.outcome
        detail = ("ok: " if outcome.passed else "FAILED: ") + outcome.detail.replace("|", "/")
        rows.append(f"| {result.source} | {result.check} | {detail} | {outcome.seconds:.2f} s |")
    return rows
