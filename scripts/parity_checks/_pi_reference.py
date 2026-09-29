"""Shared launcher for the Pi-vs-pipy comparison drivers.

pi-mono no longer ships ``tsx``. It runs TypeScript straight from source with
Node's built-in type stripping (``engines.node >= 22.19.0``) and preloads
``packages/coding-agent/src/experimental/source-resolver.ts`` so the
``@earendil-works/*`` workspace imports resolve to source rather than to stale
``dist`` files (see pi-mono ``packages/agent/package.json`` bench scripts). The
comparison drivers run the same way.

A missing reference is never a pass. ``pi_unavailable_reason`` returns why the
Pi leg cannot run, and the comparison scripts report that as not passing with
``EXIT_PI_UNAVAILABLE``.

This module has no entry point; the comparison scripts import it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

DEFAULT_PI_MONO_DIR = "/Users/jochen/src/pi-mono"
MIN_NODE_VERSION = (22, 19, 0)
SOURCE_RESOLVER = Path(
    "packages", "coding-agent", "src", "experimental", "source-resolver.ts"
)
# Non-zero, and distinct from a comparison failure (1).
EXIT_PI_UNAVAILABLE = 2
UNAVAILABLE_BANNER = "NOT RUN (Pi reference unavailable) - comparison did not pass"


def pi_mono_dir() -> Path:
    # Absolute, because the driver runs with the checkout as its cwd.
    return Path(os.environ.get("PI_MONO_DIR", DEFAULT_PI_MONO_DIR)).resolve()


def _node_version(node: str) -> tuple[int, int, int] | None:
    try:
        proc = subprocess.run(
            [node, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.match(r"v(\d+)\.(\d+)\.(\d+)", proc.stdout.decode("utf-8").strip())
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def pi_unavailable_reason(pi_dir: Path, required: Path) -> str | None:
    """Return why the Pi leg cannot run, or ``None`` when it can."""

    if not pi_dir.is_dir():
        return f"pi-mono not found at {pi_dir}"
    if not (pi_dir / "node_modules").is_dir():
        return f"pi-mono deps not installed (no {pi_dir / 'node_modules'})"
    if not (pi_dir / SOURCE_RESOLVER).exists():
        return f"pi source resolver missing ({pi_dir / SOURCE_RESOLVER})"
    if not (pi_dir / required).exists():
        return f"pi source missing ({pi_dir / required})"
    node = shutil.which("node")
    if node is None:
        return "node not found on PATH"
    version = _node_version(node)
    if version is None or version < MIN_NODE_VERSION:
        wanted = ".".join(str(part) for part in MIN_NODE_VERSION)
        return f"node {version} is older than pi-mono's required {wanted}"
    return None


def run_pi_driver(
    pi_dir: Path, driver: Path, args: list[str], *, timeout: float = 120.0
) -> str:
    """Run a ``.mts`` driver the way pi-mono runs its own TypeScript sources.

    A driver that starts but fails (API drift, an incomplete install) raises
    ``RuntimeError``; callers report that as a failed comparison (exit 1).
    """

    pi_dir = pi_dir.resolve()

    env = dict(os.environ)
    env["PI_MONO_DIR"] = str(pi_dir)
    proc = subprocess.run(
        ["node", "--import", str(pi_dir / SOURCE_RESOLVER), str(driver), *args],
        cwd=str(pi_dir),  # anchor module resolution at the Pi checkout
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"pi driver exit {proc.returncode}: {proc.stderr.decode('utf-8')[:400]}"
        )
    return proc.stdout.decode("utf-8")
