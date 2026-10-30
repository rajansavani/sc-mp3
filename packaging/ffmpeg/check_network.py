"""yt-dlp's ffmpeg downloader with our build: HLS from disk and over HTTP, and HTTPS.

yt-dlp hands ffmpeg the HLS playlists its own downloader can't handle. Everything is served
from 127.0.0.1, HTTPS with a self-signed certificate, so no check needs the internet.
"""

import contextlib
import functools
import http.server
import ssl
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path

from check_common import (
    CommandFailed,
    NamedOutcome,
    Outcome,
    Runner,
    aac_m4a_problems,
    run_and_check,
    run_ffmpeg,
)
from check_samples import make_playlist, make_streamable_m4a

# Any 16 bytes make an AES-128 key.
KEY = bytes(range(16))
# ffmpeg checks certificates by default. It passes this option only to the URL it opens itself,
# not to an HLS playlist's segments, so HTTPS is checked with a single file.
NO_CERTIFICATE_CHECK = ("-tls_verify", "0")
# How ffmpeg's TLS layer starts its errors, with Schannel and with Secure Transport alike.
TLS_ERROR = "[tls @"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Serves a folder without logging each request."""

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextlib.contextmanager
def local_server(folder: Path, tls: ssl.SSLContext | None = None) -> Iterator[str]:
    """Serve folder on 127.0.0.1 over HTTP, or HTTPS with a TLS context, yielding its URL."""
    handler = functools.partial(QuietHandler, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    scheme = "https" if tls else "http"
    try:
        yield f"{scheme}://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def tls_context(certificate: Path, private_key: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    return context


def yt_dlp_download(url: str, protocol: str, input_args: Sequence[str] = ()) -> Runner:
    """Download url with yt-dlp's ffmpeg downloader into dst, ignoring src."""

    def run(ffmpeg: Path, src: Path, dst: Path) -> None:
        # Imported here, as the app does, so yt-dlp loads only when it's used.
        from yt_dlp import YoutubeDL
        from yt_dlp.downloader.external import FFmpegFD

        params: dict[str, object] = {
            "ffmpeg_location": str(ffmpeg),
            "quiet": True,
            "noprogress": True,
            "enable_file_urls": True,
            "external_downloader_args": {"ffmpeg_i": list(input_args)},
        }
        info = {"url": url, "protocol": protocol, "ext": "m4a", "acodec": "aac", "vcodec": "none"}
        with YoutubeDL(params) as ydl:
            succeeded, _ = FFmpegFD(ydl, ydl.params).download(str(dst), info)
        if not succeeded:
            raise RuntimeError("yt-dlp's ffmpeg download failed")

    return run


def certificate_refusal(ffmpeg: Path, url: str, dst: Path) -> Outcome:
    """Whether ffmpeg, checking certificates as it does by default, refuses a self-signed one.

    The download before it, of the same URL with the check off, shows that HTTPS itself works.
    """
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-i", url, "-c", "copy", str(dst)]
    try:
        run_ffmpeg(command)
    except CommandFailed as error:
        # Only a TLS error is a refusal; a dropped connection, say, proves nothing.
        if TLS_ERROR not in str(error):
            return Outcome(False, f"failed, but not in TLS: {error}")
        return Outcome(True, f"refused: {error}")
    return Outcome(False, "ffmpeg accepted a self-signed certificate")


def check_network(
    ffmpeg: Path, reference: Path, folder: Path, certificate: Path, private_key: Path
) -> list[NamedOutcome]:
    """Download HLS playlists and an M4A file the ways yt-dlp's ffmpeg downloader would."""
    served = folder / "served"
    outputs = folder / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    plain = make_playlist(reference, served / "hls")
    make_playlist(reference, served / "hls-aes", key=KEY)
    # Python's file server can't answer range requests, so the M4A must not need them.
    make_streamable_m4a(reference, served / "aac.m4a")

    def download(source: str, check: str, runner: Runner, name: str) -> NamedOutcome:
        outcome = run_and_check(runner, aac_m4a_problems, ffmpeg, plain, outputs / name)
        return NamedOutcome(source, check, outcome)

    results = [
        download(
            "HLS playlist on disk",
            "yt-dlp's ffmpeg download",
            yt_dlp_download(plain.as_uri(), "m3u8"),
            "hls-file.m4a",
        )
    ]
    with local_server(served) as base:
        results.append(
            download(
                "HLS over HTTP",
                "yt-dlp's ffmpeg download",
                yt_dlp_download(f"{base}/hls/playlist.m3u8", "m3u8"),
                "hls-http.m4a",
            )
        )
        results.append(
            download(
                "HLS over HTTP, AES-128",
                "yt-dlp's ffmpeg download",
                yt_dlp_download(f"{base}/hls-aes/playlist.m3u8", "m3u8"),
                "hls-aes.m4a",
            )
        )
    with local_server(served, tls_context(certificate, private_key)) as base:
        results.append(
            download(
                "M4A over HTTPS",
                "yt-dlp's ffmpeg download, certificate check off",
                yt_dlp_download(f"{base}/aac.m4a", "https", NO_CERTIFICATE_CHECK),
                "https.m4a",
            )
        )
        refusal = certificate_refusal(ffmpeg, f"{base}/aac.m4a", outputs / "https-checked.m4a")
        results.append(NamedOutcome("M4A over HTTPS", "ffmpeg with its certificate check", refusal))
    return results
