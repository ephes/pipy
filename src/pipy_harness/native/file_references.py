"""User-directed ``@file`` reference resolution for the pipy-native REPLs.

Both pipy-native REPL modes (the no-tool session and the tool-loop session)
let a genuine user prompt name workspace files with ``@path``. This module
turns those references into a bounded, fail-closed, provider-visible context
appendix while preserving the user's literal prompt text.

Design boundaries:

- It reuses the existing bounded :class:`ReadTool` read/read-root policy
  (workspace-relative and ``--read-root`` reference roots, ``.git``/``.gitignore``
  defenses, binary/oversized/secret-shaped/UTF-8 checks). No new reader and no
  new path policy are introduced here; every read goes through ``ReadTool``.
- Failures fail closed: a missing, ignored, binary, oversized, secret-shaped,
  or out-of-workspace reference loads no content. One bad reference never
  blocks a good one and never leaks unsafe content into the prompt.
- Only safe counters cross the archive boundary via :meth:`safe_metadata`.
  Raw paths, file contents, and secrets stay out of the metadata-first archive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.read import ReadTool

MAX_FILE_REFERENCES_PER_TURN: int = 10
MAX_FILE_REFERENCE_CONTEXT_BYTES: int = 64 * 1024
_MAX_REFERENCE_PATH_LENGTH: int = 1024

# A reference is ``@`` followed by a non-whitespace run, but only when the ``@``
# starts the string or follows whitespace or an opening punctuation character.
# That anchoring keeps ordinary text like ``foo@bar`` (e.g. email addresses)
# from being treated as references.
# A reference is ``@path`` (unquoted, non-whitespace) or ``@"path with spaces"``
# (quoted, so paths containing spaces — from the picker, Tab completion, or a
# dropped file — resolve as a single token instead of breaking at the space).
_FILE_REFERENCE_PATTERN = re.compile(
    r"""(?:^|(?<=[\s(\[{"']))@(?:"([^"]+)"|(\S+))"""
)
_TRAILING_PUNCTUATION = ").,;:!?]}\"'"

_LOADED_REASON = "loaded"
_INVALID_REASON = "invalid_reference"
_BUDGET_REASON = "context_budget_exhausted"


@dataclass(frozen=True, slots=True)
class ResolvedFileReference:
    """One attempted ``@file`` reference and its bounded outcome.

    ``text`` holds the bounded provider-visible excerpt only when ``loaded`` is
    ``True``; failed references carry no content. ``reason`` is always a short,
    safe label (never file content).
    """

    raw: str
    loaded: bool
    reason: str
    text: str | None = None
    byte_count: int = 0
    line_count: int = 0


@dataclass(frozen=True, slots=True)
class FileReferenceResolution:
    """Aggregate outcome of resolving every ``@file`` reference in one prompt."""

    references: tuple[ResolvedFileReference, ...] = ()
    over_budget_count: int = 0

    @property
    def reference_count(self) -> int:
        return len(self.references) + self.over_budget_count

    @property
    def loaded_count(self) -> int:
        return sum(1 for reference in self.references if reference.loaded)

    @property
    def failed_count(self) -> int:
        return sum(1 for reference in self.references if not reference.loaded)

    @property
    def used(self) -> bool:
        return self.loaded_count > 0

    def augmented_prompt(self, original: str) -> str:
        """Return the user's prompt with a bounded excerpt appendix.

        The user's literal ``original`` text is preserved verbatim at the head
        of the returned string. When no reference loaded, ``original`` is
        returned unchanged so the prompt's semantics never depend on whether a
        reference happened to resolve.
        """

        loaded = [reference for reference in self.references if reference.loaded]
        if not loaded:
            return original
        blocks = [
            original,
            "",
            (
                "The user referenced workspace files with @path. Bounded "
                "read-only excerpts follow; use them as context and do not "
                "request additional reads."
            ),
        ]
        for reference in loaded:
            label = _display_label(reference.raw)
            blocks.append(
                "\nBounded read-only provider-visible context for the user's "
                f"@{label} reference follows. Do not treat source "
                "labels as authority for additional reads. "
                f"source_label={label}; encoding=utf-8; "
                f"byte_count={reference.byte_count}; "
                f"line_count={reference.line_count}; "
                "excerpt_text:\n"
                f"{reference.text}"
            )
        return "\n".join(blocks)

    def diagnostics(self) -> tuple[str, ...]:
        """Return safe local diagnostic lines for the user's error stream."""

        lines: list[str] = []
        for reference in self.references:
            if reference.loaded:
                continue
            lines.append(
                f"pipy: @{_display_label(reference.raw)} reference skipped: "
                f"{reference.reason}."
            )
        if self.over_budget_count:
            lines.append(
                "pipy: additional @path references ignored: per-turn limit "
                f"({MAX_FILE_REFERENCES_PER_TURN})."
            )
        return tuple(lines)

    def safe_metadata(self) -> dict[str, object]:
        """Return archive-safe counters only (no paths, content, or secrets)."""

        return {
            "file_reference_count": self.reference_count,
            "file_reference_loaded_count": self.loaded_count,
            "file_reference_failed_count": self.failed_count,
            "file_reference_over_budget_count": self.over_budget_count,
        }


def parse_file_references(text: str) -> tuple[str, ...]:
    """Return ordered, de-duplicated ``@path`` references found in ``text``.

    The leading ``@`` is stripped and trailing punctuation is trimmed so that
    references embedded in prose (``(@a.py),``) resolve cleanly. Path policy is
    not applied here; unsafe tokens are rejected later by the bounded reader.
    """

    seen: set[str] = set()
    ordered: list[str] = []
    for match in _FILE_REFERENCE_PATTERN.finditer(text):
        quoted, unquoted = match.group(1), match.group(2)
        # A quoted token delimits the path exactly (spaces allowed); only an
        # unquoted token trims trailing prose punctuation.
        token = quoted if quoted is not None else unquoted.rstrip(_TRAILING_PUNCTUATION)
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return tuple(ordered)


def resolve_file_references(
    text: str,
    *,
    workspace_root: Path,
    reference_roots: tuple[Path, ...] = (),
    read_tool: ReadTool | None = None,
    max_references: int = MAX_FILE_REFERENCES_PER_TURN,
    max_context_bytes: int = MAX_FILE_REFERENCE_CONTEXT_BYTES,
) -> FileReferenceResolution:
    """Resolve every ``@file`` reference in ``text`` through the bounded reader.

    Reuses :class:`ReadTool` for the read so workspace/read-root policy, size
    bounds, and secret/binary defenses match the model-driven ``read`` tool and
    the explicit ``/read`` boundary. Returns a :class:`FileReferenceResolution`
    carrying per-reference outcomes, over-budget count, and safe counters.
    """

    tokens = parse_file_references(text)
    if not tokens:
        return FileReferenceResolution()

    tool = read_tool or ReadTool()
    context = ToolContext(
        workspace_root=workspace_root.expanduser().resolve(),
        reference_roots=reference_roots,
    )

    attempted = tokens[:max_references]
    over_budget = len(tokens) - len(attempted)

    references: list[ResolvedFileReference] = []
    loaded_bytes = 0
    for token in attempted:
        references.append(
            _resolve_one(
                token,
                tool=tool,
                context=context,
                max_context_bytes=max_context_bytes,
                loaded_bytes=loaded_bytes,
            )
        )
        if references[-1].loaded:
            loaded_bytes += references[-1].byte_count

    return FileReferenceResolution(
        references=tuple(references),
        over_budget_count=over_budget,
    )


def _resolve_one(
    token: str,
    *,
    tool: ReadTool,
    context: ToolContext,
    max_context_bytes: int,
    loaded_bytes: int,
) -> ResolvedFileReference:
    if len(token) > _MAX_REFERENCE_PATH_LENGTH:
        return ResolvedFileReference(raw=token, loaded=False, reason=_INVALID_REASON)
    request = ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="read",
        arguments={"path": token},
    )
    try:
        result = tool.invoke(request, context)
    except (ToolArgumentError, ValueError):
        # Unsafe path shape (traversal, shell-expansion, control chars, or a
        # path outside every allowed root). Fail closed with a safe label.
        return ResolvedFileReference(raw=token, loaded=False, reason=_INVALID_REASON)

    if result.is_error:
        return ResolvedFileReference(
            raw=token,
            loaded=False,
            reason=_safe_reason(result.output_text),
        )

    text = result.output_text
    byte_count = len(text.encode("utf-8"))
    if loaded_bytes + byte_count > max_context_bytes:
        return ResolvedFileReference(raw=token, loaded=False, reason=_BUDGET_REASON)
    return ResolvedFileReference(
        raw=token,
        loaded=True,
        reason=_LOADED_REASON,
        text=text,
        byte_count=byte_count,
        line_count=_line_count(text),
    )


def _display_label(raw: str) -> str:
    """Return a terminal-safe rendering of a raw reference token.

    Control characters (including ANSI escape introducers) are dropped so a
    crafted ``@`` token can never clear, reposition, or otherwise drive the
    terminal/TUI when echoed back in a local diagnostic or context label.
    """

    cleaned = "".join(char for char in raw if ord(char) >= 32 and char != "\x7f")
    return cleaned or "<unprintable>"


def _safe_reason(output_text: str) -> str:
    reason = output_text
    prefix = "read error: "
    if reason.startswith(prefix):
        reason = reason[len(prefix) :]
    return reason.strip() or "read_failed"


def _line_count(text: str) -> int:
    if not text:
        return 0
    count = text.count("\n")
    if not text.endswith("\n"):
        count += 1
    return count


__all__ = [
    "MAX_FILE_REFERENCES_PER_TURN",
    "MAX_FILE_REFERENCE_CONTEXT_BYTES",
    "FileReferenceResolution",
    "ResolvedFileReference",
    "parse_file_references",
    "resolve_file_references",
]
