"""The yt-dlp checks the spike runs in the frozen app, each returning plain data for the report."""

import importlib
import pkgutil
import sys
import time
from typing import Any

# __main__ would run the yt-dlp command line, and __pyinstaller needs PyInstaller itself.
SKIPPED_SUBMODULES = ("yt_dlp.__main__", "yt_dlp.__pyinstaller")
# The parts of yt-dlp's verbose header that answer the spike's questions.
HEADER_PREFIXES = (
    "[debug] yt-dlp version",
    "[debug] Python ",
    "[debug] Optional libraries",
    "[debug] JS runtimes",
    "[debug] Request Handlers",
    "[debug] Plugin directories",
)
# Log lines that show how YouTube's JS challenges were handled.
CHALLENGE_MARKERS = ("jsc", "challenge", "ejs", "remote component", "formats may be missing")


class CapturingLogger:
    """A yt-dlp logger that keeps every line, because a windowed app has no stdout."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def debug(self, message: str) -> None:
        self.lines.append(message)

    def info(self, message: str) -> None:
        self.lines.append(message)

    def warning(self, message: str) -> None:
        self.lines.append(f"WARNING: {message}")

    def error(self, message: str) -> None:
        # yt-dlp's own errors already start with "ERROR:".
        self.lines.append(message if message.startswith("ERROR") else f"ERROR: {message}")


def describe_engine() -> dict[str, Any]:
    """Report the active yt-dlp, the yt-dlp-ejs it sees, and how yt-dlp's updater sees the app."""
    import yt_dlp
    import yt_dlp.update
    import yt_dlp.version
    from yt_dlp.extractor.youtube.jsc._builtin import vendor

    details: dict[str, Any] = {
        "version": yt_dlp.version.__version__,
        "channel": getattr(yt_dlp.version, "CHANNEL", None),
        "file": yt_dlp.__file__,
        "expected_ejs_version": vendor.VERSION,
        "update_variant": yt_dlp.update.detect_variant(),
    }
    try:
        import yt_dlp_ejs

        details["ejs_package_version"] = yt_dlp_ejs.version
        details["ejs_package_file"] = yt_dlp_ejs.__file__
    except ImportError as error:
        details["ejs_package_error"] = repr(error)
    details["curl_cffi"] = probe_curl_cffi()
    return details


def probe_curl_cffi() -> str:
    """Import curl_cffi directly, because yt-dlp hides the reason when it can't."""
    try:
        import curl_cffi
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    return f"curl_cffi {curl_cffi.__version__}"


def import_all_submodules() -> dict[str, Any]:
    """Import every yt_dlp submodule, to catch modules the bundle is missing for this engine."""
    import yt_dlp

    started = time.perf_counter()
    imported = 0
    failures: dict[str, str] = {}
    walk_errors: list[str] = []
    walker = pkgutil.walk_packages(yt_dlp.__path__, "yt_dlp.", onerror=walk_errors.append)
    for info in walker:
        if info.name in SKIPPED_SUBMODULES:
            continue
        try:
            importlib.import_module(info.name)
            imported += 1
        except Exception as error:
            failures[info.name] = f"{type(error).__name__}: {error}"
    return {
        "imported": imported,
        "failures": failures,
        "walk_errors": walk_errors,
        "seconds": round(time.perf_counter() - started, 2),
        "modules_loaded": len([name for name in sys.modules if name.startswith("yt_dlp")]),
    }


def impersonated_request(url: str) -> dict[str, Any]:
    """Fetch a page as Chrome, which only the curl_cffi request handler can do."""
    import yt_dlp
    from yt_dlp.networking import Request
    from yt_dlp.networking.impersonate import ImpersonateTarget

    result: dict[str, Any] = {"url": url}
    with yt_dlp.YoutubeDL({"quiet": True, "logger": CapturingLogger()}) as ydl:
        targets = ydl._get_available_impersonate_targets()
        result["target_count"] = len(targets)
        result["first_chrome_target"] = next(
            (f"{target} via {handler}" for target, handler in targets if target.client == "chrome"),
            None,
        )
        request = Request(url, extensions={"impersonate": ImpersonateTarget("chrome")})
        try:
            with ydl.urlopen(request) as response:
                result["status"] = response.status
                result["response_class"] = f"{type(response).__module__}.{type(response).__name__}"
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {error}"
    return result


def requests_check(url: str) -> dict[str, Any]:
    """Use requests directly and through yt-dlp, which a second import of yt-dlp breaks."""
    import requests
    import yt_dlp
    from yt_dlp.networking import Request

    result: dict[str, Any] = {"url": url}
    result["prepare"] = try_text(lambda: prepare_request(requests, url))
    result["requests_get"] = try_text(lambda: requests.get(url, timeout=30).status_code)
    logger = CapturingLogger()
    with yt_dlp.YoutubeDL({"quiet": True, "logger": logger}) as ydl:
        result["ydl_urlopen"] = try_text(lambda: urlopen_handler(ydl, Request(url)))
    # yt-dlp logs a failing handler as an error and then retries with the next one.
    result["ydl_errors"] = [line for line in logger.lines if line.startswith("ERROR")]
    result["ok"] = (
        result["prepare"] == "ok" and result["requests_get"] == 200 and not result["ydl_errors"]
    )
    return result


def prepare_request(requests: Any, url: str) -> str:
    """Build a request without sending it, which runs urllib3's URL parsing."""
    requests.Request("GET", url).prepare()
    return "ok"


def urlopen_handler(ydl: Any, request: Any) -> str:
    """Send a request through yt-dlp and name the handler class that answered."""
    with ydl.urlopen(request) as response:
        return f"{response.status} via {type(response).__name__}"


def try_text(probe: Any) -> Any:
    """Return the probe's result, or its error as text."""
    try:
        return probe()
    except Exception as error:
        return f"{type(error).__name__}: {error}"


def summarize_info(info: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields of an info dict that show extraction worked."""
    formats = info.get("formats") or []
    return {
        "extractor": info.get("extractor"),
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "format_count": len(formats),
        "format_ids": [fmt.get("format_id") for fmt in formats],
        "chosen_format": info.get("format_id"),
    }


def extract(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run extract_info without downloading, and keep the verbose log."""
    import yt_dlp

    logger = CapturingLogger()
    options = {"logger": logger, "verbose": True, "skip_download": True, "noprogress": True}
    options.update(params)
    started = time.perf_counter()
    result: dict[str, Any] = {"url": url}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        result["ok"] = True
        result["info"] = summarize_info(info)
    except Exception as error:
        result["ok"] = False
        result["error"] = f"{type(error).__name__}: {error}"
    result["seconds"] = round(time.perf_counter() - started, 2)
    result["header"] = [line for line in logger.lines if line.startswith(HEADER_PREFIXES)]
    result["challenge_log"] = [
        line for line in logger.lines if any(marker in line.lower() for marker in CHALLENGE_MARKERS)
    ]
    result["warnings"] = [line for line in logger.lines if line.startswith(("WARNING", "ERROR"))]
    result["log"] = logger.lines
    return result
