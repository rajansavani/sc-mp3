"""Command-line interface: `scmp3 <command>`, with each command in the `COMMANDS` table.

`cli_get` runs `scmp3 get` and `cli_retag` runs `scmp3 retag`. What the commands print is in
`cli_output`, and the terminal review they share is in `cli_review`.
"""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from scmp3.cli_get import add_get_arguments, run_get
from scmp3.cli_retag import add_retag_arguments, run_retag
from scmp3.core.engine_start import EngineStart


@dataclass(frozen=True)
class Command:
    """One CLI command: its help line, how to add its arguments and how to run it."""

    help: str
    add_arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace], int]


# Every CLI command by name; the parser and the top-level help are both built from this.
COMMANDS: dict[str, Command] = {
    "get": Command(
        help="save SoundCloud and YouTube links as tagged MP3 files",
        add_arguments=add_get_arguments,
        run=run_get,
    ),
    "retag": Command(
        help="review and fix the tags on files you already have",
        add_arguments=add_retag_arguments,
        run=run_retag,
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


def main(argv: Sequence[str], engine_start: EngineStart | None = None) -> int:
    """Parse the arguments, run the chosen command and return its exit code.

    `engine_start` says which yt-dlp __main__ settled on, for the command's log.
    """
    args = build_parser().parse_args(argv)
    args.engine_start = engine_start or EngineStart()
    return args.run(args)
