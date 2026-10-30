"""Every way sc-mp3 runs ffmpeg on a downloaded file, run on each sample with our build."""

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from check_common import (
    CommandFailed,
    NamedOutcome,
    Outcome,
    Runner,
    aac_m4a_problems,
    length_problems,
    run_and_check,
    run_ffmpeg,
)
from check_samples import SAMPLES, Sample, make_cover, make_sample
from mutagen.mp3 import MP3, BitrateMode

from scmp3.core import convert


@dataclass(frozen=True)
class Command:
    """One way sc-mp3 runs ffmpeg on a download, and how to check the file it makes."""

    name: str
    suffix: str
    applies: Callable[[Sample], bool]
    run: Runner
    check: Callable[[Path], list[str]]


def app_command(codec: Sequence[str]) -> Runner:
    """Run one of the app's own conversion commands, built by scmp3.core.convert."""

    def run(ffmpeg: Path, src: Path, dst: Path) -> None:
        run_ffmpeg(convert.ffmpeg_command(ffmpeg, src, dst, codec))

    return run


def yt_dlp_fixup(name: str, info: dict[str, str]) -> Runner:
    """Run the named yt-dlp fixup on a copy of src, as yt-dlp does after a download."""

    def run(ffmpeg: Path, src: Path, dst: Path) -> None:
        # Imported here, as the app does, so yt-dlp loads only when it's used.
        from yt_dlp import YoutubeDL
        from yt_dlp.postprocessor import ffmpeg as yt_dlp_ffmpeg

        shutil.copyfile(src, dst)
        # The app passes yt-dlp the bundled ffmpeg the same way, with no ffprobe beside it.
        with YoutubeDL({"ffmpeg_location": str(ffmpeg), "quiet": True, "noprogress": True}) as ydl:
            postprocessor = getattr(yt_dlp_ffmpeg, name)(ydl)
            postprocessor.run({"filepath": str(dst), "vcodec": "none", **info})

    return run


def mode_name(mode: BitrateMode) -> str:
    """CBR, VBR and so on. mutagen's modes are ints, which print as BitrateMode.CBR."""
    return str(mode).removeprefix("BitrateMode.")


def mp3_problems(path: Path, mode: BitrateMode, bitrate: int | None) -> list[str]:
    """What's wrong with an MP3 that should have this bitrate mode, and bitrate if CBR."""
    info = MP3(path).info
    problems = length_problems(path)
    if info.bitrate_mode != mode:
        problems.append(f"bitrate mode {mode_name(info.bitrate_mode)}, not {mode_name(mode)}")
    if bitrate is not None and info.bitrate != bitrate:
        problems.append(f"{info.bitrate // 1000} kbps, not {bitrate // 1000}")
    return problems


def mp3_320_problems(path: Path) -> list[str]:
    return mp3_problems(path, BitrateMode.CBR, 320_000)


def mp3_v0_problems(path: Path) -> list[str]:
    return mp3_problems(path, BitrateMode.VBR, None)


def any_sample(sample: Sample) -> bool:
    return True


def aac_sample(sample: Sample) -> bool:
    return sample.is_aac


def hls_sample(sample: Sample) -> bool:
    return sample.from_hls


def dash_sample(sample: Sample) -> bool:
    return sample.from_dash


HLS_INFO = {"ext": "m4a", "protocol": "m3u8_native", "acodec": "aac"}
DASH_INFO = {"ext": "m4a", "container": "m4a_dash", "acodec": "aac"}

COMMANDS = (
    Command("MP3 320", ".mp3", any_sample, app_command(convert.MP3_320_CODEC), mp3_320_problems),
    Command("MP3 V0", ".mp3", any_sample, app_command(convert.MP3_V0_CODEC), mp3_v0_problems),
    Command(
        "Keep as m4a", ".m4a", aac_sample, app_command(convert.M4A_COPY_CODEC), aac_m4a_problems
    ),
    Command(
        "yt-dlp HLS fixup",
        ".m4a",
        hls_sample,
        yt_dlp_fixup("FFmpegFixupM3u8PP", HLS_INFO),
        aac_m4a_problems,
    ),
    Command(
        "yt-dlp M4A fixup",
        ".m4a",
        dash_sample,
        yt_dlp_fixup("FFmpegFixupM4aPP", DASH_INFO),
        aac_m4a_problems,
    ),
)


def output_name(sample: Sample, command: Command) -> str:
    """The output file's name, such as flac-mp3-320.mp3."""
    stem = Path(sample.file_name).stem
    return f"{stem}-{command.name.lower().replace(' ', '-')}{command.suffix}"


def check_conversions(ffmpeg: Path, reference: Path, folder: Path) -> list[NamedOutcome]:
    """Make every sample with the reference, then run each command that applies to it."""
    samples_dir = folder / "samples"
    outputs_dir = folder / "outputs"
    samples_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    cover = make_cover(reference, samples_dir)
    results = []
    for sample in SAMPLES:
        try:
            src = make_sample(reference, sample, samples_dir, cover)
        except CommandFailed as error:
            # The reference's fault, not ours, but a sample left out is a check not made.
            outcome = Outcome(False, f"the reference ffmpeg couldn't make it: {error}")
            results.append(NamedOutcome(sample.label, "Make the sample", outcome))
            continue
        for command in COMMANDS:
            if not command.applies(sample):
                continue
            dst = outputs_dir / output_name(sample, command)
            outcome = run_and_check(command.run, command.check, ffmpeg, src, dst)
            results.append(NamedOutcome(sample_label(sample), command.name, outcome))
    return results


def sample_label(sample: Sample) -> str:
    """The sample's label, marked when it's one of T-004's seven types."""
    return f"{sample.label} (T-004)" if sample.in_t004 else sample.label
