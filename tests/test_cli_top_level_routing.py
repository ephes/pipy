"""Front-controller routing matrix for the Pi-shape top-level dispatch.

`route_argv` injects the implicit `repl` subcommand for bare/positional/repl-flag
invocations so `pipy` and `pipy "<prompt>"` launch the interactive product
session, while subcommands and the root-only flags stay reachable.
"""

import pytest

from pipy_harness.cli import (
    KNOWN_SUBCOMMANDS,
    _TOP_LEVEL_ONLY_FLAGS,
    build_parser,
    route_argv,
)

SUBS = {
    "auth",
    "run",
    "repl",
    "config",
    "install",
    "remove",
    "uninstall",
    "list",
    "update",
}


@pytest.mark.parametrize(
    "argv,expected",
    [
        ([], ["repl"]),  # bare pipy -> interactive
        (["do X"], ["repl", "do X"]),  # positional prompt -> repl
        (["--model", "m"], ["repl", "--model", "m"]),  # bare repl flag -> repl
        (["-p", "x"], ["repl", "-p", "x"]),  # one-shot
        (["@file.py", "summarize"], ["repl", "@file.py", "summarize"]),
        (["--list-models"], ["repl", "--list-models"]),  # repl flag -> repl
        (["repl", "--model", "m"], ["repl", "--model", "m"]),  # subcommand unchanged
        (["run", "--agent", "a"], ["run", "--agent", "a"]),
        (["auth"], ["auth"]),  # reserved word -> subcommand (exception)
        (["config", "show"], ["config", "show"]),
        (["install", "src"], ["install", "src"]),
        (["list"], ["list"]),
        (["update"], ["update"]),
        (["--help"], ["--help"]),  # top-level only, not re-routed
        (["-h"], ["-h"]),
        (["--version"], ["--version"]),
        (["-v"], ["-v"]),
        (["--export", "s.jsonl"], ["--export", "s.jsonl"]),
        # A multi-word prompt that starts with a subcommand word is a single
        # positional string, so it is routed to the REPL, not the subcommand.
        (["run the tests"], ["repl", "run the tests"]),
        # The argparse `--flag=value` form of a root-only flag is also top-level.
        (["--export=session.jsonl"], ["--export=session.jsonl"]),
        # A `--flag=value` form whose flag is NOT root-only still routes to repl.
        (["--model=x"], ["repl", "--model=x"]),
    ],
)
def test_route_argv(argv, expected):
    assert route_argv(list(argv), SUBS) == expected


def test_route_argv_does_not_mutate_input():
    argv = ["do X"]
    route_argv(argv, SUBS)
    assert argv == ["do X"]


def test_known_subcommands_matches_parser():
    parser = build_parser()
    parser_subcommands: set[str] = set()
    for action in parser._subparsers._group_actions:  # type: ignore[union-attr]
        choices = getattr(action, "choices", None)
        if choices:
            parser_subcommands.update(choices.keys())
    assert KNOWN_SUBCOMMANDS == parser_subcommands
    assert KNOWN_SUBCOMMANDS == SUBS


def test_top_level_only_flags_are_root_options():
    parser = build_parser()
    root_option_strings: set[str] = set()
    for action in parser._actions:
        root_option_strings.update(action.option_strings)
    # Every declared top-level-only flag must be a real root option string.
    assert _TOP_LEVEL_ONLY_FLAGS <= root_option_strings
