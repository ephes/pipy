"""`ls` sorts like Pi's `toLowerCase().localeCompare(...)` (TOOLS2).

`tests/fixtures/pi_tools2/ls_locale_order.json` is Node's own order (v26,
ICU 78.3) of 400 names mixing ASCII punctuation, digits, accents, expansions
(`ß`, `æ`), compatibility forms, Greek, Cyrillic, Hangul, kana, Han, currency
and emoji.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from pipy_harness.native.tools import ToolContext, ToolRequest, make_tool_request_id
from pipy_harness.native.tools.collation import locale_compare_key, ls_sort_key
from pipy_harness.native.tools.ls import LsTool

_FIXTURE = json.loads(
    (
        Path(__file__).parent / "fixtures" / "pi_tools2" / "ls_locale_order.json"
    ).read_text()
)


def test_matches_nodes_order_for_the_fixture() -> None:
    expected = _FIXTURE["sorted"]
    keys = [ls_sort_key(name) for name in expected]

    # Node's order is non-decreasing under the key; only full ties (`a`/`A`)
    # may swap, and those keep the input order in both runtimes.
    assert keys == sorted(keys)
    shuffled = list(expected)
    random.Random(7).shuffle(shuffled)
    assert [ls_sort_key(name) for name in sorted(shuffled, key=ls_sort_key)] == keys


def test_pins_the_measured_rules() -> None:
    def order(*names: str) -> list[str]:
        return sorted(names, key=ls_sort_key)

    assert order("9", "10") == ["10", "9"]
    assert order("ß", "ss", "st") == ["ss", "ß", "st"]
    assert order("æ", "áe", "ae") == ["ae", "áe", "æ"]
    assert order("ab", "a b", "á", "a") == ["a", "á", "a b", "ab"]
    assert order("z", "ａ") == ["ａ", "z"]
    assert order("一", "가") == ["가", "一"]
    assert order("a", "_a", "-a", "\U0001f600", "$", "1") == [
        "_a",
        "-a",
        "\U0001f600",
        "$",
        "1",
        "a",
    ]


def test_full_ties_keep_the_input_order() -> None:
    assert locale_compare_key("x") == ls_sort_key("X")
    assert sorted(["B", "b"], key=ls_sort_key) == ["B", "b"]
    assert sorted(["b", "B"], key=ls_sort_key) == ["b", "B"]


def test_ls_lists_in_that_order(tmp_path: Path) -> None:
    for name in ("b", "_c", "A", "10", "9", "ä"):
        (tmp_path / name).write_text("")

    result = LsTool().invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(), tool_name="ls", arguments={}
        ),
        ToolContext(workspace_root=tmp_path),
    )

    assert result.output_text.splitlines() == ["_c", "10", "9", "A", "ä", "b"]
