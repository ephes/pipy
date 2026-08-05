"""The slash-command menu: what `/` can complete, and what each entry runs.

The menu must be honest about dispatch. A name can come from the built-in set,
a discovered prompt template, a discovered custom command, or an activated
extension, and `dispatch_resource_command` resolves collisions in that order.
The descriptions are layered in the *reverse* order so a higher-precedence
source overwrites a colliding entry -- the menu then describes what pressing
Enter will actually run, rather than whichever source was discovered last.

Rebuilt on `/reload` as well as at startup, which is why it is a leaf both the
composition root and the reload owner can reach.
"""

from __future__ import annotations

from pipy_harness.native.repl_input import DEFAULT_REPL_COMMAND_DESCRIPTIONS
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.session_generation import ExtensionCommandProjection
from pipy_harness.native.tui import TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS
from pipy_harness.native.ui.autocomplete import CommandSurface


def tool_loop_command_names(
    resources: WorkspaceResources,
    extension_command_names: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Tool-loop slash-menu command set, honest to what can execute.

    The static built-in set is augmented with the ``/skill`` resource
    entry point (which always at least lists), every discovered prompt
    template registered as its own ``/<name>`` command (Pi shape), every
    discovered, non-reserved custom ``/<name>`` command, and any activated
    extension ``/<name>`` commands (appended last, never shadowing a
    built-in or custom command).
    """

    names = list(TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS)
    insert_at = (names.index("/model") + 1) if "/model" in names else len(names)
    names[insert_at:insert_at] = ["/skill"]
    for slash_name in resources.template_slash_names():
        if slash_name not in names:
            names.append(slash_name)
    for slash_name in resources.custom_command_slash_names():
        if slash_name not in names:
            names.append(slash_name)
    for slash_name in extension_command_names:
        if slash_name not in names:
            names.append(slash_name)
    return tuple(names)


def tool_loop_command_descriptions(
    resources: WorkspaceResources,
    extension_descriptions: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the slash-menu descriptions with dispatch-honest precedence.

    The menu description for a name must describe what dispatching that name
    actually runs. ``dispatch_resource_command`` resolves a colliding name in
    the order built-in > prompt template > custom command, and extension
    commands dispatch last (lowest precedence). Descriptions are layered in
    the reverse order (lowest precedence first) so a later ``update`` for a
    higher-precedence source wins a collision — i.e. for a name shared by a
    template and a custom command, the menu shows the *template's*
    description, matching what runs.
    """

    descriptions: dict[str, str] = {}
    if extension_descriptions:
        descriptions.update(extension_descriptions)
    descriptions.update(resources.custom_command_descriptions())
    descriptions.update(resources.template_descriptions())
    descriptions.update(DEFAULT_REPL_COMMAND_DESCRIPTIONS)
    return descriptions


def published_command_surface(
    resources: WorkspaceResources,
    commands: ExtensionCommandProjection,
) -> CommandSurface:
    """The one frozen command surface a generation publishes to the menu owner.

    Startup and ``/reload`` derive names, descriptions, and extension shortcut
    keys from the same resources + command projection and hand the result to
    ``AutocompleteComponent.replace_command_surface`` in one motion; building
    the record here keeps the two writers byte-identical.
    """

    return CommandSurface(
        names=tool_loop_command_names(resources, commands.menu_names),
        descriptions=tool_loop_command_descriptions(
            resources, dict(commands.descriptions)
        ),
        shortcut_keys=frozenset(commands.shortcuts),
    )
