"""Command-line interface: `scmp3 get` and `scmp3 retag`."""

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

NOT_IMPLEMENTED = "Not implemented yet."


@dataclass(frozen=True)
class Command:
    """One CLI command: its help line, how to add its arguments and how to run it."""

    help: str
    add_arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace], int]


def not_implemented(args: argparse.Namespace) -> int:
    """Stand in for a command that a later ticket builds."""
    print(NOT_IMPLEMENTED, file=sys.stderr)
    return 1


def add_get_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the arguments for `scmp3 get`."""
    parser.add_argument("urls", nargs="+", metavar="url", help="a SoundCloud or YouTube link")


def add_retag_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the arguments for `scmp3 retag`."""
    parser.add_argument("files", nargs="+", metavar="file", help="an MP3 or M4A file")


# Every CLI command by name; the parser and the top-level help are both built from this.
COMMANDS: dict[str, Command] = {
    "get": Command(
        help="save SoundCloud or YouTube links as tagged MP3 files",
        add_arguments=add_get_arguments,
        run=not_implemented,
    ),
    "retag": Command(
        help="review and fix the tags on files you already have",
        add_arguments=add_retag_arguments,
        run=not_implemented,
    ),
}


def build_parser() -> argparse.ArgumentParser:
    """Build the parser with one subcommand per entry in `COMMANDS`."""
    parser = argparse.ArgumentParser(prog="scmp3")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name, command in COMMANDS.items():
        subparser = subparsers.add_parser(name, help=command.help)
        command.add_arguments(subparser)
        subparser.set_defaults(run=command.run)
    return parser


def main(argv: Sequence[str]) -> int:
    """Parse the arguments, run the chosen command and return its exit code."""
    args = build_parser().parse_args(argv)
    return args.run(args)
