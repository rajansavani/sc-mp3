"""The spike's tiny app: a pywebview window plus yt-dlp checks, writing a JSON report."""

import argparse
import ctypes
import functools
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import Any

import spike_checks
import spike_console
import spike_engine

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_NO_METADATA = 3
EXIT_CRASHED = 4
EXIT_DEADLINE = 5
OVERLAY_CHECK_TIMEOUT_SECONDS = 60
RESTART_GRACE_SECONDS = 15

# Seconds between 1601-01-01 (the FILETIME epoch) and 1970-01-01.
FILETIME_EPOCH_OFFSET = 11_644_473_600

PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Frozen overlay spike</title>
<style>body { font: 15px system-ui, sans-serif; margin: 32px; }</style></head>
<body><h1>Frozen overlay spike</h1><p id="status">Running checks.</p></body>
</html>
"""


class Report:
    """Collects results from several threads and writes them as JSON."""

    def __init__(self, path: Path | None, process_started: float) -> None:
        self.path = path
        self.process_started = process_started
        self.failed = False
        self.deadline_reached = False
        self.data: dict[str, Any] = {"argv": sys.argv[1:], "timings": {}}
        self._lock = threading.Lock()

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self.data[key] = value

    def mark(self, name: str) -> None:
        """Record how many seconds after process creation something happened."""
        with self._lock:
            self.data["timings"][name] = round(time.time() - self.process_started, 3)

    def fail(self, message: str) -> None:
        with self._lock:
            self.failed = True
            self.data.setdefault("failures", []).append(message)

    def write(self) -> None:
        if self.path is None:
            return
        with self._lock:
            text = json.dumps(self.data, indent=2, default=str)
        self.path.write_text(text, encoding="utf-8")


def process_start_time() -> float:
    """Return when this process was created, in seconds since the epoch."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    filetime_pointer = ctypes.POINTER(wintypes.FILETIME)
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, *[filetime_pointer] * 4]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), *map(ctypes.byref, times)):
        raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
    created = times[0]
    ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
    return ticks / 10_000_000 - FILETIME_EPOCH_OFFSET


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Read the options the spike's driver passes."""
    parser = argparse.ArgumentParser(prog="frozen-overlay", description=__doc__)
    parser.add_argument("--version", action="store_true", help="print sc-mp3's version and exit")
    parser.add_argument("--attach-console", action="store_true", help="use the parent's console")
    parser.add_argument("--report", type=Path, help="write the results to this JSON file")
    parser.add_argument("--overlay", type=Path, help="yt-dlp zipapp to put first on sys.path")
    parser.add_argument("--install-overlay", type=Path, help="check a new overlay and mark it")
    # The app passes these two to itself: the child side of an install, and a restart's reason.
    parser.add_argument("--check-overlay", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--restarted", metavar="REASON", help=argparse.SUPPRESS)
    parser.add_argument("--import-all", action="store_true", help="import every yt_dlp module")
    parser.add_argument("--requests-check", metavar="URL", help="send requests to this URL")
    parser.add_argument("--soundcloud", metavar="URL", help="extract this SoundCloud track")
    parser.add_argument("--youtube", metavar="URL", help="extract this YouTube video")
    parser.add_argument("--deno", type=Path, help="deno.exe to pass through js_runtimes")
    parser.add_argument("--youtube-client", action="append", default=[], metavar="NAME")
    parser.add_argument("--remote-components", action="append", default=[], metavar="NAME")
    parser.add_argument("--cache-dir", type=Path, help="yt-dlp cache folder; none by default")
    parser.add_argument("--no-window", action="store_true", help="run the checks without a window")
    parser.add_argument("--close-after", type=float, default=2.0, metavar="SECONDS")
    parser.add_argument("--deadline", type=float, default=150.0, metavar="SECONDS")
    return parser.parse_args(argv)


def check_scmp3(report: Report, print_version: bool) -> bool:
    """Import scmp3, which reads its version from the package metadata, and maybe print it."""
    try:
        import scmp3
    except Exception as error:
        report.set("scmp3", {"ok": False, "error": f"{type(error).__name__}: {error}"})
        print(f"Couldn't read the sc-mp3 version: {error}", file=sys.stderr)
        return False
    report.set("scmp3", {"ok": True, "version": scmp3.__version__})
    if print_version:
        print(scmp3.__version__)
    return True


def wants_engine(options: argparse.Namespace) -> bool:
    checks = (options.import_all, options.requests_check, options.soundcloud, options.youtube)
    return bool(options.overlay or any(checks))


def extraction_params(options: argparse.Namespace) -> dict[str, Any]:
    """Build the YoutubeDL options shared by the SoundCloud and YouTube checks."""
    params: dict[str, Any] = {"cachedir": str(options.cache_dir) if options.cache_dir else False}
    if options.deno:
        params["js_runtimes"] = {"deno": {"path": str(options.deno)}}
    # The default clients may need no JS at all, so tests of the EJS scripts pick clients that do.
    if options.youtube_client:
        params["extractor_args"] = {"youtube": {"player_client": list(options.youtube_client)}}
    if options.remote_components:
        params["remote_components"] = list(options.remote_components)
    return params


def load_engine(options: argparse.Namespace, report: Report) -> None:
    """Set up the overlay before anything imports yt_dlp, and record which engine won."""
    state = spike_engine.load_engine(options.overlay)
    report.mark("engine_loaded")
    report.set("engine_state", asdict(state))
    report.set("engine", spike_checks.describe_engine())


def own_command() -> list[str]:
    """Return the command that starts this app again."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.abspath(sys.argv[0])]


def restart_app(options: argparse.Namespace, report: Report, reason: str) -> int:
    """Start the app again, now that the overlay is marked bad, and hand the run to it."""
    # The new process writes the report.
    report.path = None
    command = [*own_command(), *sys.argv[1:], "--restarted", reason]
    # The real app would exit here. The spike waits, so its driver gets one exit code.
    # The new process has the same deadline, so give it a little longer than that.
    timeout = options.deadline + RESTART_GRACE_SECONDS
    return subprocess.run(command, timeout=timeout, check=False).returncode


def check_overlay_here(overlay: Path) -> int:
    """Be the child of an install: import the overlay with no fallback and print the result."""
    try:
        version = spike_engine.check_overlay(overlay)
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_CHECK_FAILED
    print(version)
    return EXIT_OK


def install_overlay(overlay: Path, report: Report) -> int:
    """Check a newly downloaded overlay in a child process, then mark it good or bad."""
    command = [*own_command(), "--check-overlay", str(overlay)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=OVERLAY_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        status, detail = "bad", f"The check took longer than {OVERLAY_CHECK_TIMEOUT_SECONDS} s."
    else:
        status = "good" if completed.returncode == 0 else "bad"
        output = completed.stdout if status == "good" else completed.stderr
        detail = output.strip() or f"exit code {completed.returncode}"
    spike_engine.write_status(overlay, status, detail)
    # This process never imports the overlay, so it has nothing to undo.
    loaded_here = len(spike_engine.engine_module_names())
    report.set("install", {"status": status, "detail": detail, "engine_modules_here": loaded_here})
    return EXIT_OK if status == "good" else EXIT_CHECK_FAILED


def run_engine_checks(options: argparse.Namespace, report: Report) -> None:
    """Run the yt-dlp checks the options ask for."""
    if options.import_all:
        result = spike_checks.import_all_submodules()
        report.set("import_all", result)
        if result["failures"]:
            report.fail(f"{len(result['failures'])} yt_dlp modules failed to import")
    if options.requests_check:
        result = spike_checks.requests_check(options.requests_check)
        report.set("requests_check", result)
        if not result["ok"]:
            report.fail("requests failed")
    for site, url in (("soundcloud", options.soundcloud), ("youtube", options.youtube)):
        if not url:
            continue
        result = spike_checks.extract(url, extraction_params(options))
        report.set(site, result)
        if not result["ok"]:
            report.fail(f"{site} extraction failed")
    if options.soundcloud:
        # The track extractor doesn't impersonate, so check curl_cffi with a request of our own.
        result = spike_checks.impersonated_request(options.soundcloud)
        report.set("impersonation", result)
        if "error" in result:
            report.fail("impersonated request failed")
    report.mark("checks_done")


def describe_pythonnet() -> dict[str, Any]:
    """Report the .NET runtime pythonnet loaded for pywebview."""
    import clr
    import pythonnet
    from System import Environment

    info = pythonnet.get_runtime_info()
    return {
        "pythonnet_version": getattr(clr, "__version__", None),
        "runtime_kind": getattr(info, "kind", None),
        "runtime_version": getattr(info, "version", None),
        "environment_version": str(Environment.Version),
    }


def webview2_version() -> str:
    """Ask WebView2 for the runtime version it uses."""
    from Microsoft.Web.WebView2.Core import CoreWebView2Environment

    return str(CoreWebView2Environment.GetAvailableBrowserVersionString())


def describe_window(window: Any) -> dict[str, Any]:
    """Report the renderer, the browser engine and the .NET runtime behind the window."""
    import webview

    try_text = spike_checks.try_text
    return {
        "renderer": webview.renderer,
        "user_agent": try_text(lambda: window.evaluate_js("navigator.userAgent")),
        "is_webview2": try_text(
            lambda: window.evaluate_js("Boolean(window.chrome && window.chrome.webview)")
        ),
        "webview2_version": try_text(webview2_version),
        "pythonnet": try_text(describe_pythonnet),
    }


def set_status(window: Any, text: str) -> None:
    window.evaluate_js(f"document.getElementById('status').textContent = {json.dumps(text)}")


def window_worker(
    window: Any, options: argparse.Namespace, report: Report, work: Callable[[], None] | None
) -> None:
    """Wait for the page, record what's behind it, run the checks, then close the window."""
    try:
        if not window.events.loaded.wait(options.deadline):
            report.fail("the window never loaded")
            return
        report.set("window", describe_window(window))
        if work:
            work()
        set_status(window, "Checks finished. The window closes by itself.")
        time.sleep(options.close_after)
    except Exception as error:
        report.fail(f"window worker: {type(error).__name__}: {error}")
    finally:
        window.destroy()


def close_at_deadline(window: Any, report: Report) -> None:
    """Close a run that took too long, keeping what it found so far."""
    report.deadline_reached = True
    report.fail("deadline reached")
    report.set("exit_code", EXIT_DEADLINE)
    report.write()
    if window.events.shown.is_set():
        try:
            window.destroy()
            return
        except Exception as error:
            report.fail(f"destroy at the deadline: {type(error).__name__}: {error}")
            report.write()
    # destroy() waits 20 s for a window that never showed and then raises, so stop here.
    os._exit(EXIT_DEADLINE)


def run_window(
    options: argparse.Namespace, report: Report, work: Callable[[], None] | None
) -> None:
    """Open the window, run the checks beside it, and return once it closes."""
    import webview

    window = webview.create_window("Frozen overlay spike", html=PAGE, width=640, height=360)
    window.events.shown += lambda: report.mark("window_shown")
    window.events.loaded += lambda: report.mark("window_loaded")
    worker = threading.Thread(target=window_worker, args=(window, options, report, work))
    worker.daemon = True
    deadline = threading.Timer(options.deadline, close_at_deadline, (window, report))
    deadline.daemon = True
    worker.start()
    deadline.start()
    webview.start(private_mode=True)
    deadline.cancel()
    report.mark("window_closed")


def run(options: argparse.Namespace, report: Report) -> int:
    """Do what the options ask and return the exit code."""
    if options.check_overlay:
        return check_overlay_here(options.check_overlay)
    report.set(
        "streams", {"stdout_is_none": sys.stdout is None, "stderr_is_none": sys.stderr is None}
    )
    if options.attach_console:
        report.set("console", spike_console.attach_parent_console())
    if not check_scmp3(report, print_version=options.version):
        return EXIT_NO_METADATA
    if options.version:
        return EXIT_OK
    report.set("frozen", getattr(sys, "frozen", False))
    report.set("restarted_because", options.restarted)
    if options.install_overlay:
        return install_overlay(options.install_overlay, report)
    work = None
    if wants_engine(options):
        try:
            load_engine(options, report)
        except spike_engine.OverlayFailedError as error:
            if options.restarted:
                raise
            return restart_app(options, report, str(error))
        work = functools.partial(run_engine_checks, options, report)
    if options.no_window:
        if work:
            work()
    else:
        run_window(options, report, work)
    report.mark("finished")
    if report.deadline_reached:
        return EXIT_DEADLINE
    return EXIT_CHECK_FAILED if report.failed else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the spike app and return the exit code."""
    report = Report(None, process_start_time())
    report.mark("main_entered")
    # Plugins installed for someone's own yt-dlp would otherwise load here too.
    os.environ["YTDLP_NO_PLUGINS"] = "1"
    options = parse_args(argv)
    report.path = options.report
    try:
        code = run(options, report)
    except Exception as error:
        # An uncaught error would open PyInstaller's traceback dialog and wait for a click.
        report.fail(f"crashed: {type(error).__name__}: {error}")
        report.set("traceback", traceback.format_exc())
        code = EXIT_CRASHED
    report.set("exit_code", code)
    try:
        report.write()
    except OSError as error:
        # Left uncaught, this would also open PyInstaller's traceback dialog.
        print(f"Couldn't write the report: {error}", file=sys.stderr)
        return EXIT_CRASHED
    return code


if __name__ == "__main__":
    sys.exit(main())
