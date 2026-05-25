"""Contracts for the native pipy model-driven tool loop.

This module names the small value objects and the `ToolPort` Protocol that
later slices of the Tool-Loop Parity Track depend on. It deliberately does not
wire any provider, REPL session, workspace effect, or archive boundary; it
only fixes the data shapes and validation surface.

Design boundaries:

- `ToolDefinition` carries model-visible tool name, description, and
  JSON-schema for arguments.
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
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

SUPPORTED_TOOL_REQUEST_ID_PREFIX = "pipy-tool-"

_ALLOWED_TOP_LEVEL_TYPES = ("object",)
_ALLOWED_TYPES = ("object", "string", "integer", "boolean", "array")
_ALLOWED_OBJECT_KEYS = frozenset(
    {"type", "properties", "required", "additionalProperties", "description"}
)
_ALLOWED_ARRAY_KEYS = frozenset({"type", "items", "description"})
_ALLOWED_STRING_KEYS = frozenset(
    {"type", "enum", "minLength", "maxLength", "description"}
)
_ALLOWED_INTEGER_KEYS = frozenset(
    {"type", "minimum", "maximum", "description"}
)
_ALLOWED_BOOLEAN_KEYS = frozenset({"type", "description"})


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
        path_label = (
            "." + ".".join(self.field_path) if self.field_path else ""
        )
        super().__init__(f"{tool_name}{path_label}: {message}")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Model-visible declaration of one tool.

    `input_schema` is a JSON-schema dict in the subset supported by
    `validate_arguments`. It is validated at construction time so later slices
    do not need to re-check definition shape.
    """

    name: str
    description: str
    input_schema: Mapping[str, Any]

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
            raise ValueError(
                "ToolDefinition name must be alphanumeric or underscore"
            )
        if not isinstance(self.description, str) or not self.description:
            raise ValueError("ToolDefinition requires a non-empty description")
        if len(self.description) > self.DESCRIPTION_MAX_LENGTH:
            raise ValueError(
                "ToolDefinition description exceeds "
                f"{self.DESCRIPTION_MAX_LENGTH} characters"
            )
        if not isinstance(self.input_schema, Mapping):
            raise ValueError("ToolDefinition.input_schema must be a mapping")
        _validate_schema_shape(self.input_schema, top_level=True)


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
        if not self.tool_request_id.startswith(SUPPORTED_TOOL_REQUEST_ID_PREFIX):
            raise ValueError(
                "ToolRequest tool_request_id must be pipy-owned "
                f"(prefix '{SUPPORTED_TOOL_REQUEST_ID_PREFIX}')"
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
                "ToolRequest.provider_correlation_id must be a non-empty string "
                "or None"
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

    OUTPUT_TEXT_MAX_LENGTH: ClassVar[int] = 64 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.tool_request_id, str) or not self.tool_request_id:
            raise ValueError(
                "ToolExecutionResult requires a non-empty tool_request_id"
            )
        if not self.tool_request_id.startswith(SUPPORTED_TOOL_REQUEST_ID_PREFIX):
            raise ValueError(
                "ToolExecutionResult tool_request_id must be pipy-owned "
                f"(prefix '{SUPPORTED_TOOL_REQUEST_ID_PREFIX}')"
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


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Environment passed to one tool invocation.

    `stderr_sink` is an optional callable that mutation tools (`write`,
    `edit`) use to stream unified diffs out to the loop's `error_stream`.
    The default is `None`, in which case mutation tools fall back to
    discarding the diff. The archive boundary is unrelated; diffs never
    cross it from inside the tool.
    """

    workspace_root: Path
    stderr_sink: Callable[[str], None] | None = field(default=None)

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_root, Path):
            raise ValueError("ToolContext.workspace_root must be a Path")
        if not self.workspace_root.is_absolute():
            raise ValueError("ToolContext.workspace_root must be absolute")
        if self.stderr_sink is not None and not callable(self.stderr_sink):
            raise ValueError("ToolContext.stderr_sink must be callable or None")


@runtime_checkable
class ToolPort(Protocol):
    """Minimal Protocol implemented by every model-driven tool.

    Tools must expose a stable `definition` (used by the loop to build the
    provider tool schema) and an `invoke(request, context)` method that
    returns a provider-visible `ToolExecutionResult`. Validation against the
    schema is performed by the loop (see `validate_arguments`) before
    `invoke()` runs, so implementations receive already-validated arguments.
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

    return f"{SUPPORTED_TOOL_REQUEST_ID_PREFIX}{uuid.uuid4()}"


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


def _validate_schema_shape(schema: Mapping[str, Any], *, top_level: bool) -> None:
    schema_type = schema.get("type")
    if top_level and schema_type not in _ALLOWED_TOP_LEVEL_TYPES:
        raise ValueError("ToolDefinition top-level schema must be an object")
    if schema_type not in _ALLOWED_TYPES:
        raise ValueError(
            f"Unsupported schema type: {schema_type!r}; "
            f"allowed: {_ALLOWED_TYPES}"
        )
    if schema_type == "object":
        for key in schema.keys():
            if key not in _ALLOWED_OBJECT_KEYS:
                raise ValueError(f"Unsupported object schema key: {key!r}")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ValueError("object schema 'properties' must be a mapping")
        for name, sub in properties.items():
            if not isinstance(name, str) or not name:
                raise ValueError("object property names must be non-empty strings")
            if not isinstance(sub, Mapping):
                raise ValueError(f"object property {name!r} schema must be a mapping")
            _validate_schema_shape(sub, top_level=False)
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
    elif schema_type == "array":
        for key in schema.keys():
            if key not in _ALLOWED_ARRAY_KEYS:
                raise ValueError(f"Unsupported array schema key: {key!r}")
        items = schema.get("items")
        if items is None:
            raise ValueError("array schema must declare 'items'")
        if not isinstance(items, Mapping):
            raise ValueError("array schema 'items' must be a mapping")
        _validate_schema_shape(items, top_level=False)
    elif schema_type == "string":
        for key in schema.keys():
            if key not in _ALLOWED_STRING_KEYS:
                raise ValueError(f"Unsupported string schema key: {key!r}")
        enum_values = schema.get("enum")
        if enum_values is not None:
            if not isinstance(enum_values, list) or not all(
                isinstance(item, str) for item in enum_values
            ):
                raise ValueError(
                    "string schema 'enum' must be a list of strings"
                )
        min_length = schema.get("minLength")
        if min_length is not None and (
            not isinstance(min_length, int) or min_length < 0
        ):
            raise ValueError("string schema 'minLength' must be a non-negative int")
        max_length = schema.get("maxLength")
        if max_length is not None and (
            not isinstance(max_length, int) or max_length < 0
        ):
            raise ValueError("string schema 'maxLength' must be a non-negative int")
    elif schema_type == "integer":
        for key in schema.keys():
            if key not in _ALLOWED_INTEGER_KEYS:
                raise ValueError(f"Unsupported integer schema key: {key!r}")
        for bound_key in ("minimum", "maximum"):
            bound = schema.get(bound_key)
            if bound is not None and not isinstance(bound, int):
                raise ValueError(
                    f"integer schema {bound_key!r} must be an int"
                )
    elif schema_type == "boolean":
        for key in schema.keys():
            if key not in _ALLOWED_BOOLEAN_KEYS:
                raise ValueError(f"Unsupported boolean schema key: {key!r}")


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
        raise ToolArgumentError(
            tool_name, "expected object", field_path=field_path
        )
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
        raise ToolArgumentError(
            tool_name, "expected array", field_path=field_path
        )
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
        raise ToolArgumentError(
            tool_name, "expected string", field_path=field_path
        )
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
    value: Any,
    field_path: tuple[str, ...],
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgumentError(
            tool_name, "expected integer", field_path=field_path
        )
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
        raise ToolArgumentError(
            tool_name, "expected boolean", field_path=field_path
        )
    return value
