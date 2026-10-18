"""Entry point: `scmp3` opens the app and `scmp3 <command>` runs the CLI."""

import argparse
import sys
from collections.abc import Sequence

from scmp3 import __version__, cli


def build_parser() -> argparse.ArgumentParser:
    """Build the parser for the options used when no command is given."""
    command_names = ", ".join(cli.COMMANDS)
    parser = argparse.ArgumentParser(
        prog="scmp3",
        description="With no arguments, opens the sc-mp3 window.",
        epilog=f"Commands: {command_names}. Run 'scmp3 <command> --help' to see its options.",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a CLI command, print the version, or open the app, and return the exit code."""
    args = list(sys.argv[1:] if argv is None else argv)

    # A leading word is a command, so the CLI's parser can name the valid ones on a typo.
    if args and not args[0].startswith("-"):
        return cli.main(args)

    options = build_parser().parse_args(args)
    if options.version:
        print(__version__)
        return 0

    # Imported here so CLI commands never load the window toolkit.
    from scmp3.app import main as app_main

    return app_main.run()


if __name__ == "__main__":
    sys.exit(main())
