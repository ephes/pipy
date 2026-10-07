"""Command line for ``python -m pipy_harness.native.codemode install|status``.

``install`` is the only step that downloads or unpacks the codemode runtime;
nothing fetches it during a turn. ``--from-file`` installs from a local copy of
the pinned archive, which must still match the pinned size and sha256.
``install`` then runs the availability self-test, which also builds the
compiled-module cache. ``status`` runs only the self-test.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipy_harness.native.codemode.runtime import (
    RUNTIME_PIN,
    CodemodeRuntimeError,
    default_paths,
    install_runtime,
)
from pipy_harness.native.codemode.selftest import Availability, availability


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pipy_harness.native.codemode")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser(
        "install", help="install the pinned CPython-on-WASI runtime"
    )
    install.add_argument(
        "--from-file", type=Path, help="install from a local copy of the pinned archive"
    )
    install.add_argument(
        "--force", action="store_true", help="reinstall even if a valid runtime exists"
    )
    commands.add_parser("status", help="run the availability self-test")
    return parser


def _report(result: Availability) -> int:
    if result.available:
        print(
            f"codemode available: wasmtime {result.wasmtime_version}, runtime {RUNTIME_PIN.version}, cache {result.cache}"
        )
        return 0
    print(f"codemode unavailable: {result.reason}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = default_paths()
    if args.command == "install":
        try:
            installed = install_runtime(paths, source=args.from_file, force=args.force)
        except (CodemodeRuntimeError, OSError) as exc:
            print(f"codemode install failed: {exc}", file=sys.stderr)
            return 1
        print(f"runtime {installed.status}: {installed.runtime_dir}")
    return _report(availability(paths))
