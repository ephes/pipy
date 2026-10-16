"""Write ``src/pipy_harness/native/unicode_properties.py`` from Node.

minimatch reads its POSIX classes (``[[:alpha:]]``) as Unicode property
escapes (``\\p{L}``) of the JavaScript engine Pi runs on. Python's ``re``
has no such escapes and ``unicodedata`` follows the Python version's
Unicode release, so pipy keeps the ranges Node's engine gives, written by
this script. Run it with the Node release Pi is tested on:

    uv run python scripts/unicode_properties.py

It needs ``node`` on ``PATH`` and the dev tools (``ruff``), and rewrites
the module in place.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

# The general categories minimatch's posixClasses table uses.
CATEGORIES = ("C", "Cc", "L", "Ll", "Lu", "Nd", "Nl", "P", "Pc", "Z", "Zs")

_NODE_SCRIPT = """
const categories = JSON.parse(process.argv[1]);
const ranges = {};
for (const name of categories) {
  const re = new RegExp(`^\\\\p{${name}}$`, "u");
  const found = [];
  let start = -1;
  for (let point = 0; point <= 0x10ffff; point++) {
    const hit = re.test(String.fromCodePoint(point));
    if (hit && start < 0) start = point;
    if (!hit && start >= 0) { found.push([start, point - 1]); start = -1; }
  }
  if (start >= 0) found.push([start, 0x10ffff]);
  ranges[name] = found;
}
console.log(JSON.stringify({
  node: process.version, unicode: process.versions.unicode, ranges,
}));
"""

_TARGET = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "pipy_harness"
    / "native"
    / "unicode_properties.py"
)


def main() -> None:
    result = subprocess.run(
        ["node", "-e", _NODE_SCRIPT, json.dumps(CATEGORIES)],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    lines = [
        f'"""Unicode general categories as Node {data["node"]} reads them',
        f"(Unicode {data['unicode']}).",
        "",
        "Written by ``scripts/unicode_properties.py``; do not edit. Each value",
        "lists the category's code points as ``start-end`` ranges in hex,",
        "separated by spaces (:func:`pipy_harness.native.glob_segment`).",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Final",
        "",
        f'UNICODE_VERSION: Final = "{data["unicode"]}"',
        "",
        "CATEGORY_RANGES: Final[dict[str, str]] = {",
    ]
    for name in CATEGORIES:
        text = " ".join(f"{start:x}-{end:x}" for start, end in data["ranges"][name])
        chunks = textwrap.wrap(
            text, width=70, break_long_words=False, drop_whitespace=False
        )
        if len(chunks) == 1:
            lines.append(f'    "{name}": "{chunks[0]}",')
            continue
        lines.append(f'    "{name}": (')
        lines.extend(f'        "{chunk}"' for chunk in chunks)
        lines.append("    ),")
    lines.append("}")
    _TARGET.write_text("\n".join(lines) + "\n")
    subprocess.run([sys.executable, "-m", "ruff", "format", str(_TARGET)], check=True)


if __name__ == "__main__":
    main()
