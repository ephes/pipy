"""Regenerate or diff the pinned Pi system prompts (`tests/fixtures`).

Runs `pi_system_prompt_driver.mts` against the Pi checkout (`PI_MONO_DIR`,
default `/Users/jochen/src/pi-mono`), replaces the checkout path with `<pi>`
and writes `tests/fixtures/pi_system_prompt_sections.json` (`--write`) or
reports whether the pinned file still matches Pi (default).

    uv run python scripts/parity_checks/pi_system_prompt_fixture.py [--write]

Exits 0 when the fixture matches (or was written), 1 on a mismatch and 2 when
the Pi reference is unavailable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _pi_reference import (
    EXIT_PI_UNAVAILABLE,
    pi_mono_dir,
    pi_unavailable_reason,
    run_pi_driver,
)

DRIVER = Path(__file__).with_name("pi_system_prompt_driver.mts")
FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "pi_system_prompt_sections.json"
)


def pi_prompt_cases(pi_dir: Path) -> str:
    """The driver's JSON with the checkout path replaced by ``<pi>``."""

    output = run_pi_driver(pi_dir, DRIVER, [])
    return output.replace(str(pi_dir.resolve()), "<pi>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    pi_dir = pi_mono_dir()
    reason = pi_unavailable_reason(
        pi_dir, Path("packages", "coding-agent", "src", "core", "system-prompt.ts")
    )
    if reason is not None:
        print(f"Pi reference unavailable: {reason}", file=sys.stderr)
        return EXIT_PI_UNAVAILABLE
    current = json.loads(pi_prompt_cases(pi_dir))
    if args.write:
        FIXTURE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {FIXTURE}")
        return 0
    pinned = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if pinned != current:
        print("pinned Pi system prompts differ from Pi; rerun with --write")
        return 1
    print("pinned Pi system prompts match Pi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
