"""System-prompt replace/append inputs (Pi parity).

Mirrors Pi's ``resolvePromptInput`` + ``buildSystemPrompt`` custom-prompt/append
behavior through pipy-owned Python:

- ``--system-prompt <text-or-file>`` replaces the default base prompt entirely;
  ``--append-system-prompt <text-or-file>`` (repeatable) appends after the
  base/custom prompt and before the project context files.
- A value is treated as a **file path when it names an existing file** (read
  **unbounded**, mirroring ``readFileSync``); otherwise it is literal text. An
  existing path that cannot be read warns and falls back to the literal input
  string (Pi does not fail closed).
- Auto-discovery files independent of the flags: project ``.pipy/SYSTEM.md``
  then global ``<config>/SYSTEM.md`` (replace), and project
  ``.pipy/APPEND_SYSTEM.md`` then global ``<config>/APPEND_SYSTEM.md`` (append).
  An explicit flag wins over the auto-discovered file.

Only safe metadata (source label, sha256, byte length) is exposed for the
session archive — never the prompt body, which can carry project content.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_CONFIG_DIR_NAME = ".pipy"
SYSTEM_PROMPT_FILENAME = "SYSTEM.md"
APPEND_SYSTEM_PROMPT_FILENAME = "APPEND_SYSTEM.md"


def _default_warn(message: str) -> None:  # pragma: no cover - replaced in tests
    import sys

    print(f"Warning: {message}", file=sys.stderr)


def resolve_prompt_input(
    value: str,
    *,
    cwd: Path,
    warn: Callable[[str], None] = _default_warn,
) -> tuple[str, bool]:
    """Resolve one prompt input to ``(text, was_file)``.

    The value is a file path when it names an existing file (resolved relative
    to ``cwd`` when not absolute), read unbounded. An existing path that cannot
    be read warns and falls back to the literal input string. Otherwise the
    value is literal text.
    """

    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        exists = candidate.exists()
    except OSError:
        exists = False
    if exists:
        try:
            return candidate.read_text(encoding="utf-8"), True
        except OSError as exc:
            warn(f"could not read system-prompt input file {value}: {exc}")
            return value, False
    return value, False


@dataclass(frozen=True, slots=True)
class SystemPromptInput:
    """Safe metadata describing one resolved prompt input (no body)."""

    source_label: str
    sha256: str
    byte_length: int

    @classmethod
    def of(cls, *, source_label: str, text: str) -> "SystemPromptInput":
        encoded = text.encode("utf-8")
        return cls(
            source_label=source_label,
            sha256=hashlib.sha256(encoded).hexdigest(),
            byte_length=len(encoded),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source_label": self.source_label,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
        }


@dataclass(frozen=True, slots=True)
class ResolvedSystemPrompt:
    """The effective base prompt plus safe metadata for the inputs used."""

    base_prompt: str
    replaced: bool = False
    replace_input: SystemPromptInput | None = None
    append_inputs: tuple[SystemPromptInput, ...] = field(default_factory=tuple)

    def safe_metadata(self) -> dict[str, object]:
        meta: dict[str, object] = {"system_prompt_replaced": self.replaced}
        if self.replace_input is not None:
            meta["system_prompt_replace"] = self.replace_input.as_dict()
        if self.append_inputs:
            meta["system_prompt_append"] = [inp.as_dict() for inp in self.append_inputs]
        return meta


def _discover(cwd: Path, config_home: Path, filename: str) -> tuple[str, str] | None:
    """Return ``(source_label, path)`` for the first existing discovery file."""

    project = cwd / PROJECT_CONFIG_DIR_NAME / filename
    if project.is_file():
        return f"{PROJECT_CONFIG_DIR_NAME}/{filename}", str(project)
    global_path = config_home / filename
    if global_path.is_file():
        return f"<config>/{filename}", str(global_path)
    return None


def resolve_system_prompt(
    default_prompt: str,
    *,
    cwd: Path,
    config_home: Path,
    system_prompt_source: str | None = None,
    append_sources: Sequence[str] | None = None,
    warn: Callable[[str], None] = _default_warn,
) -> ResolvedSystemPrompt:
    """Compute the effective base prompt and safe metadata (Pi parity).

    Replace precedence: explicit ``system_prompt_source`` (``--system-prompt``)
    wins over the auto-discovered ``SYSTEM.md``; otherwise the default prompt is
    kept. Append precedence: explicit ``append_sources``
    (``--append-system-prompt``, repeatable) wins over the auto-discovered
    ``APPEND_SYSTEM.md``. The append section is joined onto the base/custom
    prompt with blank lines, ahead of the project context files the caller adds
    afterwards.
    """

    # Replace.
    replace_input: SystemPromptInput | None = None
    base = default_prompt
    replaced = False
    if system_prompt_source is not None:
        text, _ = resolve_prompt_input(system_prompt_source, cwd=cwd, warn=warn)
        base = text
        replaced = True
        replace_input = SystemPromptInput.of(source_label="--system-prompt", text=text)
    else:
        discovered = _discover(cwd, config_home, SYSTEM_PROMPT_FILENAME)
        if discovered is not None:
            label, path = discovered
            text, _ = resolve_prompt_input(path, cwd=cwd, warn=warn)
            base = text
            replaced = True
            replace_input = SystemPromptInput.of(source_label=label, text=text)

    # Append.
    append_specs: list[tuple[str, str]] = []  # (source_label, raw_value)
    if append_sources is not None:
        append_specs = [("--append-system-prompt", value) for value in append_sources]
    else:
        discovered = _discover(cwd, config_home, APPEND_SYSTEM_PROMPT_FILENAME)
        if discovered is not None:
            label, path = discovered
            append_specs = [(label, path)]

    append_inputs: list[SystemPromptInput] = []
    append_texts: list[str] = []
    for label, value in append_specs:
        text, _ = resolve_prompt_input(value, cwd=cwd, warn=warn)
        append_texts.append(text)
        append_inputs.append(SystemPromptInput.of(source_label=label, text=text))

    if append_texts:
        base = base + "\n\n" + "\n\n".join(append_texts)

    return ResolvedSystemPrompt(
        base_prompt=base,
        replaced=replaced,
        replace_input=replace_input,
        append_inputs=tuple(append_inputs),
    )
