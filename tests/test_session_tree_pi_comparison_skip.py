"""The Pi session-tree comparison treats a missing Pi reference as a skip.

When Pi cannot be driven (checkout/deps absent), the hard pipy product-path leg
must still pass and the overall script must exit 0 (skip), never 1.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "parity_checks"
    / "session_tree_pi_comparison.py"
)


def test_pi_unavailable_is_a_skip_not_a_failure(tmp_path, monkeypatch) -> None:
    env = {
        **__import__("os").environ,
        "PI_MONO_DIR": str(tmp_path / "no-such-pi-checkout"),
    }
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8")[:500]
    report = json.loads(proc.stdout.decode("utf-8"))
    assert report["passed"] is True
    assert report["skipped"] is True
    # The pipy product-path leg actually ran and passed.
    assert any(c["name"].startswith("pipy_") and c["passed"] for c in report["checks"])
