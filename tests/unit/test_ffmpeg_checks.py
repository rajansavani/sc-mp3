"""Tests for the ffmpeg build checks in packaging/ffmpeg/. The tests that use ffmpeg_exe run the
ffmpeg on PATH, and skip without one."""

import importlib
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType

import pytest

FFMPEG_DIR = Path(__file__).resolve().parents[2] / "packaging" / "ffmpeg"


def load(name: str) -> ModuleType:
    """Import a check module by name, since packaging/ffmpeg/ isn't a package."""
    if str(FFMPEG_DIR) not in sys.path:
        sys.path.insert(0, str(FFMPEG_DIR))
    return importlib.import_module(name)


info = load("check_build_info")
common = load("check_common")
conversions = load("check_conversions")
network = load("check_network")
samples = load("check_samples")
check_build = load("check_build")

# FFmpeg 9.0.2's own texts, from fftools/opt_common.c, with its line breaks.
LGPL_TEXT = """ffmpeg is free software; you can redistribute it and/or
modify it under the terms of the GNU Lesser General Public
License as published by the Free Software Foundation; either
version 2.1 of the License, or (at your option) any later version.

ffmpeg is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
Lesser General Public License for more details."""
LGPL3_TEXT = """ffmpeg is free software; you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License as published by
the Free Software Foundation; either version 3 of the License, or
(at your option) any later version.

ffmpeg is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Lesser General Public License for more details."""
GPL_TEXT = """ffmpeg is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by"""
VERSION_TEXT = """ffmpeg version 9.0.2-sc-mp3 Copyright (c) 2000-2026 the FFmpeg developers
built with gcc 13 (GCC)
configuration: --disable-everything --enable-libmp3lame --extra-version=sc-mp3
libavutil      61.  1.102 / 61.  1.102"""
ENCODERS_TEXT = """Encoders:
 V..... = Video
 A..... = Audio
 .....D = Supports direct rendering method 1
 ------
 A....D libmp3lame           libmp3lame MP3 (MPEG audio layer 3) (codec mp3)"""
MUXERS_TEXT = """File formats:
 D. = Demuxing supported
 .E = Muxing supported
 ..d = Is a device
 ---
  E  ipod            iPod H.264 MP4 (MPEG-4 Part 14)
 D d lavfi           Libavfilter virtual input device
  E  mp3             MP3 (MPEG audio layer 3)"""
BSFS_TEXT = "Bitstream filters:\naac_adtstoasc\nvp9_superframe\n"
PROTOCOLS_TEXT = "Supported file protocols:\nInput:\n  file\n  https\nOutput:\n  file\n  pipe\n"
FILTERS_TEXT = """Filters:
  T.. = Timeline support
  | = Source or sink filter
 ... aresample         A->A       Resample audio data.
 ... abuffer           |->A       Buffer audio frames."""


def test_the_lgpl_2_1_or_later_license_passes() -> None:
    assert info.license_outcome(LGPL_TEXT).passed


@pytest.mark.parametrize(
    "text",
    [LGPL3_TEXT, GPL_TEXT, LGPL_TEXT + "\n" + GPL_TEXT, ""],
    ids=["lgpl-3", "gpl", "lgpl-and-gpl", "nothing"],
)
def test_any_license_but_the_lgpl_2_1_or_later_fails(text: str) -> None:
    assert not info.license_outcome(text).passed


def test_the_configuration_comes_from_the_version_output() -> None:
    configuration = info.configuration_line(VERSION_TEXT)
    assert configuration == "--disable-everything --enable-libmp3lame --extra-version=sc-mp3"
    assert info.configuration_outcome(configuration).passed


@pytest.mark.parametrize("option", ["--enable-gpl", "--enable-version3", "--enable-nonfree"])
def test_a_configuration_with_gpl_lgpl_3_or_nonfree_code_fails(option: str) -> None:
    outcome = info.configuration_outcome(f"--disable-everything {option}")
    assert not outcome.passed
    assert option in outcome.detail


def test_a_missing_configuration_line_fails() -> None:
    assert not info.configuration_outcome(info.configuration_line("ffmpeg version 9")).passed


def test_the_protocols_must_include_what_hls_over_https_needs() -> None:
    assert info.protocols_outcome(["crypto", "file", "http", "https", "tcp", "tls"]).passed
    outcome = info.protocols_outcome(["file", "http", "tcp"])
    assert not outcome.passed
    assert "https, tls, crypto" in outcome.detail


@pytest.mark.parametrize(
    ("option", "text", "names"),
    [
        ("-encoders", ENCODERS_TEXT, ["libmp3lame"]),
        ("-muxers", MUXERS_TEXT, ["ipod", "lavfi", "mp3"]),
        ("-bsfs", BSFS_TEXT, ["aac_adtstoasc", "vp9_superframe"]),
        ("-protocols", PROTOCOLS_TEXT, ["file", "https", "pipe"]),
        ("-filters", FILTERS_TEXT, ["aresample", "abuffer"]),
        ("-decoders", "Decoders:\n no dashes here", []),
    ],
)
def test_component_names_are_read_from_each_listing(
    option: str, text: str, names: list[str]
) -> None:
    assert info.listed_names(option, text) == names


def test_the_size_table_puts_t004s_build_next_to_ours(tmp_path: Path) -> None:
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(bytes(2_500_000))
    rows = info.size_table("windows-x64", binary, tmp_path)
    assert rows[2].startswith("| Ours, windows-x64 | 2.5 MB |")
    assert rows[3] == "| T-004: gyan.dev essentials 9.0.2 | 105.4 MB | 37.4 MB |"
    # The zip is only measured, not kept.
    assert sorted(tmp_path.iterdir()) == [binary]


def test_the_app_must_find_the_build_through_scmp3_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ours = tmp_path / "ours" / "ffmpeg.exe"
    other = tmp_path / "other" / "ffmpeg.exe"
    for path in (ours, other):
        path.parent.mkdir()
        path.write_bytes(b"pretend ffmpeg")
    monkeypatch.delenv("SCMP3_FFMPEG", raising=False)
    assert not info.app_resolution_outcome(ours).passed
    monkeypatch.setenv("SCMP3_FFMPEG", str(other))
    assert not info.app_resolution_outcome(ours).passed
    monkeypatch.setenv("SCMP3_FFMPEG", str(ours))
    assert info.app_resolution_outcome(ours).passed


def test_t004s_seven_source_types_are_all_samples() -> None:
    labels = {sample.label for sample in samples.SAMPLES if sample.in_t004}
    assert labels == {
        "AAC in M4A",
        "Opus in WebM",
        "Opus in Ogg",
        "FLAC",
        "16-bit WAV",
        "24-bit WAV",
        "ALAC in M4A",
    }


def test_each_command_runs_only_on_the_samples_it_applies_to() -> None:
    applied = {
        command.name: {s.file_name for s in samples.SAMPLES if command.applies(s)}
        for command in conversions.COMMANDS
    }
    assert applied["MP3 320"] == {sample.file_name for sample in samples.SAMPLES}
    assert applied["Keep as m4a"] == {"aac.m4a", "aac-fragmented.m4a", "aac.ts"}
    assert applied["yt-dlp HLS fixup"] == {"aac-fragmented.m4a", "aac.ts"}
    assert applied["yt-dlp M4A fixup"] == {"aac-fragmented.m4a"}


def test_an_output_is_named_after_its_sample_and_command() -> None:
    flac = next(sample for sample in samples.SAMPLES if sample.file_name == "flac.flac")
    command = next(command for command in conversions.COMMANDS if command.name == "MP3 V0")
    assert conversions.output_name(flac, command) == "flac-mp3-v0.mp3"


def test_the_outcome_table_marks_failures_and_keeps_pipes_out_of_cells() -> None:
    results = [
        common.NamedOutcome("FLAC", "MP3 320", common.Outcome(True, "mp3, 320 kbps", 0.05)),
        common.NamedOutcome("WAV", "MP3 V0", common.Outcome(False, "exit 1 | bad", 0.5)),
    ]
    rows = common.outcome_table(results, "Source")
    assert rows[2] == "| FLAC | MP3 320 | ok: mp3, 320 kbps | 0.05 s |"
    assert rows[3] == "| WAV | MP3 V0 | FAILED: exit 1 / bad | 0.50 s |"


def test_the_summary_counts_the_failed_checks() -> None:
    passed = common.NamedOutcome("a", "b", common.Outcome(True, ""))
    failed = common.NamedOutcome("a", "b", common.Outcome(False, ""))
    assert check_build.summary_line([passed, passed]) == "All 2 checks passed."
    assert check_build.summary_line([passed, failed]) == "1 of 2 checks failed."


def test_a_bitrate_mode_is_named_without_its_class() -> None:
    assert conversions.mode_name(conversions.BitrateMode.VBR) == "VBR"


@pytest.fixture(scope="module")
def mp3s(ffmpeg_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """A WAV sample made into an MP3 by each of the app's MP3 commands, by command name."""
    folder = tmp_path_factory.mktemp("mp3s")
    wav = next(sample for sample in samples.SAMPLES if sample.file_name == "wav16.wav")
    src = samples.make_sample(ffmpeg_exe, wav, folder, samples.make_cover(ffmpeg_exe, folder))
    made = {}
    for command in conversions.COMMANDS:
        if command.suffix == ".mp3":
            made[command.name] = folder / conversions.output_name(wav, command)
            command.run(ffmpeg_exe, src, made[command.name])
    return made


@pytest.mark.parametrize(
    ("name", "other", "problem"),
    [
        ("MP3 320", "MP3 V0", "bitrate mode VBR, not CBR"),
        ("MP3 V0", "MP3 320", "bitrate mode CBR, not VBR"),
    ],
)
def test_each_mp3_check_passes_its_own_file_and_names_the_mode_of_the_other(
    mp3s: dict[str, Path], name: str, other: str, problem: str
) -> None:
    command = next(command for command in conversions.COMMANDS if command.name == name)
    assert command.check(mp3s[name]) == []
    assert any(problem in found for found in command.check(mp3s[other]))


def fail_with(message: str | None) -> Callable[[Sequence[str]], None]:
    """A stand-in for run_ffmpeg that fails with this message, or succeeds when it's None."""

    def run(command: Sequence[str]) -> None:
        if message is not None:
            raise common.CommandFailed(message)

    return run


@pytest.mark.parametrize(
    ("message", "passed"),
    [
        ("exit code 251: [tls @ 0x1] Unable to negotiate TLS/SSL session: -9807", True),
        ("exit code 1: [tcp @ 0x1] Connection to tcp://127.0.0.1:1 failed: refused", False),
        (None, False),
    ],
    ids=["tls-error", "other-error", "accepted"],
)
def test_only_a_tls_error_counts_as_refusing_the_certificate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, message: str | None, passed: bool
) -> None:
    monkeypatch.setattr(network, "run_ffmpeg", fail_with(message))
    outcome = network.certificate_refusal(
        tmp_path / "ffmpeg", "https://127.0.0.1:1/a.m4a", tmp_path
    )
    assert outcome.passed is passed


def test_an_aes_128_playlist_served_locally_downloads_through_yt_dlp(
    ffmpeg_exe: Path, tmp_path: Path
) -> None:
    served = tmp_path / "served"
    playlist = samples.make_playlist(ffmpeg_exe, served / "hls-aes", key=network.KEY)
    assert "METHOD=AES-128" in playlist.read_text(encoding="utf-8")
    with network.local_server(served) as base:
        runner = network.yt_dlp_download(f"{base}/hls-aes/playlist.m3u8", "m3u8")
        dst = tmp_path / "out.m4a"
        outcome = common.run_and_check(runner, common.aac_m4a_problems, ffmpeg_exe, playlist, dst)
    assert outcome.passed, outcome.detail
