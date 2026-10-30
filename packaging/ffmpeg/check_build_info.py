"""What the ffmpeg build says about itself: its license, configuration, components and size."""

import os
import subprocess
import zipfile
from pathlib import Path

from check_common import Outcome

from scmp3.core import paths
from scmp3.core.errors import ScmpError

MB = 1_000_000
# T-004's sizes for the builds we ship now: which build, ffmpeg unpacked and ffmpeg zipped, in MB.
T004_SIZES = {
    "windows-x64": ("gyan.dev essentials 9.0.2", 105.4, 37.4),
    "macos-arm64": ("Martin Riedl 9.0.2", 66.3, 28.4),
    "macos-x64": ("Martin Riedl 9.0.2", 94.8, 33.8),
}
# What ffmpeg -L says, with its line breaks as spaces, for an LGPL 2.1-or-later build. The
# LGPL 3 text names version 3 here, and the GPL texts never name the Lesser license.
LGPL_21_WORDING = (
    "GNU Lesser General Public License as published by the Free Software Foundation; "
    "either version 2.1 of the License, or (at your option) any later version."
)
GPL_WORDING = "GNU General Public License"
# Configure options that would make the build GPL, LGPL 3 or unredistributable.
FORBIDDEN_OPTIONS = ("--enable-gpl", "--enable-version3", "--enable-nonfree")
# The protocols yt-dlp's ffmpeg downloader needs for HLS over HTTPS, AES-128 included.
NETWORK_PROTOCOLS = ("http", "https", "tls", "tcp", "crypto")
# The ffmpeg options that list each kind of component, for the report.
COMPONENT_LISTS = {
    "Encoders": "-encoders",
    "Decoders": "-decoders",
    "Demuxers": "-demuxers",
    "Muxers": "-muxers",
    "Bitstream filters": "-bsfs",
    "Filters": "-filters",
    "Protocols": "-protocols",
}


def ffmpeg_output(ffmpeg: Path, *args: str) -> str:
    """What ffmpeg prints for an informational option like -L or -encoders."""
    command = [str(ffmpeg), "-hide_banner", *args]
    result = subprocess.run(command, capture_output=True, text=True, errors="replace")
    return result.stdout + result.stderr


def version_output(ffmpeg: Path) -> str:
    command = [str(ffmpeg), "-version"]
    return subprocess.run(command, capture_output=True, text=True, errors="replace").stdout


def configuration_line(version_text: str) -> str:
    """The configure options ffmpeg -version reports."""
    for line in version_text.splitlines():
        if line.startswith("configuration:"):
            return line.removeprefix("configuration:").strip()
    return ""


def license_outcome(license_text: str) -> Outcome:
    text = " ".join(license_text.split())
    if GPL_WORDING in text or LGPL_21_WORDING not in text:
        return Outcome(False, "ffmpeg -L doesn't say LGPL 2.1 or later")
    return Outcome(True, "ffmpeg -L says LGPL 2.1 or later")


def configuration_outcome(configuration: str) -> Outcome:
    if not configuration:
        return Outcome(False, "ffmpeg -version printed no configuration line")
    found = [option for option in FORBIDDEN_OPTIONS if option in configuration.split()]
    if found:
        return Outcome(False, f"the configuration has {', '.join(found)}")
    return Outcome(True, f"the configuration has none of {', '.join(FORBIDDEN_OPTIONS)}")


def protocols_outcome(protocols: list[str]) -> Outcome:
    missing = [name for name in NETWORK_PROTOCOLS if name not in protocols]
    if missing:
        return Outcome(False, f"ffmpeg -protocols doesn't list {', '.join(missing)}")
    return Outcome(True, f"ffmpeg -protocols lists {', '.join(NETWORK_PROTOCOLS)}")


def app_resolution_outcome(ffmpeg: Path) -> Outcome:
    """Whether the app, run with this environment, finds the ffmpeg being checked."""
    if not os.environ.get(paths.FFMPEG_ENV):
        return Outcome(
            False, f"{paths.FFMPEG_ENV} isn't set, so the tests would use another ffmpeg"
        )
    try:
        found = paths.ffmpeg_exe()
    except ScmpError as error:
        return Outcome(False, f"the app finds no ffmpeg: {error}")
    if not os.path.samefile(found, ffmpeg):
        return Outcome(False, f"the app finds {found}, not the build being checked")
    return Outcome(True, f"the app finds this build through {paths.FFMPEG_ENV}")


def listed_names(option: str, text: str) -> list[str]:
    """The component names in the output of ffmpeg -encoders, -muxers, -bsfs and so on."""
    lines = text.splitlines()
    if option in {"-bsfs", "-protocols"}:
        # One name per line under a heading; -protocols lists inputs, then outputs.
        names = [line.strip() for line in lines if line.startswith(("  ", "\t")) or line.islower()]
        return sorted({name for name in names if name and ":" not in name})
    if option == "-filters":
        # A filter's line has its flags, its name, then its input and output types, like A->A.
        rows = [line.split() for line in lines]
        return [row[1] for row in rows if len(row) > 2 and "->" in row[2]]
    # Encoders, decoders, muxers and demuxers come after a line of dashes as wide as the flags.
    dashes = next((i for i, line in enumerate(lines) if set(line.strip()) == {"-"}), None)
    if dashes is None:
        return []
    flags_width = len(lines[dashes])
    rows = [line[flags_width:].split() for line in lines[dashes + 1 :]]
    return [row[0] for row in rows if row]


def component_lines(ffmpeg: Path) -> list[str]:
    lines = []
    for label, option in COMPONENT_LISTS.items():
        names = listed_names(option, ffmpeg_output(ffmpeg, option))
        lines.append(f"- {label}: {', '.join(names) or 'none'}")
    return lines


def zipped_size(binary: Path, folder: Path) -> int:
    """The binary's size zipped on its own, as T-004 measured the builds we ship now."""
    archive = folder / f"{binary.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        zipped.write(binary, binary.name)
    size = archive.stat().st_size
    archive.unlink()
    return size


def size_table(target: str, binary: Path, folder: Path) -> list[str]:
    ours = binary.stat().st_size / MB
    zipped = zipped_size(binary, folder) / MB
    rows = [
        "| Build | ffmpeg | ffmpeg alone, zipped |",
        "|---|---|---|",
        f"| Ours, {target} | {ours:.1f} MB | {zipped:.1f} MB |",
    ]
    if target in T004_SIZES:
        name, unpacked, packed = T004_SIZES[target]
        rows.append(f"| T-004: {name} | {unpacked:.1f} MB | {packed:.1f} MB |")
    return rows
