"""Owned private generic migration producer; no public Product reset parity claim."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT / "tests/durable")]
from durable_reference import migration_values as values  # noqa: E402
from migration_reference_support import exercise, origins  # noqa: E402


def run(recipe, output):
    assert recipe == values.RECIPE
    rows = []
    for case in recipe["cases"]:
        row = exercise(case, output / "stores/native" / case["id"])
        row["native_origins"] = origins(ROOT)
        rows.append(row)
    (output / "native-raw.json").write_text(json.dumps(rows, indent=2) + "\n")
    (output / "native-modules.json").write_text(
        json.dumps(origins(ROOT), indent=2) + "\n"
    )


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
