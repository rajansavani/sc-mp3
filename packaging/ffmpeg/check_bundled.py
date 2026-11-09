"""Check the ffmpeg inside a built app by running the app's MP3 commands on a WAV tone.

Usage: uv run python packaging/ffmpeg/check_bundled.py --ffmpeg <the app's ffmpeg> --out <folder>
release.yml runs this on each app it builds. Our ffmpeg has no lavfi to make a tone with, so the
tone is written here with Python's wave module.
"""

import argparse
import array
import math
import sys
import wave
from collections.abc import Sequence
from pathlib import Path

# packaging/ffmpeg/ is on sys.path when this file runs as a script.
from check_common import SECONDS, NamedOutcome, outcome_table, run_and_check
from check_conversions import COMMANDS, output_name
from check_samples import Sample

SAMPLE_RATE = 44100
TONE_HZ = 440
# Half of full scale, so the tone is loud without clipping.
AMPLITUDE = 16384
TONE = Sample("tone.wav", "16-bit WAV tone", ())


def write_tone(path: Path) -> Path:
    """Write SECONDS of a stereo sine tone as a 16-bit WAV."""
    frames = array.array("h")
    for index in range(int(SECONDS * SAMPLE_RATE)):
        value = round(AMPLITUDE * math.sin(2 * math.pi * TONE_HZ * index / SAMPLE_RATE))
        frames.extend((value, value))
    # WAV samples are little-endian, and array writes them in the machine's own order.
    if sys.byteorder == "big":
        frames.byteswap()
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(frames.tobytes())
    return path


def check_bundled(ffmpeg: Path, folder: Path) -> list[NamedOutcome]:
    """Convert the tone with each of the app's commands that takes a WAV, and check each file."""
    folder.mkdir(parents=True, exist_ok=True)
    tone = write_tone(folder / TONE.file_name)
    results = []
    for command in COMMANDS:
        if not command.applies(TONE):
            continue
        dst = folder / output_name(TONE, command)
        outcome = run_and_check(command.run, command.check, ffmpeg, tone, dst)
        results.append(NamedOutcome(TONE.label, command.name, outcome))
    return results


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the ffmpeg inside a built app.")
    parser.add_argument("--ffmpeg", type=Path, required=True, help="the app's ffmpeg")
    parser.add_argument("--out", type=Path, required=True, help="where the files go")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the checks, print their table, and return 0 only when every one passed."""
    args = parse_args(argv)
    results = check_bundled(args.ffmpeg.resolve(), args.out)
    print("\n".join(outcome_table(results, "Source")))
    # No results would mean no command took a WAV, which checks nothing.
    passed = bool(results) and all(result.outcome.passed for result in results)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
