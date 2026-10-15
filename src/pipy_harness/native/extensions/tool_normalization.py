"""Normalization leaf for extension tool registration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from pipy_harness.native.extension_types import (
    REASON_INVALID_TOOL,
    ExtensionTool,
    RegisteredTool,
    _ActivationError,
    _safe_diagnostic,
)
from pipy_harness.native.tools.base import (
    ToolDefinition,
    ToolExposure,
    materialize_tool_input_schema,
)


def _normalize_extension_tool(
    tool: ExtensionTool, *, name: str, extension: str
) -> RegisteredTool:
    """Validate extension-controlled non-name values without the host guard.

    The values are held to the native `ToolDefinition` rules. The input schema
    is any JSON Schema object with `type: "object"` (`schema_mode="foreign"`,
    as an MCP server tool's: Pi accepts any TypeBox schema, with `number`,
    `null`, unions and `$ref`), and the metadata (`exposure`, `namespace`,
    `annotations`, `output_schema`, `reaches_tools`, `default_active`,
    `prepare_loadout`, `prompt_snippet`, `prompt_guidelines`,
    `execution_mode`) to the shapes that definition accepts.
    `prepare_arguments` must be callable or None.
    """

    description = tool.description
    input_schema = tool.input_schema
    handler = tool.handler
    exposure = tool.exposure
    output_schema = tool.output_schema
    if not callable(handler) or not isinstance(input_schema, Mapping):
        raise _ActivationError(REASON_INVALID_TOOL)
    if type(exposure) is not str or not (
        output_schema is None or isinstance(output_schema, Mapping)
    ):
        raise _ActivationError(REASON_INVALID_TOOL)
    if tool.prepare_arguments is not None and not callable(tool.prepare_arguments):
        raise _ActivationError(REASON_INVALID_TOOL)
    try:
        normalized_description = str(description)
        # Detached copies: the declared schemas cannot change after
        # registration.
        normalized_schema = materialize_tool_input_schema(input_schema)
        normalized_output_schema = (
            None
            if output_schema is None
            else materialize_tool_input_schema(output_schema)
        )
        definition = ToolDefinition(
            name=name,
            description=normalized_description,
            input_schema=normalized_schema,
            exposure=cast(ToolExposure, exposure),
            namespace=tool.namespace,
            annotations=tool.annotations,
            output_schema=normalized_output_schema,
            reaches_tools=tool.reaches_tools,
            default_active=tool.default_active,
            prepare_loadout=tool.prepare_loadout,
            prompt_snippet=tool.prompt_snippet,
            prompt_guidelines=cast(tuple[str, ...], tool.prompt_guidelines),
            execution_mode=tool.execution_mode,
            schema_mode="foreign",
        )
    except (ValueError, TypeError) as exc:
        raise _ActivationError(REASON_INVALID_TOOL, _safe_diagnostic(exc)) from None
    normalized = replace(
        tool,
        name=name,
        description=normalized_description,
        input_schema=normalized_schema,
        exposure=definition.exposure,
        namespace=definition.namespace,
        annotations=definition.annotations,
        output_schema=normalized_output_schema,
        reaches_tools=definition.reaches_tools,
        default_active=definition.default_active,
        prepare_loadout=definition.prepare_loadout,
        prompt_snippet=definition.prompt_snippet,
        prompt_guidelines=definition.prompt_guidelines,
        execution_mode=definition.execution_mode,
    )
    return RegisteredTool(tool=normalized, extension=extension)
