"""The sample files the ffmpeg build checks convert, made with a full reference ffmpeg."""

from dataclasses import dataclass
from pathlib import Path

from check_common import SECONDS, run_ffmpeg

SINE = f"sine=frequency=440:sample_rate=44100:duration={SECONDS}"
QUIET = ("-hide_banner", "-loglevel", "error", "-nostdin", "-y")
AAC = ("-c:a", "aac", "-b:a", "128k")


@dataclass(frozen=True)
class Sample:
    """A source file to convert, and the reference ffmpeg options that make it."""

    file_name: str
    label: str
    make_args: tuple[str, ...]
    # One of the seven source types T-004 tested.
    in_t004: bool = False
    # Embed a cover, which ffmpeg sees as a video stream the commands must leave out.
    cover: bool = False
    is_aac: bool = False
    # Downloaded over HLS, so yt-dlp's HLS fixup runs on it.
    from_hls: bool = False
    # Downloaded from YouTube's DASH formats, so yt-dlp's M4A fixup runs on it.
    from_dash: bool = False


SAMPLES = (
    Sample("aac.m4a", "AAC in M4A", AAC, in_t004=True, cover=True, is_aac=True),
    Sample("opus.webm", "Opus in WebM", ("-c:a", "libopus", "-b:a", "96k"), in_t004=True),
    Sample("opus.opus", "Opus in Ogg", ("-c:a", "libopus", "-b:a", "96k"), in_t004=True),
    Sample("flac.flac", "FLAC", ("-c:a", "flac"), in_t004=True, cover=True),
    Sample("wav16.wav", "16-bit WAV", ("-c:a", "pcm_s16le"), in_t004=True),
    Sample("wav24.wav", "24-bit WAV", ("-c:a", "pcm_s24le"), in_t004=True),
    Sample("alac.m4a", "ALAC in M4A", ("-c:a", "alac"), in_t004=True, cover=True),
    # Beyond T-004's seven: the other things a download can be.
    Sample("mp3.mp3", "MP3", ("-c:a", "libmp3lame", "-b:a", "128k"), cover=True),
    Sample("vorbis.ogg", "Vorbis in Ogg", ("-c:a", "libvorbis", "-q:a", "4")),
    Sample("aiff.aiff", "16-bit AIFF", ("-c:a", "pcm_s16be")),
    Sample("float.wav", "32-bit float WAV", ("-c:a", "pcm_f32le")),
    Sample(
        "aac-fragmented.m4a",
        "AAC in fragmented MP4, as YouTube's DASH and newer HLS give it",
        (*AAC, "-movflags", "+frag_keyframe+empty_moov"),
        is_aac=True,
        from_hls=True,
        from_dash=True,
    ),
    Sample("aac.ts", "AAC in MPEG-TS, as older HLS gives it", AAC, is_aac=True, from_hls=True),
)


def make_cover(reference: Path, folder: Path) -> Path:
    """A small PNG for the samples that carry a cover."""
    cover = folder / "cover.png"
    lavfi = ("-f", "lavfi", "-i", "color=c=red:s=16x16", "-frames:v", "1")
    run_ffmpeg([str(reference), *QUIET, *lavfi, str(cover)])
    return cover


def make_sample(reference: Path, sample: Sample, folder: Path, cover: Path) -> Path:
    """Write SECONDS of a stereo tone with a title, encoded as the sample says."""
    dest = folder / sample.file_name
    command = [str(reference), *QUIET, "-f", "lavfi", "-i", SINE]
    if sample.cover:
        command += ["-i", str(cover), "-map", "0:a", "-map", "1:v"]
        command += ["-c:v", "copy", "-disposition:v:0", "attached_pic"]
    command += ["-ac", "2", "-metadata", "title=Sample title", *sample.make_args, str(dest)]
    run_ffmpeg(command)
    return dest


def make_streamable_m4a(reference: Path, dest: Path) -> Path:
    """Write SECONDS of AAC as an M4A with its index first, so it plays without range requests."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = [str(reference), *QUIET, "-f", "lavfi", "-i", SINE, "-ac", "2", *AAC]
    run_ffmpeg([*command, "-movflags", "+faststart", str(dest)])
    return dest


def make_playlist(reference: Path, folder: Path, key: bytes | None = None) -> Path:
    """Write SECONDS of AAC as a VOD HLS playlist of one-second MPEG-TS segments.

    With a key, the segments are AES-128 encrypted, and the playlist names the key as key.bin
    beside it.
    """
    folder.mkdir(parents=True, exist_ok=True)
    playlist = folder / "playlist.m3u8"
    command = [str(reference), *QUIET, "-f", "lavfi", "-i", SINE, "-ac", "2", *AAC]
    command += ["-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod"]
    command += ["-hls_segment_filename", str(folder / "segment%d.ts")]
    if key is not None:
        key_file = folder / "key.bin"
        key_file.write_bytes(key)
        # The key's URI in the playlist, then where the reference reads it from.
        key_info = folder / "key-info.txt"
        key_info.write_text(f"key.bin\n{key_file}\n", encoding="utf-8")
        command += ["-hls_key_info_file", str(key_info)]
    run_ffmpeg([*command, str(playlist)])
    return playlist
