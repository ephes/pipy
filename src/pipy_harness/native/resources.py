"""Runtime resource registry and slash-command dispatcher.

This is the pipy-owned runtime consumer that turns the display-only
`.pipy/skills`, `.pipy/templates`, and `.pipy/commands` discovery
loaders into executable resources for both REPL product paths (the
no-tool line editor and the bounded tool loop / product TUI).

`WorkspaceResources.discover` runs the three loaders once for a
workspace + global root. `dispatch_resource_command` is a pure
function shared by both REPL paths: given a typed line and the
discovered resources it returns a `ResourceDispatch` describing the
local-command outcome — list, run (a bounded provider-visible
message), or reject (fail closed, no provider turn) — or `None` when
the line is not a resource command and normal dispatch should handle
it.

Privacy: the only data a caller should record for the archive is the
`safe_metadata` projection on a `ResourceDispatch` (path label,
sha256, byte length, truncated, resource name + kind). The
`provider_text` is the bounded instruction/expansion that goes to the
provider boundary and must never reach the metadata archive, prompt
history, or transcript sidecar body.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipy_harness.native.package_resources import PackageResourceRoots

from pipy_harness.native.custom_commands import (
    CustomSlashCommand,
    discover_workspace_custom_commands,
    find_custom_command_by_name,
    safe_custom_command_metadata,
)
from pipy_harness.native.prompt_templates import (
    PromptTemplate,
    discover_workspace_prompt_templates,
    expand_template_body,
    find_template_by_name,
)
from pipy_harness.native.skills import (
    SkillFile,
    discover_workspace_skills,
    find_skill_by_name,
)
from pipy_harness.native._resource_files import (
    CLI_PATH_LABEL_PREFIX,
    safe_resource_metadata,
)

SKILL_RESOURCE_COMMAND = "/skill"
TEMPLATE_RESOURCE_COMMAND = "/template"

# Built-in slash-command names (without the leading slash) that a custom
# command may never shadow. The dispatcher is always consulted after the
# built-in handlers, but excluding collisions keeps custom commands out of
# slash discovery / the menu so the surface stays honest.
RESERVED_COMMAND_NAMES: frozenset[str] = frozenset(
    {
        "help",
        "clear",
        "status",
        "settings",
        "login",
        "logout",
        "model",
        "theme",
        "copy",
        "exit",
        "quit",
        "skill",
        "template",
    }
)

# Dispatch kinds.
DISPATCH_LIST = "list"
DISPATCH_SKILL_RUN = "skill_run"
DISPATCH_TEMPLATE_RUN = "template_run"
DISPATCH_COMMAND_RUN = "command_run"
DISPATCH_REJECT = "reject"

_RUN_KINDS = frozenset({DISPATCH_SKILL_RUN, DISPATCH_TEMPLATE_RUN, DISPATCH_COMMAND_RUN})


def _is_executable_command_token(name: str) -> bool:
    """Return True when `name` can run as a ``/<name>`` slash command.

    A custom command is dispatched as ``/<name> [args]`` where the
    command name is the first whitespace-delimited token. A name with
    embedded whitespace or a slash can never match that token, so it is
    not executable and must not be advertised. Reserved built-in names
    are also rejected so a custom command can never shadow a built-in.
    """

    return bool(
        name
        and name not in RESERVED_COMMAND_NAMES
        and "/" not in name
        and not any(ch.isspace() for ch in name)
    )


@dataclass(frozen=True, slots=True)
class ResourceDispatch:
    """Outcome of dispatching one typed line against the resources.

    - ``kind == DISPATCH_LIST``: print ``message`` locally; no provider
      turn.
    - ``kind in _RUN_KINDS``: send ``provider_text`` as a bounded
      provider-visible message; record ``safe_metadata`` for the
      archive. ``provider_text`` is never empty for a run.
    - ``kind == DISPATCH_REJECT``: print ``message`` locally and fail
      closed; no provider turn. ``safe_metadata`` may carry the
      attempted resource name/label for the audit trail.
    """

    kind: str
    message: str = ""
    provider_text: str | None = None
    safe_metadata: dict[str, object] | None = None
    resource_label: str = ""

    @property
    def is_run(self) -> bool:
        return self.kind in _RUN_KINDS

    @property
    def is_reject(self) -> bool:
        return self.kind == DISPATCH_REJECT


@dataclass(frozen=True, slots=True)
class WorkspaceResources:
    """Discovered workspace + global skills, templates, and commands."""

    skills: tuple[SkillFile, ...]
    templates: tuple[PromptTemplate, ...]
    commands: tuple[CustomSlashCommand, ...]
    skills_cap_reached: bool
    templates_cap_reached: bool
    commands_cap_reached: bool

    @classmethod
    def discover(
        cls,
        workspace_root: Path,
        *,
        config_home_env: Mapping[str, str] | None = None,
        home_dir: Path | None = None,
        package_roots: "PackageResourceRoots | None" = None,
        explicit_skill_paths: Sequence[Path] = (),
        explicit_prompt_template_paths: Sequence[Path] = (),
        include_skills_defaults: bool = True,
        include_prompt_template_defaults: bool = True,
    ) -> "WorkspaceResources":
        skill_pkg_roots = package_roots.skills if package_roots is not None else ()
        prompt_pkg_roots = package_roots.prompts if package_roots is not None else ()
        skills, skills_cap = discover_workspace_skills(
            workspace_root,
            config_home_env=config_home_env,
            home_dir=home_dir,
            package_roots=skill_pkg_roots,
            explicit_paths=explicit_skill_paths,
            include_defaults=include_skills_defaults,
        )
        templates, templates_cap = discover_workspace_prompt_templates(
            workspace_root,
            config_home_env=config_home_env,
            home_dir=home_dir,
            package_roots=prompt_pkg_roots,
            explicit_paths=explicit_prompt_template_paths,
            include_defaults=include_prompt_template_defaults,
        )
        commands, commands_cap = discover_workspace_custom_commands(
            workspace_root,
            config_home_env=config_home_env,
            home_dir=home_dir,
        )
        return cls(
            skills=tuple(skills),
            templates=tuple(templates),
            commands=tuple(commands),
            skills_cap_reached=skills_cap,
            templates_cap_reached=templates_cap,
            commands_cap_reached=commands_cap,
        )

    def with_enablement(
        self,
        *,
        skills_patterns: list[str] | None = None,
        prompts_patterns: list[str] | None = None,
        enable_skill_commands: bool = True,
    ) -> "WorkspaceResources":
        """Return a copy with disabled resources removed (Pi `pi config` model).

        `skills_patterns` / `prompts_patterns` are the settings `-pattern`/
        `+pattern` directive arrays; a discovered skill/template whose name is
        disabled by them is dropped from what is registered (its file is left on
        disk). Explicit per-run CLI resources (`--skill` /
        `--prompt-template`) are session overrides for `+/-pattern` filters and
        remain enabled even when a persisted pattern disables the same resource
        name. `enable_skill_commands=False` is still a hard command-surface
        disable for skills.
        """

        from pipy_harness.native.resource_enablement import is_resource_enabled

        if not enable_skill_commands:
            kept_skills: tuple[SkillFile, ...] = ()
        elif skills_patterns:
            kept_skills = tuple(
                s
                for s in self.skills
                if _is_cli_resource_label(s.path_label)
                or is_resource_enabled(s.name, skills_patterns)
            )
        else:
            kept_skills = self.skills
        if prompts_patterns:
            kept_templates = tuple(
                t
                for t in self.templates
                if _is_cli_resource_label(t.path_label)
                or is_resource_enabled(t.name, prompts_patterns)
            )
        else:
            kept_templates = self.templates
        return WorkspaceResources(
            skills=kept_skills,
            templates=kept_templates,
            commands=self.commands,
            skills_cap_reached=self.skills_cap_reached,
            templates_cap_reached=self.templates_cap_reached,
            commands_cap_reached=self.commands_cap_reached,
        )

    def has_any(self) -> bool:
        return bool(self.skills or self.templates or self.commands)

    def skill_names(self) -> tuple[str, ...]:
        return tuple(skill.name for skill in self.skills)

    def template_names(self) -> tuple[str, ...]:
        return tuple(template.name for template in self.templates)

    def custom_command_slash_names(self) -> tuple[str, ...]:
        """Return ``/<name>`` forms for executable custom commands.

        Only commands whose name is a valid single slash token are
        advertised: the dispatcher invokes a custom command as
        ``/<name> [args]`` and splits the first whitespace-delimited
        token as the command name, so a name containing whitespace (or
        a slash) could never be invoked and must not appear in slash
        discovery. Names that collide with a reserved built-in or
        duplicate an earlier command are also dropped so the surface
        only advertises commands that can actually run.
        """

        names: list[str] = []
        seen: set[str] = set()
        for command in self.commands:
            name = command.name
            if not _is_executable_command_token(name) or name in seen:
                continue
            seen.add(name)
            names.append(f"/{name}")
        return tuple(names)

    def custom_command_descriptions(self) -> dict[str, str]:
        """Map ``/<name>`` to a menu description for executable commands."""

        descriptions: dict[str, str] = {}
        for command in self.commands:
            if not _is_executable_command_token(command.name):
                continue
            slash = f"/{command.name}"
            if slash in descriptions:
                continue
            descriptions[slash] = command.description or "Custom command"
        return descriptions

    def safe_skill_metadata_all(self) -> list[dict[str, object]]:
        return safe_resource_metadata(self.skills)

    def safe_template_metadata_all(self) -> list[dict[str, object]]:
        return safe_resource_metadata(self.templates)


def _is_cli_resource_label(path_label: str) -> bool:
    """Return True for resources loaded from explicit per-run CLI paths."""

    return path_label.startswith(CLI_PATH_LABEL_PREFIX)


def format_skills_listing(skills: Sequence[SkillFile]) -> str:
    """Local listing text for ``/skill`` (no provider turn).

    Only names and descriptions appear; bodies never leak.
    """

    if not skills:
        return "pipy: no skills found. Add Markdown files under .pipy/skills/."
    lines = ["pipy: available skills (load with /skill <name>):"]
    for skill in skills:
        if skill.description:
            lines.append(f"  {skill.name}: {skill.description}")
        else:
            lines.append(f"  {skill.name}")
    return "\n".join(lines)


def format_templates_listing(templates: Sequence[PromptTemplate]) -> str:
    """Local listing text for ``/template`` (no provider turn)."""

    if not templates:
        return (
            "pipy: no prompt templates found. "
            "Add Markdown files under .pipy/templates/."
        )
    lines = ["pipy: available prompt templates (run with /template <name> [args]):"]
    for template in templates:
        if template.description:
            lines.append(f"  {template.name}: {template.description}")
        else:
            lines.append(f"  {template.name}")
    return "\n".join(lines)


def _split_command(stripped: str) -> tuple[str, str]:
    """Split ``/foo bar baz`` into (``foo``, ``bar baz``)."""

    without_slash = stripped[1:]
    head, sep, rest = without_slash.partition(" ")
    if not sep:
        head, sep, rest = without_slash.partition("\t")
    return head, rest.strip()


def _skill_metadata(skill: SkillFile) -> dict[str, object]:
    return {
        "resource_kind": "skill",
        "name": skill.name,
        "path_label": skill.path_label,
        "sha256": skill.sha256,
        "byte_length": skill.byte_length,
        "truncated": skill.truncated,
    }


def _template_metadata(template: PromptTemplate) -> dict[str, object]:
    return {
        "resource_kind": "prompt_template",
        "name": template.name,
        "path_label": template.path_label,
        "sha256": template.sha256,
        "byte_length": template.byte_length,
        "truncated": template.truncated,
    }


def _command_metadata(command: CustomSlashCommand) -> dict[str, object]:
    meta = safe_custom_command_metadata(command)
    meta["resource_kind"] = "custom_command"
    return meta


def dispatch_resource_command(
    line: str,
    resources: WorkspaceResources,
) -> ResourceDispatch | None:
    """Resolve a typed line against the workspace resources.

    Returns ``None`` when the line is not a resource command (so the
    caller's normal dispatch — including the built-in fail-closed
    handling for unknown ``/`` commands — runs). Otherwise returns a
    `ResourceDispatch`.

    This function must be consulted **after** the built-in command
    handlers so a custom command can never shadow a built-in.
    """

    stripped = line.strip()
    if not stripped.startswith("/"):
        return None
    name_token, arguments = _split_command(stripped)

    if name_token == "skill":
        if not arguments:
            return ResourceDispatch(
                kind=DISPATCH_LIST,
                message=format_skills_listing(resources.skills),
            )
        # A skill takes no arguments, so the whole argument string is the
        # skill name. This lets multi-word skill names (which the listing
        # shows) actually load, keeping the listing honest.
        target = arguments.strip()
        skill = find_skill_by_name(resources.skills, target)
        if skill is None:
            return ResourceDispatch(
                kind=DISPATCH_REJECT,
                message=(
                    f"pipy: no skill named {target!r}. "
                    "Run /skill to list available skills."
                ),
                resource_label=f"skill:{target}",
            )
        if not skill.body.strip():
            return ResourceDispatch(
                kind=DISPATCH_REJECT,
                message=(
                    f"pipy: skill {skill.name!r} has no instruction body to load."
                ),
                safe_metadata=_skill_metadata(skill),
                resource_label=f"skill:{skill.name}",
            )
        return ResourceDispatch(
            kind=DISPATCH_SKILL_RUN,
            provider_text=skill.body,
            safe_metadata=_skill_metadata(skill),
            resource_label=f"skill:{skill.name}",
            message=f"pipy: loaded skill {skill.name!r}.",
        )

    if name_token == "template":
        if not arguments:
            return ResourceDispatch(
                kind=DISPATCH_LIST,
                message=format_templates_listing(resources.templates),
            )
        target, _, template_args = arguments.partition(" ")
        template = find_template_by_name(resources.templates, target)
        if template is None:
            return ResourceDispatch(
                kind=DISPATCH_REJECT,
                message=(
                    f"pipy: no prompt template named {target!r}. "
                    "Run /template to list available templates."
                ),
                resource_label=f"template:{target}",
            )
        expanded = expand_template_body(template.body, template_args.strip())
        if not expanded.strip():
            return ResourceDispatch(
                kind=DISPATCH_REJECT,
                message=(
                    f"pipy: template {template.name!r} expanded to empty text; "
                    "nothing to send."
                ),
                safe_metadata=_template_metadata(template),
                resource_label=f"template:{template.name}",
            )
        return ResourceDispatch(
            kind=DISPATCH_TEMPLATE_RUN,
            provider_text=expanded,
            safe_metadata=_template_metadata(template),
            resource_label=f"template:{template.name}",
            message=f"pipy: ran prompt template {template.name!r}.",
        )

    # Otherwise: a custom command invocation. Only claim the line when the
    # name resolves to a discovered, non-reserved command; otherwise return
    # None so the caller's unknown-command handling fails closed.
    if name_token in RESERVED_COMMAND_NAMES or not name_token:
        return None
    command = find_custom_command_by_name(resources.commands, name_token)
    if command is None:
        return None
    expanded = expand_template_body(command.body, arguments)
    if not expanded.strip():
        return ResourceDispatch(
            kind=DISPATCH_REJECT,
            message=(
                f"pipy: custom command /{command.name} expanded to empty text; "
                "nothing to send."
            ),
            safe_metadata=_command_metadata(command),
            resource_label=f"command:{command.name}",
        )
    return ResourceDispatch(
        kind=DISPATCH_COMMAND_RUN,
        provider_text=expanded,
        safe_metadata=_command_metadata(command),
        resource_label=f"command:{command.name}",
        message=f"pipy: ran custom command /{command.name}.",
    )
