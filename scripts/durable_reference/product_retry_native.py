"""Native Product producer entry; source-bound to the immutable authoring Core."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT / "tests/durable")]
from durable_reference import product_retry_values as values  # noqa: E402
from product_retry_reference_support import (  # noqa: E402
    exercise,
    require_native_binding,
)


def run(recipe, output):
    values.checked_recipe(recipe)
    # Before directory creation or importing/starting Product/provider actors.
    require_native_binding()
    rows = [
        exercise(case, output / "stores/native" / case["id"])
        for case in recipe["cases"]
    ]
    (output / "native-raw.json").write_text(json.dumps(rows, indent=2) + "\n")
    origins = {}
    for row in rows:
        origins.update(row["native_origins"])
    (output / "native-modules.json").write_text(json.dumps(origins, indent=2) + "\n")


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
