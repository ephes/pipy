"""Values in a server config that name an environment variable or a command.

A value that starts with ``!`` is a shell command, and its trimmed standard
output is the value. Any other value is a template: ``${NAME}`` and ``$NAME``
are replaced by that environment variable, ``$$`` is a literal ``$`` and
``$!`` a literal ``!``. A variable that is unset or empty, and a command that
fails or prints nothing, make the value unresolvable.

This is Pi's current ``resolve-config-value.ts``. The older rule in
``native/auth_store.py`` (a whole value that is the name of a variable) is
what API keys use; only its command runner is shared.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Final, Literal

from pipy_harness.native import auth_store
from pipy_harness.native.auth_store import CommandRunner

_ENV_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_Part = tuple[Literal["literal", "env"], str]


class McpConfigValueError(Exception):
    """A config value could not be resolved. The message names the value."""


def _append_literal(parts: list[_Part], value: str) -> None:
    if not value:
        return
    if parts and parts[-1][0] == "literal":
        parts[-1] = ("literal", parts[-1][1] + value)
    else:
        parts.append(("literal", value))


def _parse_template(config: str) -> list[_Part]:
    parts: list[_Part] = []
    index = 0
    while index < len(config):
        dollar = config.find("$", index)
        if dollar < 0:
            _append_literal(parts, config[index:])
            break
        _append_literal(parts, config[index:dollar])
        following = config[dollar + 1 : dollar + 2]
        if following in ("$", "!"):
            _append_literal(parts, following)
            index = dollar + 2
        elif following == "{":
            end = config.find("}", dollar + 2)
            if end < 0:
                _append_literal(parts, "$")
                index = dollar + 1
                continue
            name = config[dollar + 2 : end]
            if _ENV_NAME.fullmatch(name):
                parts.append(("env", name))
            else:
                _append_literal(parts, config[dollar : end + 1])
            index = end + 1
        else:
            match = _ENV_NAME.match(config, dollar + 1)
            if match:
                parts.append(("env", match.group()))
                index = match.end()
            else:
                _append_literal(parts, "$")
                index = dollar + 1
    return parts


def _missing_names(parts: list[_Part], env: Mapping[str, str]) -> list[str]:
    names: list[str] = []
    for kind, value in parts:
        if kind == "env" and value not in names and not env.get(value):
            names.append(value)
    return names


def resolve_config_value(
    value: str,
    description: str,
    *,
    env: Mapping[str, str] | None = None,
    run_command: CommandRunner | None = None,
) -> str:
    """Resolve ``value`` or raise ``McpConfigValueError`` naming ``description``.

    ``env`` defaults to the process environment. A command runs on every
    call; its result is not cached.
    """

    if value.startswith("!"):
        resolved = auth_store.resolve_config_value(
            value, env={}, run_command=run_command
        )
        if resolved is None:
            raise McpConfigValueError(
                f"Failed to resolve {description} from shell command: {value[1:]}"
            )
        return resolved
    variables = os.environ if env is None else env
    parts = _parse_template(value)
    missing = _missing_names(parts, variables)
    if len(missing) == 1:
        raise McpConfigValueError(
            f"Failed to resolve {description} from environment variable: {missing[0]}"
        )
    if missing:
        raise McpConfigValueError(
            f"Failed to resolve {description} from environment variables: "
            + ", ".join(missing)
        )
    return "".join(
        text if kind == "literal" else variables[text] for kind, text in parts
    )


def resolve_config_headers(
    headers: Mapping[str, str] | None,
    description: str,
    *,
    env: Mapping[str, str] | None = None,
    run_command: CommandRunner | None = None,
) -> dict[str, str] | None:
    """Resolve every header value; ``None`` when there are no headers.

    An unresolvable value is reported as ``<description> header "<name>"``.
    """

    if not headers:
        return None
    return {
        name: resolve_config_value(
            value,
            f'{description} header "{name}"',
            env=env,
            run_command=run_command,
        )
        for name, value in headers.items()
    }
