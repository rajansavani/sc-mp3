"""Check one of our ffmpeg builds on the system it's for, and write a short Markdown report.

Usage: uv run python packaging/ffmpeg/check_build.py --target windows-x64 --ffmpeg <ours>
    --reference <a full ffmpeg build> --certificate <cert.pem> --private-key <key.pem>
    --out <folder>
The reference makes the sample files, since ours encodes nothing but MP3. Ours then runs every
TECH_SPEC §9 command, yt-dlp's fixups and yt-dlp's ffmpeg downloader, and mutagen reads the
results back. The certificate is a self-signed one for the local HTTPS server.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

# packaging/ffmpeg/ is on sys.path when this file runs as a script.
import check_build_info as info
from check_common import NamedOutcome, outcome_table
from check_conversions import check_conversions
from check_network import check_network


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check one of our ffmpeg builds.")
    parser.add_argument("--target", required=True, help="such as windows-x64")
    parser.add_argument("--ffmpeg", type=Path, required=True, help="the build to check")
    parser.add_argument("--reference", type=Path, required=True, help="a full ffmpeg build")
    parser.add_argument("--certificate", type=Path, required=True, help="for the HTTPS server")
    parser.add_argument("--private-key", type=Path, required=True, help="the certificate's key")
    parser.add_argument("--out", type=Path, required=True, help="where the report goes")
    return parser.parse_args(argv)


def build_checks(ffmpeg: Path, version_text: str, license_text: str) -> list[NamedOutcome]:
    """The checks of what the build says about itself."""
    protocols = info.listed_names("-protocols", info.ffmpeg_output(ffmpeg, "-protocols"))
    outcomes = {
        "ffmpeg -L": info.license_outcome(license_text),
        "Configuration": info.configuration_outcome(info.configuration_line(version_text)),
        "Protocols": info.protocols_outcome(protocols),
        "The app's ffmpeg": info.app_resolution_outcome(ffmpeg),
    }
    return [NamedOutcome("The build", check, outcome) for check, outcome in outcomes.items()]


def summary_line(results: Sequence[NamedOutcome]) -> str:
    failed = sum(not result.outcome.passed for result in results)
    if failed == 0:
        return f"All {len(results)} checks passed."
    return f"{failed} of {len(results)} checks failed."


def report_lines(
    args: argparse.Namespace,
    version_text: str,
    license_text: str,
    sections: dict[str, list[NamedOutcome]],
) -> list[str]:
    everything = [result for results in sections.values() for result in results]
    first_line = version_text.splitlines()[0] if version_text else "ffmpeg -version printed nothing"
    lines = [f"# ffmpeg build check: {args.target}", "", summary_line(everything), ""]
    lines += [f"`{first_line}`", ""]
    lines += ["## The build", "", *outcome_table(sections["build"], "Source"), ""]
    lines += ["`ffmpeg -L`:", "", "```", license_text.strip(), "```", ""]
    configuration = info.configuration_line(version_text)
    lines += ["Configuration:", "", "```", configuration, "```", ""]
    lines += ["## Size", "", *info.size_table(args.target, args.ffmpeg.resolve(), args.out), ""]
    lines += ["## Conversions", "", *outcome_table(sections["conversions"], "Source"), ""]
    lines += ["## Downloads", "", *outcome_table(sections["downloads"], "Source"), ""]
    lines += ["## Components", "", *info.component_lines(args.ffmpeg.resolve())]
    return lines


def write_report(args: argparse.Namespace) -> bool:
    """Run every check, write report.md into the out folder, and return whether all passed."""
    ffmpeg = args.ffmpeg.resolve()
    reference = args.reference.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    version_text = info.version_output(ffmpeg)
    license_text = info.ffmpeg_output(ffmpeg, "-L")
    sections = {
        "build": build_checks(ffmpeg, version_text, license_text),
        "conversions": check_conversions(ffmpeg, reference, args.out),
        "downloads": check_network(ffmpeg, reference, args.out, args.certificate, args.private_key),
    }
    for results in sections.values():
        for result in results:
            mark = "ok  " if result.outcome.passed else "FAIL"
            print(f"{mark} {result.source}, {result.check}: {result.outcome.detail}")
    lines = report_lines(args, version_text, license_text, sections)
    (args.out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    everything = [result for results in sections.values() for result in results]
    print(summary_line(everything))
    return all(result.outcome.passed for result in everything)


def main(argv: Sequence[str] | None = None) -> int:
    return 0 if write_report(parse_args(argv)) else 1


if __name__ == "__main__":
    sys.exit(main())
