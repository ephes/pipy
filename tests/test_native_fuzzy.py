"""Pi ``fuzzy.ts`` port: pinned results from Pi's code, plus a live Node diff.

The pinned values were produced by running ``packages/tui/src/fuzzy.ts`` at
pi-mono ``1b347794e`` under Node. ``test_matches_pi_under_node`` repeats the
comparison over a random corpus when Node and a pi-mono checkout are present
(``PI_MONO_DIR``, default ``/Users/jochen/src/pi-mono`` like
``scripts/parity_checks/_pi_reference.py``) and is skipped otherwise.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from pipy_harness.native.fuzzy import fuzzy_filter, fuzzy_match, js_trim

# (query, text, matches, score) from Pi's fuzzyMatch.
_PINNED_MATCHES = [
    ("", "anything", True, 0),
    ("abc", "ab", False, 0),
    ("gpt5", "openai gpt-5.5", True, -29.5),
    ("5gpt", "gpt5", True, -154.4),
    ("gpt", "GPT", True, -139.7),
    (
        "sol",
        "openai-codex openai-codex/gpt-6.1-sol openai-codex gpt-6.1-sol GPT-6.1 Sol",
        True,
        -14.500000000000002,
    ),
    ("o/g", "openai/gpt-4o", True, -18.7),
    ("xyz", "openai/gpt-4o", False, 0),
    ("ΣΑΣ", "σας", True, -139.7),
    ("İ", "i̇stanbul", True, -24.9),
    ("😀a", "x😀ya", True, -2.3000000000000003),
    ("é", "café café", True, -3.3000000000000003),
    ("ab", "a b a﻿b", True, -22.8),
    ("b", "a\u0085b", True, 0.2),
    ("high", "high Deep reasoning (~16k tokens)", True, -59.4),
    ("4o", "openai gpt4o", True, -2.9),
    ("o4", "openai gpt4o", True, 4),
]

# (items, query, result) from Pi's fuzzyFilter with an identity getText.
_PINNED_FILTERS = [
    (
        ["model", "thinking", "resume", "reload", "settings", "scoped-models"],
        "mo",
        ["model", "scoped-models"],
    ),
    (
        ["model", "thinking", "resume", "reload"],
        "  ",
        ["model", "thinking", "resume", "reload"],
    ),
    (
        [
            "anthropic/claude-sonnet-4-5",
            "openai/gpt-5.5",
            "openrouter/openai/gpt-5.5",
        ],
        "openai/gpt",
        ["openai/gpt-5.5", "openrouter/openai/gpt-5.5"],
    ),
    (["alpha beta", "beta alpha", "gamma"], "beta alpha", ["alpha beta", "beta alpha"]),
    (["a b", "abc", "xbc"], "bc", ["abc", "xbc"]),
]


@pytest.mark.parametrize(("query", "text", "matches", "score"), _PINNED_MATCHES)
def test_fuzzy_match_matches_pi(
    query: str, text: str, matches: bool, score: float
) -> None:
    result = fuzzy_match(query, text)
    assert (result.matches, result.score) == (matches, score)


@pytest.mark.parametrize(("items", "query", "expected"), _PINNED_FILTERS)
def test_fuzzy_filter_matches_pi(
    items: list[str], query: str, expected: list[str]
) -> None:
    assert fuzzy_filter(items, query, lambda item: item) == expected


def test_js_trim_uses_the_ecmascript_whitespace_set() -> None:
    assert js_trim("﻿ a  ") == "a"
    # U+0085 and U+001C are Python whitespace but not ECMAScript whitespace.
    assert js_trim("\x85a\x1c") == "\x85a\x1c"


_NODE_SCRIPT = """
import { readFileSync } from "node:fs";
const m = await import(process.argv[2]);
const cases = JSON.parse(readFileSync(0, "utf8"));
const out = {
  match: cases.match.map(([q, t]) => m.fuzzyMatch(q, t)),
  filter: cases.filter.map(([items, q]) => m.fuzzyFilter(items, q, (x) => x)),
};
process.stdout.write(JSON.stringify(out));
"""

_ALPHABET = "abcgpo45 -_./:ΣσİiÉé́😀 ﻿\u0085AB"


def _random_text(rng: random.Random, low: int, high: int) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(low, high)))


def _pi_fuzzy_module() -> Path | None:
    root = Path(os.environ.get("PI_MONO_DIR", "/Users/jochen/src/pi-mono"))
    module = root / "packages" / "tui" / "src" / "fuzzy.ts"
    if shutil.which("node") is None or not module.is_file():
        return None
    return module


def test_matches_pi_under_node(tmp_path: Path) -> None:
    module = _pi_fuzzy_module()
    if module is None:
        pytest.skip("node or the pi-mono checkout is not available")
    rng = random.Random(20260930)
    match_cases = [
        [_random_text(rng, 0, 4), _random_text(rng, 0, 24)] for _ in range(400)
    ]
    filter_cases: list[tuple[list[str], str]] = [
        ([_random_text(rng, 1, 16) for _ in range(8)], _random_text(rng, 0, 6))
        for _ in range(150)
    ]
    script = tmp_path / "fuzzy_diff.mjs"
    script.write_text(_NODE_SCRIPT, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(script), module.as_uri()],
        input=json.dumps({"match": match_cases, "filter": filter_cases}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    expected = json.loads(proc.stdout)
    for (query, text), pi in zip(match_cases, expected["match"], strict=True):
        result = fuzzy_match(query, text)
        assert (result.matches, result.score) == (pi["matches"], pi["score"]), (
            query,
            text,
        )
    for (filter_items, filter_query), pi in zip(
        filter_cases, expected["filter"], strict=True
    ):
        assert fuzzy_filter(filter_items, filter_query, lambda item: item) == pi, (
            filter_items,
            filter_query,
        )
