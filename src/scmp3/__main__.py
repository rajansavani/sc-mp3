"""Entry point: `scmp3` opens the app and `scmp3 <command>` runs the CLI.

Anything else opens the app too, so files dropped on sc-mp3.exe arrive as if dropped on the window.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from scmp3 import __version__, cli, cli_output, frozen


def build_parser() -> argparse.ArgumentParser:
    """Build the parser for the options used when no command is given."""
    command_names = ", ".join(cli.COMMANDS)
    parser = argparse.ArgumentParser(
        prog="scmp3",
        description="With no arguments, opens the sc-mp3 window. Files given after it are added "
        "as if dropped on the window.",
        epilog=f"Commands: {command_names}. Run 'scmp3 <command> --help' to see its options.",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument(
        "--dev-ui",
        action="store_true",
        help="open the interface in your browser with sample data, for working on the UI",
    )
    return parser


def launch_paths(extras: Sequence[str]) -> list[str]:
    """The files the app is opened with, without options it doesn't know, like Finder's -psn_."""
    return [extra for extra in extras if not extra.startswith("-")]


def in_terminal() -> bool:
    """Whether someone typed the command, rather than Explorer or Finder starting the app."""
    return sys.stdout is not None and sys.stdout.isatty()


def run_command(args: Sequence[str]) -> int:
    """Run a CLI command, after the Windows app's own checks."""
    stopped = frozen.prepare()
    if stopped is not None:
        return stopped
    return cli.main(args)


def main(argv: Sequence[str] | None = None) -> int:
    """Run a CLI command, print the version, or open the app, and return the exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in cli.COMMANDS:
        return run_command(args)

    options, extras = build_parser().parse_known_args(args)
    if options.version:
        print(__version__)
        return 0

    paths = launch_paths(extras)
    typos = [path for path in paths if not Path(path).exists()]
    # In a terminal, a word that's neither a command nor a file is most likely a typo.
    if typos and in_terminal():
        print(cli_output.unknown_command(typos[0], list(cli.COMMANDS)), file=sys.stderr)
        return cli_output.EXIT_USAGE

    # The Windows app checks its folder before anything loads curl_cffi or the window.
    stopped = frozen.prepare()
    if stopped is not None:
        return stopped

    if options.dev_ui:
        from scmp3.app import devserver

        return devserver.run()

    # Imported here so CLI commands never load the window toolkit.
    from scmp3.app import main as app_main

    return app_main.run(paths)


if __name__ == "__main__":
    sys.exit(main())
