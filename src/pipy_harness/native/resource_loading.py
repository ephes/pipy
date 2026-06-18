"""Per-run resource-loading options for Pi-shaped CLI flags.

These options are intentionally ephemeral: they are built from CLI flags such
as ``--extension`` / ``--no-extensions`` and affect only the current run. The
settings-backed package/resource filters remain the persisted source of truth.
Explicit paths are session overrides: they survive both the matching
``--no-*`` default-discovery cutoff and persisted resource-name disable filters.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeResourceOptions:
    """Temporary resource-source controls for one native REPL run."""

    extension_paths: tuple[Path, ...] = ()
    skill_paths: tuple[Path, ...] = ()
    prompt_template_paths: tuple[Path, ...] = ()
    theme_paths: tuple[Path, ...] = ()
    no_extensions: bool = False
    no_skills: bool = False
    no_prompt_templates: bool = False
    no_themes: bool = False

    @classmethod
    def empty(cls) -> "RuntimeResourceOptions":
        return cls()
