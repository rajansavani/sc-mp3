"""Run the frozen spike app through every scenario, with timeouts, and save what each reports."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import launchers

SPIKE_DIR = Path(__file__).absolute().parent
DIST_DIR = SPIKE_DIR / "dist"
MANIFEST = SPIKE_DIR / "downloads" / "manifest.json"
RESULTS_DIR = SPIKE_DIR / "results"

SOUNDCLOUD_URL = "https://soundcloud.com/ethmusic/lostin-powers-she-so-heavy"
YOUTUBE_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
# These clients need YouTube's JS challenges solved. The default clients may need none.
JS_CLIENTS = ["--youtube-client", "mweb", "--youtube-client", "web_embedded"]
# The app's own deadline comes this long before the driver's timeout, so it can still report.
DEADLINE_MARGIN_SECONDS = 30
# Broken and mismatched copies of the overlay. "missing" is never created, like a deleted file.
# The install_* copies get a status file from --install-overlay, so they're kept apart.
OVERLAY_VARIANTS = (
    "missing",
    "garbage",
    "truncated",
    "broken",
    "no_ejs",
    "stale_ejs",
    "install_good",
    "install_garbage",
    "install_broken",
)
EMOJI = "\N{GRINNING FACE}"


@dataclass
class Scenario:
    """One way of starting the frozen app, and the question it answers."""

    name: str
    question: str
    args: list[str]
    mode: str = "report"
    exe: str = "frozen-overlay"
    timeout: float = 180
    repeat: int = 1
    # Run a copy of the bundle placed so that its longest file path has this many UTF-16 units.
    longest_path: int | None = None
    # Put one character outside the BMP in the copy's folder name, which counts as two units.
    emoji: bool = False
    # Run a copy of the bundle in a short folder with this name.
    folder: str | None = None


def youtube(overlay: str | None, *extra: str) -> list[str]:
    """Build the arguments for a YouTube check with Deno, through an overlay or not."""
    through = ["--overlay", overlay] if overlay else []
    return [*through, "--youtube", YOUTUBE_URL, "--deno", "{deno}", *extra]


# After any fallback, requests must still work. A second import of yt-dlp breaks it.
REQUESTS = ["--requests-check", "https://soundcloud.com/"]
VERSION = ["--version"]
ATTACH = ["--version", "--attach-console"]
NO_COPY_METADATA = "frozen-overlay-no-copy-metadata"
NO_METADATA = "frozen-overlay-no-metadata"
NO_EXCLUDES = "frozen-overlay-no-excludes"
SOUNDCLOUD = ["--soundcloud", SOUNDCLOUD_URL]

SCENARIOS = [
    Scenario("window", "1", [], timeout=60, repeat=5),
    # A copy made just before the launch, like a release that was just unzipped, started twice.
    Scenario("fresh-copy", "1", [], timeout=60, repeat=2, longest_path=230),
    # The app's deadline closes a shown window, and ends a run whose window never showed.
    Scenario("deadline-shown", "1", ["--close-after", "30", "--deadline", "5"], timeout=60),
    Scenario("deadline-not-shown", "1", ["--deadline", "0.1"], timeout=60),
    Scenario("no-excludes-window", "5", [], "report", NO_EXCLUDES, 60),
    Scenario("version-pipe", "6", VERSION, "pipe", timeout=60),
    Scenario("version-console-cmd", "6", VERSION, "console-cmd", timeout=60),
    Scenario("version-attach-cmd", "6", ATTACH, "console-cmd", timeout=60),
    Scenario("version-attach-powershell", "6", ATTACH, "console-powershell", timeout=60),
    Scenario("version-powershell-pipe", "6", VERSION, "powershell-pipe", timeout=60),
    Scenario("version-powershell-capture", "6", VERSION, "powershell-capture", timeout=60),
    Scenario("version-start-process", "6", VERSION, "start-process", timeout=60),
    Scenario("version-start-process-redirect", "6", VERSION, "start-process-redirect", timeout=60),
    # The stripped build exits with 3, which shows whether each launcher passes the code on.
    Scenario("exit3-console-cmd", "6", VERSION, "console-cmd", NO_METADATA, 60),
    Scenario("exit3-powershell-pipe", "6", VERSION, "powershell-pipe", NO_METADATA, 60),
    Scenario("exit3-powershell-capture", "6", VERSION, "powershell-capture", NO_METADATA, 60),
    Scenario("exit3-start-process", "6", VERSION, "start-process", NO_METADATA, 60),
    Scenario("no-copy-metadata-version", "7", VERSION, "pipe", NO_COPY_METADATA, 60),
    Scenario("no-metadata-version", "7", VERSION, "pipe", NO_METADATA, 60),
    Scenario("no-metadata-report", "7", ["--no-window"], "report", NO_METADATA, 60),
    Scenario("bundled", "2", ["--import-all", *REQUESTS]),
    Scenario("overlay", "2", ["--overlay", "{official}", "--import-all", *REQUESTS]),
    # Caught before any overlay code runs, so the bundled copy is imported once.
    Scenario("overlay-missing", "2", ["--overlay", "{missing}", *REQUESTS]),
    Scenario("overlay-garbage", "2", ["--overlay", "{garbage}", *REQUESTS]),
    Scenario("overlay-truncated", "2", ["--overlay", "{truncated}", *REQUESTS]),
    # Fails after its code ran, so the app marks it bad and starts again.
    Scenario("overlay-broken-module", "2", ["--overlay", "{broken}", *REQUESTS]),
    # A new overlay is checked in a child process before it's marked good.
    Scenario("install-good", "2", ["--install-overlay", "{install_good}"], timeout=90),
    Scenario("install-garbage", "2", ["--install-overlay", "{install_garbage}"], timeout=90),
    Scenario("install-broken", "2", ["--install-overlay", "{install_broken}"], timeout=90),
    Scenario("installed-good", "2", ["--overlay", "{install_good}", *REQUESTS]),
    Scenario("installed-bad", "2", ["--overlay", "{install_broken}", *REQUESTS]),
    Scenario("soundcloud-bundled", "4", SOUNDCLOUD),
    Scenario("soundcloud-overlay", "4", ["--overlay", "{official}", *SOUNDCLOUD]),
    # curl_cffi's DLL has the bundle's longest path. Loading it worked up to 250 units.
    Scenario("path-250", "4", SOUNDCLOUD, longest_path=250),
    Scenario("path-251", "4", SOUNDCLOUD, longest_path=251),
    Scenario("path-250-emoji", "4", SOUNDCLOUD, longest_path=250, emoji=True),
    Scenario("path-251-emoji", "4", SOUNDCLOUD, longest_path=251, emoji=True),
    # curl_cffi's requests fail when the path has characters outside the ANSI code page.
    Scenario("name-in-code-page", "4", SOUNDCLOUD, folder="caf\N{LATIN SMALL LETTER E WITH ACUTE}"),
    Scenario("name-outside-code-page", "4", SOUNDCLOUD, folder="жук"),
    # Deeper still, .NET can't load pywebview's WebView2 DLLs, and then can't start at all.
    Scenario("path-283", "1", [], timeout=60, longest_path=283),
    Scenario("path-284", "1", [], timeout=60, longest_path=284),
    Scenario("path-289", "1", [], timeout=60, longest_path=289),
    Scenario("youtube-bundled-default", "3", youtube(None)),
    Scenario("youtube-bundled-js", "3", youtube(None, *JS_CLIENTS)),
    Scenario("youtube-overlay-js", "3", youtube("{official}", *JS_CLIENTS)),
    Scenario("youtube-overlay-no-ejs-js", "3", youtube("{no_ejs}", *JS_CLIENTS)),
    Scenario("youtube-stale-ejs-js", "3", youtube("{stale_ejs}", *JS_CLIENTS)),
    Scenario(
        "youtube-stale-ejs-remote-js",
        "3",
        youtube("{stale_ejs}", *JS_CLIENTS, "--remote-components", "ejs:github"),
    ),
]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_downloads() -> dict[str, Path]:
    """Read what fetch.py downloaded, and check each file's SHA-256 again."""
    if not MANIFEST.exists():
        raise SystemExit("No downloads/manifest.json yet. Run fetch.py first.")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {}
    for name, entry in manifest.items():
        checked = SPIKE_DIR / entry.get("checked_file", entry["path"])
        path = SPIKE_DIR / entry["path"]
        if not path.exists() or sha256_of(checked) != entry["sha256"]:
            raise SystemExit(f"{entry['path']} is missing or changed. Run fetch.py again.")
        paths[name] = path
    return paths


def rewrite_zip(
    source: Path, target: Path, change: Callable[[str, bytes], bytes | None], extra: dict
) -> None:
    """Copy a zip entry by entry, letting change() edit or drop entries, then add extra ones."""
    target.parent.mkdir(parents=True)
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as new:
        for name in old.namelist():
            data = change(name, old.read(name))
            if data is not None:
                new.writestr(name, data)
        for name, data in extra.items():
            new.writestr(name, data)


def wheel_package_files(wheel: Path, package: str) -> dict[str, bytes]:
    """Read one package's files out of a wheel."""
    with zipfile.ZipFile(wheel) as archive:
        return {
            name: archive.read(name)
            for name in archive.namelist()
            if name.startswith(f"{package}/") and not name.endswith("/")
        }


def break_module(name: str, data: bytes) -> bytes:
    """Make yt_dlp/YoutubeDL.py fail after the modules before it have loaded."""
    if name != "yt_dlp/YoutubeDL.py":
        return data
    return data + b"\nraise RuntimeError('Simulated corruption in the overlay')\n"


def drop_ejs(name: str, data: bytes) -> bytes | None:
    return None if name.startswith("yt_dlp_ejs/") else data


def build_overlays(official: Path, old_wheel: Path, folder: Path) -> dict[str, Path]:
    """Make the broken and mismatched copies of the official zipapp, each in its own folder."""
    data = official.read_bytes()
    paths = {name: folder / name / "yt-dlp" for name in OVERLAY_VARIANTS}
    for name in ("garbage", "truncated", "install_good", "install_garbage"):
        paths[name].parent.mkdir(parents=True)
    garbage = os.urandom(len(data))
    paths["garbage"].write_bytes(garbage)
    paths["install_garbage"].write_bytes(garbage)
    paths["truncated"].write_bytes(data[: len(data) // 2])
    paths["install_good"].write_bytes(data)
    rewrite_zip(official, paths["broken"], break_module, {})
    rewrite_zip(official, paths["install_broken"], break_module, {})
    rewrite_zip(official, paths["no_ejs"], drop_ejs, {})
    old_ejs = wheel_package_files(old_wheel, "yt_dlp_ejs")
    rewrite_zip(official, paths["stale_ejs"], drop_ejs, old_ejs)
    return paths


def utf16_length(path: Path | str) -> int:
    """Count a path the way Windows does, in UTF-16 code units."""
    return len(str(path).encode("utf-16-le")) // 2


def longest_file_path(folder: Path) -> int:
    return max(utf16_length(path) for path in folder.rglob("*"))


def copy_to_depth(bundle: Path, base: Path, longest: int, emoji: bool) -> Path:
    """Copy a bundle into a folder deep enough that its longest file path has the given length."""
    # pywebview finds its DLLs through realpath(), so count the real path, not a mapped drive.
    base = Path(os.path.realpath(base))
    inside = longest_file_path(bundle) - utf16_length(bundle)
    units = longest - utf16_length(base) - 1 - inside
    if units < 3:
        raise ValueError(f"The spike folder is too deep to place a {longest}-unit path.")
    padding = EMOJI + "p" * (units - 2) if emoji else "p" * units
    target = base / padding
    # Repeated runs of one scenario start the same copy again.
    if not target.exists():
        shutil.copytree(bundle, target)
    return target


def run_scenario(scenario: Scenario, label: str, args: list[str], run_dir: Path) -> dict:
    """Start the app the way the scenario says and collect what came back."""
    exe = DIST_DIR / scenario.exe / f"{scenario.exe}.exe"
    longest = None
    if scenario.folder:
        target = run_dir / "copies" / scenario.name / scenario.folder
        if not target.exists():
            shutil.copytree(exe.parent, target)
        exe = target / exe.name
    if scenario.longest_path:
        try:
            base = run_dir / "copies" / scenario.name
            exe = copy_to_depth(exe.parent, base, scenario.longest_path, scenario.emoji) / exe.name
        except ValueError as error:
            return {"skipped": str(error)}
        longest = longest_file_path(exe.parent)
    command = [str(exe), *args]
    if scenario.mode == "report":
        # A scenario's own --deadline comes later in the command, so it wins.
        deadline = str(scenario.timeout - DEADLINE_MARGIN_SECONDS)
        command = [str(exe), "--deadline", deadline, *args]
    result = launchers.launch(scenario.mode, command, label, run_dir, scenario.timeout)
    return {"longest_path": longest, **result} if longest else result


def condense(result: dict) -> dict:
    """Keep the parts of a result that the README quotes, and drop the full logs."""
    report = result.get("report")
    if not report:
        return result
    short = {key: value for key, value in result.items() if key != "report"}
    kept = ("timings", "streams", "scmp3", "window", "engine_state", "engine", "failures")
    extra = ("import_all", "impersonation", "requests_check", "install", "restarted_because")
    for key in (*kept, *extra):
        if key in report:
            short[key] = report[key]
    for site in ("soundcloud", "youtube"):
        if site in report:
            short[site] = {key: value for key, value in report[site].items() if key != "log"}
    return short


def scrub(value: Any) -> Any:
    """Replace this machine's paths so results can be quoted in the public repo."""
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if not isinstance(value, str):
        return value
    for path, placeholder in ((str(SPIKE_DIR), "<spike>"), (str(Path.home()), "<home>")):
        # Paths also show up inside repr() output, with each backslash doubled.
        value = value.replace(path.replace("\\", "\\\\"), placeholder).replace(path, placeholder)
    return value


def main() -> int:
    """Run the chosen scenarios and write the results under results/<time>/."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="comma-separated scenario names")
    options = parser.parse_args()
    chosen = set(options.only.split(",")) if options.only else None
    downloads = load_downloads()
    run_dir = RESULTS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    overlays = build_overlays(downloads["yt_dlp"], downloads["old_ejs"], run_dir / "overlays")
    paths = {key: str(path) for key, path in overlays.items()}
    paths["official"] = str(downloads["yt_dlp"])
    paths["deno"] = str(downloads["deno"])
    shutil.copy(MANIFEST, run_dir / "downloads.json")
    summary: dict[str, Any] = {}
    for scenario in SCENARIOS:
        if chosen and scenario.name not in chosen:
            continue
        args = [arg.format(**paths) for arg in scenario.args]
        for run in range(1, scenario.repeat + 1):
            label = scenario.name if scenario.repeat == 1 else f"{scenario.name}-{run}"
            print(f"Running {label} (question {scenario.question})", flush=True)
            result = run_scenario(scenario, label, args, run_dir)
            summary[label] = {"question": scenario.question, "args": args, **condense(result)}
            raw = json.dumps(result, indent=2, default=str)
            (run_dir / f"{label}.result.json").write_text(raw, encoding="utf-8")
    text = json.dumps(scrub(summary), indent=2, default=str)
    (run_dir / "summary.json").write_text(text, encoding="utf-8")
    print(f"Results: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
