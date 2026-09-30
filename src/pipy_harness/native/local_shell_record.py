"""The conversation record and rows of a ``!`` shell command.

Pi stores a ``bashExecution`` message, sends it to the model as the text of
``bashExecutionToText`` (``core/messages.ts``) and draws it with
``BashExecutionComponent`` (``modes/interactive/components/bash-execution.ts``).
pipy records the command as a user message holding exactly that text, so the
model sees what Pi's sees; :func:`parse_local_shell_record` reads it back for
the restored-history rows, and :func:`shell_result_lines` draws the rows the
live shortcut and the restored history share.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pipy_harness.native.tools.truncate import truncate_tail

# Pi's collapsed preview keeps the last 20 lines (`PREVIEW_LINES`).
SHELL_PREVIEW_LINES = 20

_FENCE = "```"
_RECORD = re.compile(
    r"Ran `(?P<command>.*?)`\n"
    r"(?:```\n(?P<output>.*)\n```|\(no output\))"
    r"(?:\n\n(?P<status>\(command cancelled\)|Command exited with code (?P<code>-?\d+)))?"
    r"(?:\n\n\[Output truncated\. Full output: (?P<path>[^\n]*)\])?",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class LocalShellRecord:
    """The facts of Pi's ``BashExecutionMessage`` that its text carries."""

    command: str
    output: str
    exit_code: int | None
    cancelled: bool
    truncated: bool
    full_output_path: str | None


def format_local_shell_record(record: LocalShellRecord) -> str:
    """Pi ``bashExecutionToText``."""

    text = f"Ran `{record.command}`\n"
    if record.output:
        text += f"{_FENCE}\n{record.output}\n{_FENCE}"
    else:
        text += "(no output)"
    if record.cancelled:
        text += "\n\n(command cancelled)"
    elif record.exit_code is not None and record.exit_code != 0:
        text += f"\n\nCommand exited with code {record.exit_code}"
    if record.truncated and record.full_output_path:
        text += f"\n\n[Output truncated. Full output: {record.full_output_path}]"
    return text


def parse_local_shell_record(text: str) -> LocalShellRecord | None:
    """Read a record written by :func:`format_local_shell_record`, else ``None``.

    The whole text must have the record's shape. The text holds no exit code
    for a successful command, so it reads back as ``0``.
    """

    match = _RECORD.fullmatch(text)
    if match is None:
        return None
    cancelled = match["status"] == "(command cancelled)"
    code = match["code"]
    path = match["path"]
    return LocalShellRecord(
        command=match["command"],
        output=match["output"] or "",
        exit_code=None if cancelled else int(code) if code is not None else 0,
        cancelled=cancelled,
        truncated=path is not None,
        full_output_path=path,
    )


def shell_result_lines(record: LocalShellRecord, *, expanded: bool) -> tuple[str, ...]:
    """The rows below ``$ command``, as Pi's ``BashExecutionComponent`` draws them.

    The output is cut to the last 2000 lines / 50 KB (the context limit) and,
    collapsed, to the last 20 lines; then the status lines.
    """

    context = truncate_tail(record.output)
    available = context.content.split("\n") if context.content else []
    preview = available[-SHELL_PREVIEW_LINES:]
    hidden = len(available) - len(preview)
    lines = list(available if expanded else preview)
    status: list[str] = []
    if hidden > 0:
        status.append(
            "(ctrl+o to collapse)"
            if expanded
            else f"... {hidden} more lines (ctrl+o to expand)"
        )
    status.extend(shell_status_lines(record, context_truncated=context.truncated))
    if status:
        lines.extend(["", *status])
    return tuple(lines)


def shell_status_lines(
    record: LocalShellRecord, *, context_truncated: bool = False
) -> list[str]:
    """Pi's status lines: ``(cancelled)`` / ``(exit N)`` and the truncation."""

    status: list[str] = []
    if record.cancelled:
        status.append("(cancelled)")
    elif record.exit_code is not None and record.exit_code != 0:
        status.append(f"(exit {record.exit_code})")
    if (record.truncated or context_truncated) and record.full_output_path:
        status.append(f"Output truncated. Full output: {record.full_output_path}")
    return status


__all__ = [
    "SHELL_PREVIEW_LINES",
    "LocalShellRecord",
    "format_local_shell_record",
    "parse_local_shell_record",
    "shell_result_lines",
    "shell_status_lines",
]
