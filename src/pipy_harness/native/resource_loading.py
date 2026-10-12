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
    extension_flag_tokens: tuple[str, ...] = ()
    # ``--no-mcp``: MCP is off for this run, whatever else says.
    no_mcp: bool = False
    # Built-in extensions named with ``--extension builtin:<name>``.
    builtin_extensions: tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> "RuntimeResourceOptions":
        return cls()

    def mcp_enabled(self, *, configured: bool) -> bool:
        """Whether this run has MCP (the built-in extension ``mcp``).

        ``configured`` is what the ``extensions`` settings say. ``--no-mcp``
        turns it off; otherwise ``--extension builtin:mcp`` turns it on, also
        with ``--no-extensions``, which turns it off like the settings can.
        """

        if self.no_mcp:
            return False
        if "mcp" in self.builtin_extensions:
            return True
        return configured and not self.no_extensions
