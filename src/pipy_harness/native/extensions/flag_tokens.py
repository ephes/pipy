"""Parser for extension-owned CLI flag tokens."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pipy_harness.native.extension_types import ExtensionFlag, RegisteredFlag


@dataclass(frozen=True, slots=True)
class _ParsedExtensionFlagToken:
    name: str
    value: object
    next_index: int


def _parse_boolean_flag_value(name: str, separator: str, inline: str) -> bool | str:
    if not separator:
        return True
    lowered = inline.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return f"invalid boolean value for --{name}"


def _parse_string_flag_value(
    name: str,
    separator: str,
    inline: str,
    tokens: Sequence[str],
    index: int,
) -> tuple[str, int] | str:
    if separator:
        return inline, index + 1
    next_index = index + 1
    if next_index >= len(tokens) or tokens[next_index].startswith("--"):
        return f"missing value for --{name}"
    return tokens[next_index], index + 2


def _parse_extension_flag_token(
    definitions: Mapping[str, ExtensionFlag],
    tokens: Sequence[str],
    index: int,
) -> _ParsedExtensionFlagToken | str:
    """Classify one token and parse its value without mutating flag owners."""

    token = tokens[index]
    if not token.startswith("--") or token == "--":
        return f"unexpected extension flag token: {token!r}"
    name, separator, inline = token[2:].partition("=")
    flag = definitions.get(name)
    if flag is None:
        return f"unknown extension flag: --{name}"
    if flag.flag_type == "boolean":
        value = _parse_boolean_flag_value(name, separator, inline)
        if isinstance(value, str):
            return value
        return _ParsedExtensionFlagToken(name, value, index + 1)
    parsed = _parse_string_flag_value(name, separator, inline, tokens, index)
    if isinstance(parsed, str):
        return parsed
    value, next_index = parsed
    return _ParsedExtensionFlagToken(name, value, next_index)


def parse_extension_flag_tokens(
    registered_flags: Sequence[RegisteredFlag],
    tokens: Sequence[str],
) -> tuple[dict[str, object], str | None]:
    """Parse unknown CLI tokens against activated extension flags."""

    definitions = {
        registered.flag.name: registered.flag for registered in registered_flags
    }
    owners = {registered.flag.name: registered for registered in registered_flags}
    values: dict[str, object] = {
        flag.name: flag.default
        for flag in definitions.values()
        if flag.default is not None
    }
    index = 0
    while index < len(tokens):
        parsed = _parse_extension_flag_token(definitions, tokens, index)
        if isinstance(parsed, str):
            return {}, parsed
        values[parsed.name] = parsed.value
        owner = owners.get(parsed.name)
        if owner is not None:
            owner._apply_value(parsed.value)
        index = parsed.next_index
    return values, None
