"""The conversation record a ``!`` shell command leaves in the session.

Pi stores a ``bashExecution`` message and renders it with its shell component.
pipy records the command as a user message with a fixed shape, so the live
shortcut writes it here and the restored-history renderer reads it back into
the same ``$ command`` and status/output rows the shortcut drew.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LOCAL_SHELL_RECORD_PREFIX = (
    "I ran a shell command in the workspace (not a tool call):\n\n$ "
)

_STATUS_LINE = re.compile(
    r"^(?:exit code: (?P<code>-?\d+|None)|\(timed out\)|\(cancelled by [^)\n]*\))$"
)


@dataclass(frozen=True, slots=True)
class LocalShellRecord:
    command: str
    status_line: str
    output: str

    @property
    def is_error(self) -> bool:
        """The shortcut's own error rule: a timeout or a non-zero exit code."""

        if self.status_line == "(timed out)":
            return True
        match = _STATUS_LINE.match(self.status_line)
        code = match.group("code") if match else None
        return code not in (None, "None", "0")


def format_local_shell_record(command: str, status_line: str, output: str) -> str:
    return f"{LOCAL_SHELL_RECORD_PREFIX}{command}\n{status_line}\n\n{output}"


def parse_local_shell_record(text: str) -> LocalShellRecord | None:
    """Read a record written by :func:`format_local_shell_record`, else ``None``."""

    if not text.startswith(LOCAL_SHELL_RECORD_PREFIX):
        return None
    lines = text[len(LOCAL_SHELL_RECORD_PREFIX) :].split("\n")
    for index in range(1, len(lines)):
        if not _STATUS_LINE.match(lines[index]):
            continue
        rest = lines[index + 1 :]
        if not rest or rest[0] != "":
            continue
        return LocalShellRecord(
            command="\n".join(lines[:index]),
            status_line=lines[index],
            output="\n".join(rest[1:]),
        )
    return None
