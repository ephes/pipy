"""Contracts for the native pipy model-driven tool loop.

This module names the small value objects and the `ToolPort` Protocol that
later slices of the Tool-Loop Parity Track depend on. It deliberately does not
wire any provider, REPL session, workspace effect, or archive boundary; it
only fixes the data shapes and validation surface.

Design boundaries:

- `ToolDefinition` carries model-visible tool name, description, and
  JSON-schema for arguments, plus the metadata that decides how a tool is
  reached (`exposure`, `namespace`, `annotations`, `output_schema`,
  `reaches_tools`) and the `schema_mode` its input schema is held to.
- `ToolRequest` carries the pipy-owned internal `tool_request_id` plus an
  optional `provider_correlation_id`. The internal id must not leak as a
  provider id; the provider id is opaque to pipy logic.
- `ToolExecutionResult` carries provider-visible payload and is kept strictly
  separate from the archive-safe `pipy_harness.native.models.NativeToolResult`
  metadata shape.
- `ToolContext` is the minimal environment passed into each invocation.
- `ToolArgumentError` is a `ValueError` subclass raised when arguments fail
  JSON-schema validation or an invariant check.
- `validate_arguments` validates a small JSON-schema subset against a mapping
  using only the standard library; no `pydantic`, no third-party schema
  runtime.

Supported JSON-schema subset (sufficient for `read`/`write`/`edit`/`ls`/
`grep`/`find`):

- `type`: `"object" | "string" | "integer" | "boolean" | "array"`
- on objects: `properties`, `required`, `additionalProperties` (default
  `false`)
- on arrays: `items` (schema for each element)
- on strings: `enum` (list of allowed values), `minLength`, `maxLength`
- on integers: `minimum`, `maximum`

Anything outside this subset raises a clear error at definition time.

A definition with `schema_mode="foreign"` carries a schema pipy did not write
(an MCP server's). It only has to be a JSON object with `type: "object"`, and
its arguments are checked by `tools/foreign_schema.py` instead.
"""

from __future__ import annotations

import json
import math
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, get_args, runtime_checkable

from pipy_harness.native.agent.identity import AGENT_TOOL_REQUEST_ID_PREFIX

_ALLOWED_TOP_LEVEL_TYPES = ("object",)
_ALLOWED_TYPES = ("object", "string", "integer", "boolean", "array")
_ALLOWED_OBJECT_KEYS = frozenset(
    {"type", "properties", "required", "additionalProperties", "description"}
)
_ALLOWED_ARRAY_KEYS = frozenset({"type", "items", "description"})
_ALLOWED_STRING_KEYS = frozenset(
    {"type", "enum", "minLength", "maxLength", "description"}
)
_ALLOWED_INTEGER_KEYS = frozenset({"type", "minimum", "maximum", "description"})
_ALLOWED_BOOLEAN_KEYS = frozenset({"type", "description"})

# Pi ``ToolExposure`` (``core/extensions/types.ts``): how the model reaches a
# tool. "Callable" means callable from another tool, as codemode scripts do.
#
# - ``direct``: declared to the model while active, and callable while active.
# - ``model-only``: declared to the model while active, never callable.
# - ``codemode``: callable whenever registered; not declared unless activated;
#   listed in the codemode description.
# - ``deferred``: like ``codemode``, but not listed; tool search can find it.
# - ``hidden``: registered but unreachable.
ToolExposure = Literal["direct", "model-only", "codemode", "deferred", "hidden"]
TOOL_EXPOSURES: tuple[str, ...] = get_args(ToolExposure)

# ``strict`` holds an input schema to the subset above; ``foreign`` accepts any
# JSON Schema object and leaves argument checks to ``validate_foreign_arguments``.
ToolSchemaMode = Literal["strict", "foreign"]
TOOL_SCHEMA_MODES: tuple[str, ...] = get_args(ToolSchemaMode)

# Pi ``reachesTools``: which tools a call with this input may reach. ``"all"``,
# or the words of the input that may name them (a word refers to a tool or
# namespace whose name it contains, so words may over-match).
ToolReach = Literal["all"] | tuple[str, ...]
ReachesTools = Callable[[Mapping[str, Any]], ToolReach]


class ToolArgumentError(ValueError):
    """Raised when tool arguments fail JSON-schema validation.

    The error carries the originating tool name and a structured field path so
    later slices of the tool loop can format a deterministic observation that
    is returned to the model without leaking raw arguments or model output.
    """

    def __init__(
        self,
        tool_name: str,
        message: str,
        *,
        field_path: tuple[str, ...] = (),
    ) -> None:
        if not tool_name:
            raise ValueError("ToolArgumentError requires a non-empty tool_name")
        if not message:
            raise ValueError("ToolArgumentError requires a non-empty message")
        self.tool_name = tool_name
        self.field_path = tuple(str(part) for part in field_path)
        path_label = "." + ".".join(self.field_path) if self.field_path else ""
        super().__init__(f"{tool_name}{path_label}: {message}")


def materialize_tool_input_schema(
    schema: Mapping[str, object],
) -> dict[str, object]:
    """Detach a validated tool schema into ordinary JSON containers.

    Canonical agent policy freezes schema mappings and sequences before handing
    them back to product adapters. Provider encoders require mutable ``dict`` /
    ``list`` containers, so this boundary recursively copies either form while
    rejecting cycles and values that JSON cannot represent.
    """

    active_containers: set[int] = set()
    materialized = _materialize_tool_schema_value(schema, active_containers)
    if not isinstance(materialized, dict):
        raise TypeError("tool input schema must materialize to an object")
    return materialized


def _materialize_tool_schema_value(
    value: object,
    active_containers: set[int],
) -> object:
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active_containers:
            raise TypeError("tool input schema must not contain cycles")
        active_containers.add(identity)
        try:
            result: dict[str, object] = {}
            for key, child in value.items():
                if type(key) is not str:
                    raise TypeError("tool input schema keys must be exact strings")
                result[key] = _materialize_tool_schema_value(child, active_containers)
            return result
        finally:
            active_containers.remove(identity)
    if isinstance(value, (list, tuple)) and type(value) in {list, tuple}:
        identity = id(value)
        if identity in active_containers:
            raise TypeError("tool input schema must not contain cycles")
        active_containers.add(identity)
        try:
            return [
                _materialize_tool_schema_value(child, active_containers)
                for child in value
            ]
        finally:
            active_containers.remove(identity)
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TypeError(
        "tool input schema values must be JSON mappings, sequences, or primitives"
    )


@dataclass(frozen=True, slots=True)
class ToolNamespace:
    """A group of related tools, such as the tools of one MCP server.

    Pi ``ToolNamespace``. `aliases` are other names that refer to the group
    where tools look it up by name. `description` is the short summary shown
    with the group in tool listings; `instructions` is longer guidance that is
    returned only when the namespace is described on request.
    """

    name: str
    aliases: tuple[str, ...] = ()
    description: str | None = None
    instructions: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ToolNamespace requires a non-empty name")
        if type(self.aliases) is not tuple or any(
            not isinstance(alias, str) or not alias for alias in self.aliases
        ):
            raise ValueError("ToolNamespace.aliases must be a tuple of names")
        for field_name in ("description", "instructions"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"ToolNamespace.{field_name} must be a string or None")


@dataclass(frozen=True, slots=True)
class ToolAnnotations:
    """Hints about what a tool does, with the meaning of MCP tool annotations.

    Pi ``ToolAnnotations``. They come from the tool's author and are not
    verified. `None` means the author gave no hint.
    """

    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    idempotent_hint: bool | None = None
    open_world_hint: bool | None = None

    def __post_init__(self) -> None:
        for field_name in self.__slots__:
            value = getattr(self, field_name)
            if value is not None and type(value) is not bool:
                raise ValueError(f"ToolAnnotations.{field_name} must be a bool or None")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Model-visible declaration of one tool.

    `input_schema` is a JSON-schema dict in the subset supported by
    `validate_arguments`. It is validated at construction time so later slices
    do not need to re-check definition shape. With `schema_mode="foreign"` it
    is any JSON Schema object with `type: "object"`, the description has no
    length cap, and arguments are checked by `validate_foreign_arguments`.

    The keyword-only fields are tool metadata with Pi's meaning:

    - `exposure`: how the model reaches the tool (see `ToolExposure`).
    - `namespace`: the group the tool belongs to.
    - `annotations`: the author's hints about what the tool does.
    - `output_schema`: JSON Schema of the `structured_content` in the tool's
      successful results. It is declared, not enforced.
    - `reaches_tools`: for a tool that reaches other tools, which ones a call
      with the given input may reach.
    - `default_active`: whether registering the tool activates it. A `direct`
      or `model-only` tool with `False` is registered inactive, until a tool
      selection names it.
    - `prepare_loadout`: for a tool that presents other tools, a function
      from a `ToolLoadout` to the `ToolLoadoutChanges` it wants while it is
      declared (`native/tools/loadout.py`).
    """

    name: str
    description: str
    input_schema: Mapping[str, Any]
    exposure: ToolExposure = field(default="direct", kw_only=True)
    namespace: ToolNamespace | None = field(default=None, kw_only=True)
    annotations: ToolAnnotations | None = field(default=None, kw_only=True)
    output_schema: Mapping[str, Any] | None = field(default=None, kw_only=True)
    reaches_tools: ReachesTools | None = field(default=None, kw_only=True)
    default_active: bool = field(default=True, kw_only=True)
    prepare_loadout: Callable[[Any], Any] | None = field(default=None, kw_only=True)
    schema_mode: ToolSchemaMode = field(default="strict", kw_only=True)

    NAME_MAX_LENGTH: ClassVar[int] = 64
    DESCRIPTION_MAX_LENGTH: ClassVar[int] = 4 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ToolDefinition requires a non-empty name")
        if len(self.name) > self.NAME_MAX_LENGTH:
            raise ValueError(
                f"ToolDefinition name exceeds {self.NAME_MAX_LENGTH} characters"
            )
        if not self.name.replace("_", "").isalnum():
            raise ValueError("ToolDefinition name must be alphanumeric or underscore")
        if not isinstance(self.description, str) or not self.description:
            raise ValueError("ToolDefinition requires a non-empty description")
        if self.schema_mode not in TOOL_SCHEMA_MODES:
            raise ValueError(
                f"ToolDefinition.schema_mode must be one of {TOOL_SCHEMA_MODES}"
            )
        foreign = self.schema_mode == "foreign"
        if not foreign and len(self.description) > self.DESCRIPTION_MAX_LENGTH:
            raise ValueError(
                "ToolDefinition description exceeds "
                f"{self.DESCRIPTION_MAX_LENGTH} characters"
            )
        if not isinstance(self.input_schema, Mapping):
            raise ValueError("ToolDefinition.input_schema must be a mapping")
        if foreign:
            _require_json_schema_object(self.input_schema, "input_schema")
            if self.input_schema.get("type") != "object":
                raise ValueError("ToolDefinition top-level schema must be an object")
        else:
            _validate_schema_shape(self.input_schema, top_level=True)
        self._validate_metadata()

    def _validate_metadata(self) -> None:
        if self.exposure not in TOOL_EXPOSURES:
            raise ValueError(f"ToolDefinition.exposure must be one of {TOOL_EXPOSURES}")
        if self.namespace is not None and type(self.namespace) is not ToolNamespace:
            raise ValueError("ToolDefinition.namespace must be a ToolNamespace or None")
        if (
            self.annotations is not None
            and type(self.annotations) is not ToolAnnotations
        ):
            raise ValueError(
                "ToolDefinition.annotations must be a ToolAnnotations or None"
            )
        if self.output_schema is not None:
            if not isinstance(self.output_schema, Mapping):
                raise ValueError("ToolDefinition.output_schema must be a mapping")
            _require_json_schema_object(self.output_schema, "output_schema")
        if self.reaches_tools is not None and not callable(self.reaches_tools):
            raise ValueError("ToolDefinition.reaches_tools must be callable or None")
        if type(self.default_active) is not bool:
            raise ValueError("ToolDefinition.default_active must be a bool")
        if self.prepare_loadout is not None and not callable(self.prepare_loadout):
            raise ValueError("ToolDefinition.prepare_loadout must be callable or None")


@dataclass(frozen=True, slots=True)
class ToolInfo:
    """One registered tool as an extension sees it (Pi ``ToolInfo``).

    `ctx.get_all_tools()` returns one per registered tool, whether or not the
    tool is active. `input_schema` is a detached copy. `reaches_tools` is the
    tool's reach function, or None when the tool reaches no other tools.
    """

    name: str
    description: str
    input_schema: Mapping[str, Any]
    exposure: ToolExposure
    namespace: ToolNamespace | None = None
    annotations: ToolAnnotations | None = None
    reaches_tools: ReachesTools | None = None


def tool_info(definition: ToolDefinition) -> ToolInfo:
    """Report one definition's extension-visible metadata."""

    return ToolInfo(
        name=definition.name,
        description=definition.description,
        input_schema=materialize_tool_input_schema(definition.input_schema),
        exposure=definition.exposure,
        namespace=definition.namespace,
        annotations=definition.annotations,
        reaches_tools=definition.reaches_tools,
    )


def _require_json_schema_object(schema: Mapping[str, Any], field_name: str) -> None:
    """Reject a schema that JSON cannot represent (or that contains a cycle)."""

    try:
        materialize_tool_input_schema(schema)
    except TypeError as exc:
        raise ValueError(f"ToolDefinition.{field_name} must be JSON: {exc}") from None


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """One pipy-owned invocation of a model-selected tool.

    `tool_request_id` is generated by pipy (use `make_tool_request_id()`) and
    must not be a provider id. `provider_correlation_id` is the opaque
    provider-side id, used only to round-trip back into the next provider
    message. Tools must not inspect or persist it.
    """

    tool_request_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    provider_correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool_request_id, str) or not self.tool_request_id:
            raise ValueError("ToolRequest requires a non-empty tool_request_id")
        if not self.tool_request_id.startswith(AGENT_TOOL_REQUEST_ID_PREFIX):
            raise ValueError(
                "ToolRequest tool_request_id must be pipy-owned "
                f"(prefix '{AGENT_TOOL_REQUEST_ID_PREFIX}')"
            )
        if not isinstance(self.tool_name, str) or not self.tool_name:
            raise ValueError("ToolRequest requires a non-empty tool_name")
        if not isinstance(self.arguments, Mapping):
            raise ValueError("ToolRequest.arguments must be a mapping")
        if self.provider_correlation_id is not None and (
            not isinstance(self.provider_correlation_id, str)
            or not self.provider_correlation_id
        ):
            raise ValueError(
                "ToolRequest.provider_correlation_id must be a non-empty string or None"
            )


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """Provider-visible result returned by a tool to the loop.

    This shape is deliberately distinct from
    `pipy_harness.native.models.NativeToolResult`. Archive-safe metadata stays
    in `NativeToolResult` and is never read out of `output_text`.
    """

    tool_request_id: str
    output_text: str
    is_error: bool = False
    provider_correlation_id: str | None = None
    # Pi ``AgentToolResult.details``: structured data for renderers and the
    # session file, never sent to a provider. A JSON object or None.
    details: Mapping[str, Any] | None = None
    # Pi ``AgentToolResult.structuredContent``: the machine-readable result of
    # a tool that declares an ``output_schema``, for programmatic callers such
    # as codemode scripts. Any JSON value; None means the tool gave none. It is
    # never sent to a provider and, as in Pi, never written to the session file.
    structured_content: Any = None

    OUTPUT_TEXT_MAX_LENGTH: ClassVar[int] = 64 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.tool_request_id, str) or not self.tool_request_id:
            raise ValueError("ToolExecutionResult requires a non-empty tool_request_id")
        if not self.tool_request_id.startswith(AGENT_TOOL_REQUEST_ID_PREFIX):
            raise ValueError(
                "ToolExecutionResult tool_request_id must be pipy-owned "
                f"(prefix '{AGENT_TOOL_REQUEST_ID_PREFIX}')"
            )
        if not isinstance(self.output_text, str):
            raise ValueError("ToolExecutionResult.output_text must be a string")
        if len(self.output_text) > self.OUTPUT_TEXT_MAX_LENGTH:
            raise ValueError(
                "ToolExecutionResult.output_text exceeds "
                f"{self.OUTPUT_TEXT_MAX_LENGTH} characters"
            )
        if not isinstance(self.is_error, bool):
            raise ValueError("ToolExecutionResult.is_error must be a bool")
        if self.provider_correlation_id is not None and (
            not isinstance(self.provider_correlation_id, str)
            or not self.provider_correlation_id
        ):
            raise ValueError(
                "ToolExecutionResult.provider_correlation_id must be a non-empty "
                "string or None"
            )
        if self.details is not None and not is_json_object(self.details):
            raise ValueError("ToolExecutionResult.details must be a JSON object")
        if self.structured_content is not None and not is_json_value(
            self.structured_content
        ):
            raise ValueError("ToolExecutionResult.structured_content must be JSON")


def is_json_value(value: object) -> bool:
    """Whether ``value`` is plain JSON data: no NaN, no non-string keys."""

    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return False
    return _has_only_string_keys(value)


def _has_only_string_keys(value: object) -> bool:
    # Iterative: a value the encoder accepts may nest deeper than Python
    # frames can follow.
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                return False
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return True


def is_json_object(value: object) -> bool:
    """Whether ``value`` is a mapping that JSON encodes as an object."""

    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        return False
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True


def json_safe_details(value: object) -> dict[str, Any] | None:
    """A detached JSON copy of ``value`` when it is a JSON object, else None.

    Pi writes ``details`` to the session file through ``JSON.stringify``;
    pipy keeps only what survives the same round trip, so live rows and
    restored rows render from the same value.
    """

    if not is_json_object(value):
        return None
    copied = json.loads(json.dumps(value, allow_nan=False))
    return copied if isinstance(copied, dict) else None


def json_safe_value(value: object) -> Any:
    """A detached JSON copy of ``value`` when it is JSON data, else None.

    The copy is what a tool result keeps as ``structured_content``, so a
    handler that goes on mutating its own value cannot change the result.
    """

    if value is None or not is_json_value(value):
        return None
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (ValueError, RecursionError):
        return None


# What a tool gets for a call to another tool where none can run (Pi's text).
NESTED_CALLS_UNAVAILABLE = "Nested tool calls are not available in this context"


@dataclass(frozen=True, slots=True)
class NestedToolResult:
    """What a tool gets back for a call it made to another tool.

    The result as the session's hooks left it. A failure of any kind, also a
    tool that is not callable, a blocked call or invalid arguments, is a
    result with ``is_error``.
    """

    content: str
    is_error: bool = False
    details: Mapping[str, Any] | None = None
    structured_content: Any = None


class NestedToolCalls(Protocol):
    """How a running tool calls other tools (Pi ``ctx.executeTool``).

    One object serves one invocation of one tool, from any thread, and stops
    answering when that invocation ended.
    """

    def tools(self) -> tuple[ToolInfo, ...]:
        """The tools ``execute`` can call, in registration order."""
        ...

    def execute(self, tool_name: str, arguments: object, /) -> NestedToolResult:
        """Run the tool and return its result. Never raises for a tool failure."""
        ...


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Environment passed to one tool invocation.

    `output_sink` is an optional callable that long-running tools (`bash`)
    use to stream incremental command output to the loop's live UI region as
    it is produced, matching Pi's live tool-output rendering. The default is
    `None`, in which case the tool still returns its full bounded result but
    emits nothing mid-run. The archive boundary is unrelated; streamed output
    never crosses it.

    `cancel_event` is set by the live TUI when the user interrupts an active
    tool run (Escape or a submitted local command such as `/quit`). Tools that
    can stop safely should observe it; the bash tool kills its process group.

    `extension_generation_id` is an internal product adapter value. A projected
    extension tool receives the generation selected for its complete invocation,
    so a retained context cannot mutate a successor generation after reload.
    Built-in and standalone tool calls leave it as `None`.

    `nested_tools` is set for a tool that may call other tools while it runs:
    an extension tool the model called. It is `None` for every other call,
    also for a call another tool made.
    """

    workspace_root: Path
    output_sink: Callable[[str], None] | None = field(default=None)
    cancel_event: threading.Event | None = field(default=None)
    extension_generation_id: int | None = field(default=None)
    nested_tools: NestedToolCalls | None = field(default=None)

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_root, Path):
            raise ValueError("ToolContext.workspace_root must be a Path")
        if not self.workspace_root.is_absolute():
            raise ValueError("ToolContext.workspace_root must be absolute")
        if self.output_sink is not None and not callable(self.output_sink):
            raise ValueError("ToolContext.output_sink must be callable or None")
        if self.cancel_event is not None and not hasattr(self.cancel_event, "is_set"):
            raise ValueError(
                "ToolContext.cancel_event must be an Event-like object or None"
            )


@runtime_checkable
class ToolPort(Protocol):
    """Minimal Protocol implemented by every model-driven tool.

    Tools must expose a stable `definition` (used by the loop to build the
    provider tool schema) and an `invoke(request, context)` method that
    returns a provider-visible `ToolExecutionResult`. Validation against the
    schema is performed by the loop (see `validate_arguments`) before
    `invoke()` runs, so implementations receive already-validated arguments.
    A tool may also define ``prepare_arguments(raw) -> raw``, which the
    executor applies to the parsed arguments before validation (Pi's
    ``prepareArguments``).
    """

    @property
    def definition(self) -> ToolDefinition: ...

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult: ...


def make_tool_request_id() -> str:
    """Return a fresh pipy-owned `tool_request_id`.

    The id is a UUID4 with a stable pipy-owned prefix. Callers must not reuse
    provider-supplied ids; the prefix lets later validation cheaply reject
    accidental cross-wiring.
    """

    return f"{AGENT_TOOL_REQUEST_ID_PREFIX}{uuid.uuid4()}"


def validate_arguments(
    *,
    tool_name: str,
    schema: Mapping[str, Any],
    arguments: Any,
) -> dict[str, Any]:
    """Validate `arguments` against the JSON-schema subset described by `schema`.

    Returns a defensive `dict` copy of the validated arguments. Raises
    `ToolArgumentError` if anything fails. The top-level schema must declare
    `type: "object"`.
    """

    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("validate_arguments requires a non-empty tool_name")
    if not isinstance(schema, Mapping):
        raise ValueError("validate_arguments schema must be a mapping")
    schema_type = schema.get("type")
    if schema_type not in _ALLOWED_TOP_LEVEL_TYPES:
        raise ValueError(
            "validate_arguments only supports object schemas at the top level"
        )
    validated = _validate_value(
        tool_name=tool_name,
        schema=schema,
        value=arguments,
        field_path=(),
    )
    assert isinstance(validated, dict)
    return validated


def _validate_object_schema_shape(schema: Mapping[str, Any]) -> None:
    for key in schema.keys():
        if key not in _ALLOWED_OBJECT_KEYS:
            raise ValueError(f"Unsupported object schema key: {key!r}")
    properties = _validate_object_properties(schema)
    _validate_object_requirements(schema, properties)


def _validate_object_properties(
    schema: Mapping[str, Any],
) -> Mapping[str, Any]:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ValueError("object schema 'properties' must be a mapping")
    for name, sub in properties.items():
        if not isinstance(name, str) or not name:
            raise ValueError("object property names must be non-empty strings")
        if not isinstance(sub, Mapping):
            raise ValueError(f"object property {name!r} schema must be a mapping")
        _validate_schema_shape(sub, top_level=False)
    return properties


def _validate_object_requirements(
    schema: Mapping[str, Any], properties: Mapping[str, Any]
) -> None:
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(item, str) for item in required
    ):
        raise ValueError("object schema 'required' must be a list of strings")
    for required_name in required:
        if required_name not in properties:
            raise ValueError(
                f"required key {required_name!r} is not declared in properties"
            )
    additional = schema.get("additionalProperties", False)
    if not isinstance(additional, bool):
        raise ValueError("object schema 'additionalProperties' must be a bool")


def _validate_array_schema_shape(schema: Mapping[str, Any]) -> None:
    for key in schema.keys():
        if key not in _ALLOWED_ARRAY_KEYS:
            raise ValueError(f"Unsupported array schema key: {key!r}")
    items = schema.get("items")
    if items is None:
        raise ValueError("array schema must declare 'items'")
    if not isinstance(items, Mapping):
        raise ValueError("array schema 'items' must be a mapping")
    _validate_schema_shape(items, top_level=False)


def _validate_string_schema_shape(schema: Mapping[str, Any]) -> None:
    for key in schema.keys():
        if key not in _ALLOWED_STRING_KEYS:
            raise ValueError(f"Unsupported string schema key: {key!r}")
    enum_values = schema.get("enum")
    if enum_values is not None:
        if not isinstance(enum_values, list) or not all(
            isinstance(item, str) for item in enum_values
        ):
            raise ValueError("string schema 'enum' must be a list of strings")
    min_length = schema.get("minLength")
    if min_length is not None and (not isinstance(min_length, int) or min_length < 0):
        raise ValueError("string schema 'minLength' must be a non-negative int")
    max_length = schema.get("maxLength")
    if max_length is not None and (not isinstance(max_length, int) or max_length < 0):
        raise ValueError("string schema 'maxLength' must be a non-negative int")


def _validate_integer_schema_shape(schema: Mapping[str, Any]) -> None:
    for key in schema.keys():
        if key not in _ALLOWED_INTEGER_KEYS:
            raise ValueError(f"Unsupported integer schema key: {key!r}")
    for bound_key in ("minimum", "maximum"):
        bound = schema.get(bound_key)
        if bound is not None and not isinstance(bound, int):
            raise ValueError(f"integer schema {bound_key!r} must be an int")


def _validate_boolean_schema_shape(schema: Mapping[str, Any]) -> None:
    for key in schema.keys():
        if key not in _ALLOWED_BOOLEAN_KEYS:
            raise ValueError(f"Unsupported boolean schema key: {key!r}")


def _validate_schema_shape(schema: Mapping[str, Any], *, top_level: bool) -> None:
    schema_type = schema.get("type")
    if top_level and schema_type not in _ALLOWED_TOP_LEVEL_TYPES:
        raise ValueError("ToolDefinition top-level schema must be an object")
    if schema_type not in _ALLOWED_TYPES:
        raise ValueError(
            f"Unsupported schema type: {schema_type!r}; allowed: {_ALLOWED_TYPES}"
        )
    if schema_type == "object":
        _validate_object_schema_shape(schema)
    elif schema_type == "array":
        _validate_array_schema_shape(schema)
    elif schema_type == "string":
        _validate_string_schema_shape(schema)
    elif schema_type == "integer":
        _validate_integer_schema_shape(schema)
    elif schema_type == "boolean":
        _validate_boolean_schema_shape(schema)


def _validate_value(
    *,
    tool_name: str,
    schema: Mapping[str, Any],
    value: Any,
    field_path: tuple[str, ...],
) -> Any:
    schema_type = schema.get("type")
    if schema_type == "object":
        return _validate_object(
            tool_name=tool_name,
            schema=schema,
            value=value,
            field_path=field_path,
        )
    if schema_type == "array":
        return _validate_array(
            tool_name=tool_name,
            schema=schema,
            value=value,
            field_path=field_path,
        )
    if schema_type == "string":
        return _validate_string(
            tool_name=tool_name,
            schema=schema,
            value=value,
            field_path=field_path,
        )
    if schema_type == "integer":
        return _validate_integer(
            tool_name=tool_name,
            schema=schema,
            value=value,
            field_path=field_path,
        )
    if schema_type == "boolean":
        return _validate_boolean(
            tool_name=tool_name,
            value=value,
            field_path=field_path,
        )
    raise ToolArgumentError(
        tool_name,
        f"unsupported schema type {schema_type!r}",
        field_path=field_path,
    )


def _validate_object(
    *,
    tool_name: str,
    schema: Mapping[str, Any],
    value: Any,
    field_path: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ToolArgumentError(tool_name, "expected object", field_path=field_path)
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    additional = schema.get("additionalProperties", False)
    keys = set(value.keys())
    declared = set(properties.keys())
    if not additional:
        extra = keys - declared
        if extra:
            extra_label = ", ".join(sorted(extra))
            raise ToolArgumentError(
                tool_name,
                f"unsupported argument(s): {extra_label}",
                field_path=field_path,
            )
    missing = [name for name in required if name not in keys]
    if missing:
        missing_label = ", ".join(missing)
        raise ToolArgumentError(
            tool_name,
            f"missing required argument(s): {missing_label}",
            field_path=field_path,
        )
    validated: dict[str, Any] = {}
    for name, sub_schema in properties.items():
        if name not in value:
            continue
        validated[name] = _validate_value(
            tool_name=tool_name,
            schema=sub_schema,
            value=value[name],
            field_path=field_path + (name,),
        )
    return validated


def _validate_array(
    *,
    tool_name: str,
    schema: Mapping[str, Any],
    value: Any,
    field_path: tuple[str, ...],
) -> list[Any]:
    if not isinstance(value, list):
        raise ToolArgumentError(tool_name, "expected array", field_path=field_path)
    item_schema = schema["items"]
    return [
        _validate_value(
            tool_name=tool_name,
            schema=item_schema,
            value=item,
            field_path=field_path + (f"[{index}]",),
        )
        for index, item in enumerate(value)
    ]


def _validate_string(
    *,
    tool_name: str,
    schema: Mapping[str, Any],
    value: Any,
    field_path: tuple[str, ...],
) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise ToolArgumentError(tool_name, "expected string", field_path=field_path)
    enum_values = schema.get("enum")
    if enum_values is not None and value not in enum_values:
        allowed = ", ".join(repr(item) for item in enum_values)
        raise ToolArgumentError(
            tool_name,
            f"value not in allowed set {{{allowed}}}",
            field_path=field_path,
        )
    min_length = schema.get("minLength")
    if min_length is not None and len(value) < min_length:
        raise ToolArgumentError(
            tool_name,
            f"string shorter than minLength {min_length}",
            field_path=field_path,
        )
    max_length = schema.get("maxLength")
    if max_length is not None and len(value) > max_length:
        raise ToolArgumentError(
            tool_name,
            f"string longer than maxLength {max_length}",
            field_path=field_path,
        )
    return value


def _validate_integer(
    *,
    tool_name: str,
    schema: Mapping[str, Any],
    value: object,
    field_path: tuple[str, ...],
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgumentError(tool_name, "expected integer", field_path=field_path)
    minimum = schema.get("minimum")
    if minimum is not None and value < minimum:
        raise ToolArgumentError(
            tool_name,
            f"integer below minimum {minimum}",
            field_path=field_path,
        )
    maximum = schema.get("maximum")
    if maximum is not None and value > maximum:
        raise ToolArgumentError(
            tool_name,
            f"integer above maximum {maximum}",
            field_path=field_path,
        )
    return value


def _validate_boolean(
    *,
    tool_name: str,
    value: Any,
    field_path: tuple[str, ...],
) -> bool:
    if not isinstance(value, bool):
        raise ToolArgumentError(tool_name, "expected boolean", field_path=field_path)
    return value
