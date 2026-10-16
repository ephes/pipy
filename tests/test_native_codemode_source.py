"""The codemode options line (goal-check finding M17, Pi ``source.ts``)."""

from __future__ import annotations

import pytest

from pipy_harness.native.codemode.source import (
    MAX_TIMEOUT_MS,
    CodemodeSourceError,
    ParsedCodemodeSource,
    parse_codemode_source,
)

_FIELDS = "`max_output_tokens` and `timeout_ms`"


def test_a_script_without_options_is_unchanged() -> None:
    source = "text('hi')\n# @options: not the first line\n"

    assert parse_codemode_source(source) == ParsedCodemodeSource(source)


@pytest.mark.parametrize(
    ("source", "parsed"),
    [
        (
            '// @options: {"max_output_tokens": 2000, "timeout_ms": 30000}\ntext(1)',
            ParsedCodemodeSource("\ntext(1)", 2000, 30000),
        ),
        # Leading blanks, a CRLF line and an integral float, as JSON allows.
        (
            '  \t// @options: {"timeout_ms": 1.0}\r\ntext(1)\r\n',
            ParsedCodemodeSource("\ntext(1)\r\n", None, 1),
        ),
        (
            '// @options: {"max_output_tokens": 0}\ntext(1)',
            ParsedCodemodeSource("\ntext(1)", 0, None),
        ),
        (
            f'// @options: {{"timeout_ms": {MAX_TIMEOUT_MS}}}\ntext(1)',
            ParsedCodemodeSource("\ntext(1)", None, MAX_TIMEOUT_MS),
        ),
        ("// @options: {}\ntext(1)", ParsedCodemodeSource("\ntext(1)")),
    ],
)
def test_the_options_line_is_read_and_left_out(
    source: str, parsed: ParsedCodemodeSource
) -> None:
    # Pi: the line becomes an empty line, so line numbers do not change.
    assert parse_codemode_source(source) == parsed


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            " \n\t",
            "Expected Python source text (non-empty). Provide Python only, "
            'optionally with a first line `// @options: {"max_output_tokens": 1000}`.',
        ),
        (
            '// @options: {"timeout_ms": 5}',
            "The @options line must be followed by Python source on subsequent lines",
        ),
        (
            '// @options: {"timeout_ms": 5}\n  \n',
            "The @options line must be followed by Python source on subsequent lines",
        ),
        (
            "// @options:\ntext(1)",
            f"@options must be a JSON object with supported fields {_FIELDS}",
        ),
        (
            "// @options: [1]\ntext(1)",
            f"@options must be a JSON object with supported fields {_FIELDS}",
        ),
        (
            '// @options: {"retries": 1}\ntext(1)',
            f"@options only supports {_FIELDS}; got `retries`",
        ),
        (
            '// @options: {"max_output_tokens": -1}\ntext(1)',
            "@options field `max_output_tokens` must be a non-negative safe integer",
        ),
        (
            '// @options: {"max_output_tokens": 1.5}\ntext(1)',
            "@options field `max_output_tokens` must be a non-negative safe integer",
        ),
        (
            '// @options: {"max_output_tokens": null}\ntext(1)',
            "@options field `max_output_tokens` must be a non-negative safe integer",
        ),
        (
            '// @options: {"max_output_tokens": true}\ntext(1)',
            "@options field `max_output_tokens` must be a non-negative safe integer",
        ),
        (
            '// @options: {"max_output_tokens": 9007199254740992}\ntext(1)',
            "@options field `max_output_tokens` must be a non-negative safe integer",
        ),
        (
            '// @options: {"timeout_ms": 0}\ntext(1)',
            f"@options field `timeout_ms` must be a positive integer up to {MAX_TIMEOUT_MS}",
        ),
        (
            f'// @options: {{"timeout_ms": {MAX_TIMEOUT_MS + 1}}}\ntext(1)',
            f"@options field `timeout_ms` must be a positive integer up to {MAX_TIMEOUT_MS}",
        ),
    ],
)
def test_invalid_input_is_refused_with_pis_message(source: str, message: str) -> None:
    with pytest.raises(CodemodeSourceError) as caught:
        parse_codemode_source(source)

    assert str(caught.value) == message


@pytest.mark.parametrize("directive", ["{", '{"timeout_ms": NaN}', "{'a': 1}"])
def test_options_that_are_not_json_are_refused(directive: str) -> None:
    with pytest.raises(CodemodeSourceError) as caught:
        parse_codemode_source(f"// @options: {directive}\ntext(1)")

    assert str(caught.value).startswith(
        f"@options must be valid JSON with supported fields {_FIELDS}: "
    )
