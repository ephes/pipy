"""Canonical snapshots pairing one provider request with its authorization set."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
import sys

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import ToolDefinition


@dataclass(frozen=True, slots=True)
class _ImmutableSchemaMapping(Mapping[str, object]):
    """Tuple-backed mapping for one recursively immutable schema object."""

    _items: tuple[tuple[str, object], ...]

    def __getitem__(self, key: str) -> object:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)


@dataclass(frozen=True, slots=True)
class AgentProviderRequestSnapshot:
    """One exact provider request and the tool names it advertised."""

    request: ProviderRequest
    advertised_tool_names: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_provider_request_snapshot(self)

    def authorizes(self, tool_name: str) -> bool:
        """Return whether this exact request advertised ``tool_name``."""

        return tool_name in self.advertised_tool_names


def snapshot_provider_request(
    request: ProviderRequest,
    *,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    messages: tuple[AgentMessage, ...] | None = None,
    available_tool_names: Iterable[str] | None = None,
) -> AgentProviderRequestSnapshot:
    """Apply one monotonic request transform and freeze its authorization set."""

    if type(request) is not ProviderRequest:
        raise TypeError("request must be ProviderRequest (exact type required)")
    _validate_provider_request_values(request)
    if messages is not None:
        _validate_messages(messages)
    final_system_prompt = (
        system_prompt if system_prompt is not None else request.system_prompt
    )
    final_user_prompt = user_prompt if user_prompt is not None else request.user_prompt
    if final_user_prompt != request.user_prompt and messages is None:
        raise ValueError("messages are required when user_prompt changes")
    final_tools = _narrow_tool_definitions(
        request.available_tools,
        available_tool_names,
    )
    final_request = freeze_provider_request(
        replace(
            request,
            system_prompt=final_system_prompt,
            user_prompt=final_user_prompt,
            messages=request.messages if messages is None else messages,
            available_tools=final_tools,
        )
    )
    return AgentProviderRequestSnapshot(
        final_request,
        tuple(tool.name for tool in final_tools),
    )


def _narrow_tool_definitions(
    current: tuple[ToolDefinition, ...],
    requested_names: Iterable[str] | None,
) -> tuple[ToolDefinition, ...]:
    _validate_tool_definitions(current, require_frozen_schema=False)
    if requested_names is None:
        requested = {definition.name for definition in current}
    else:
        materialized_names = tuple(requested_names)
        if any(type(name) is not str for name in materialized_names):
            raise TypeError("available_tool_names must contain exact strings")
        requested = {name for name in materialized_names if name}
    seen: set[str] = set()
    narrowed: list[ToolDefinition] = []
    for definition in current:
        if definition.name not in requested or definition.name in seen:
            continue
        seen.add(definition.name)
        narrowed.append(definition)
    return tuple(narrowed)


def freeze_provider_request(request: ProviderRequest) -> ProviderRequest:
    """Return one detached request with a recursively immutable tool catalog."""

    if type(request) is not ProviderRequest:
        raise TypeError("request must be ProviderRequest (exact type required)")
    _validate_provider_request_values(request)
    frozen_tools = tuple(
        _freeze_tool_definition(definition) for definition in request.available_tools
    )
    frozen_request = replace(request, available_tools=frozen_tools)
    validate_frozen_provider_request(frozen_request)
    return frozen_request


def validate_frozen_provider_request(request: ProviderRequest) -> None:
    """Validate one exact provider request and its fully immutable data graph."""

    if type(request) is not ProviderRequest:
        raise TypeError("request must be ProviderRequest (exact type required)")
    _validate_provider_request_values(request)
    _validate_tool_definitions(request.available_tools, require_frozen_schema=True)


def validate_provider_request_snapshot(snapshot: AgentProviderRequestSnapshot) -> None:
    """Validate one exact immutable request/authorization snapshot recursively."""

    if type(snapshot) is not AgentProviderRequestSnapshot:
        raise TypeError("snapshot must be an exact AgentProviderRequestSnapshot")
    validate_frozen_provider_request(snapshot.request)
    names = snapshot.advertised_tool_names
    if type(names) is not tuple or any(
        type(name) is not str or not name for name in names
    ):
        raise TypeError("advertised_tool_names must be an exact tuple of names")
    if len(set(names)) != len(names):
        raise ValueError("advertised_tool_names must not contain duplicates")
    request_names = tuple(tool.name for tool in snapshot.request.available_tools)
    if request_names != names:
        raise ValueError("advertised tool names must match the provider request")


def _freeze_tool_definition(definition: ToolDefinition) -> ToolDefinition:
    _validate_tool_definition(definition, require_frozen_schema=False)
    frozen_schema = _freeze_schema_mapping(definition.input_schema)
    _validate_schema_semantics(frozen_schema)
    validated = ToolDefinition(
        definition.name,
        definition.description,
        _thaw_schema_mapping(frozen_schema),
    )
    object.__setattr__(validated, "input_schema", frozen_schema)
    return validated


def _freeze_schema_mapping(value: object) -> _ImmutableSchemaMapping:
    if not isinstance(value, Mapping):
        raise TypeError("ToolDefinition.input_schema must be a mapping")
    frozen: list[tuple[str, object]] = []
    for key, child in value.items():
        if type(key) is not str:
            raise TypeError("tool schema keys must be exact strings")
        frozen.append((key, _freeze_schema_value(child)))
    return _ImmutableSchemaMapping(tuple(frozen))


def _freeze_schema_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_schema_mapping(value)
    if isinstance(value, (list, tuple)) and type(value) in {list, tuple}:
        return tuple(_freeze_schema_value(child) for child in value)
    if value is None or type(value) in {bool, int, str}:
        return value
    raise TypeError("tool schema values must be immutable JSON values")


def _thaw_schema_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {key: _thaw_schema_value(child) for key, child in value.items()}


def _thaw_schema_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _thaw_schema_mapping(value)
    if type(value) is tuple:
        return [_thaw_schema_value(child) for child in value]
    return value


def _validate_schema_semantics(schema: Mapping[str, object]) -> None:
    if type(schema.get("type")) is not str:
        raise TypeError("tool schema type must be an exact string")
    description = schema.get("description")
    if description is not None and type(description) is not str:
        raise TypeError("tool schema description must be an exact string")
    for key in ("minLength", "maxLength", "minimum", "maximum"):
        value = schema.get(key)
        if value is not None and type(value) is not int:
            raise TypeError(f"tool schema {key} must be an exact integer")
    additional = schema.get("additionalProperties")
    if additional is not None and type(additional) is not bool:
        raise TypeError("tool schema additionalProperties must be an exact bool")
    for key in ("required", "enum"):
        values = schema.get(key)
        if values is not None and (
            type(values) is not tuple or any(type(value) is not str for value in values)
        ):
            raise TypeError(f"tool schema {key} must contain exact strings")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise TypeError("tool schema properties must be a mapping")
        for child in properties.values():
            if not isinstance(child, Mapping):
                raise TypeError("tool property schemas must be mappings")
            _validate_schema_semantics(child)
    items = schema.get("items")
    if items is not None:
        if not isinstance(items, Mapping):
            raise TypeError("tool schema items must be a mapping")
        _validate_schema_semantics(items)


def _validate_provider_request_values(request: ProviderRequest) -> None:
    for field_name in (
        "system_prompt",
        "user_prompt",
        "provider_name",
        "model_id",
        "provider_turn_label",
    ):
        if type(getattr(request, field_name)) is not str:
            raise TypeError(f"ProviderRequest.{field_name} must be an exact string")
    if type(request.cwd) is not type(Path()):
        raise TypeError("ProviderRequest.cwd must be an exact platform Path")
    if type(request.provider_turn_index) is not int or request.provider_turn_index < 0:
        raise TypeError("ProviderRequest.provider_turn_index must be nonnegative int")
    if request.tool_observation is not None:
        raise TypeError("ProviderRequest.tool_observation is not an agent-loop input")
    if request.no_tool_repl_context is not None:
        raise TypeError(
            "ProviderRequest.no_tool_repl_context is not an agent-loop input"
        )
    _validate_messages(request.messages)
    if type(request.available_tools) is not tuple:
        raise TypeError("ProviderRequest.available_tools must be an exact tuple")
    _validate_attachments(request.attachments)
    if request.provider_header_callback is not None and not callable(
        request.provider_header_callback
    ):
        raise TypeError("ProviderRequest.provider_header_callback must be callable")


def _validate_tool_definitions(
    definitions: tuple[ToolDefinition, ...], *, require_frozen_schema: bool
) -> None:
    for definition in definitions:
        _validate_tool_definition(
            definition, require_frozen_schema=require_frozen_schema
        )


def _validate_tool_definition(
    definition: ToolDefinition, *, require_frozen_schema: bool
) -> None:
    if type(definition) is not ToolDefinition:
        raise TypeError("available_tools must contain exact ToolDefinition values")
    if type(definition.name) is not str or not definition.name:
        raise TypeError("ToolDefinition.name must be a non-empty exact string")
    if type(definition.description) is not str or not definition.description:
        raise TypeError("ToolDefinition.description must be a non-empty exact string")
    if require_frozen_schema:
        _validate_frozen_schema_value(definition.input_schema)
    elif not isinstance(definition.input_schema, Mapping):
        raise TypeError("ToolDefinition.input_schema must be a mapping")


def _validate_frozen_schema_value(value: object) -> None:
    if type(value) is _ImmutableSchemaMapping:
        for child in value.values():
            _validate_frozen_schema_value(child)
        return
    if type(value) is tuple:
        for child in value:
            _validate_frozen_schema_value(child)
        return
    if value is None or type(value) in {bool, int, str}:
        return
    raise TypeError("tool schema must be recursively immutable")


def _validate_messages(messages: object) -> None:
    if type(messages) is not tuple:
        raise TypeError("ProviderRequest.messages must be an exact tuple")
    for message in messages:
        _validate_message(message)


def _validate_message(message: object) -> None:
    if type(message) is AgentUserMessage:
        validate_product_content(message.content, "AgentUserMessage.content")
        return
    if type(message) is AgentAssistantMessage:
        validate_product_content(message.content, "AgentAssistantMessage.content")
        if type(message.tool_calls) is not tuple:
            raise TypeError("AgentAssistantMessage.tool_calls must be an exact tuple")
        for call in message.tool_calls:
            validate_agent_tool_call(call)
        return
    if type(message) is AgentToolResultMessage:
        validate_agent_tool_result_message(message)
        return
    raise TypeError("ProviderRequest.messages contains a non-canonical message")


def _validate_attachments(attachments: object) -> None:
    if type(attachments) is not tuple:
        raise TypeError("ProviderRequest.attachments must be an exact tuple")
    expected = _loaded_exact_type(
        "pipy_harness.native.image_attachment", "ProviderImageAttachment"
    )
    for attachment in attachments:
        if expected is None or type(attachment) is not expected:
            raise TypeError(
                "attachments must contain exact ProviderImageAttachment values"
            )
        for field_name in ("media_type", "data_base64", "sha256", "source_label"):
            if type(getattr(attachment, field_name, None)) is not str:
                raise TypeError(f"ProviderImageAttachment.{field_name} must be string")
        byte_count = getattr(attachment, "byte_count", None)
        if type(byte_count) is not int or byte_count < 0:
            raise TypeError(
                "ProviderImageAttachment.byte_count must be nonnegative int"
            )


def _loaded_exact_type(module_name: str, type_name: str) -> type[object] | None:
    module = sys.modules.get(module_name)
    candidate = getattr(module, type_name, None) if module is not None else None
    return candidate if isinstance(candidate, type) else None


def validate_agent_tool_call(call: object) -> None:
    """Validate one exact tool call embedded in a provider request."""

    if type(call) is not AgentToolCall:
        raise TypeError("call must be an exact AgentToolCall")
    if (
        type(call.provider_correlation_id) is not str
        or not call.provider_correlation_id
    ):
        raise TypeError("AgentToolCall.provider_correlation_id must be string")
    if type(call.tool_name) is not str or not call.tool_name:
        raise TypeError("AgentToolCall.tool_name must be string")
    validate_product_content(call.arguments_json, "AgentToolCall.arguments_json")


def validate_agent_tool_result_message(result: object) -> None:
    """Validate one exact tool-result message embedded in a provider request."""

    if type(result) is not AgentToolResultMessage:
        raise TypeError("result must be an exact AgentToolResultMessage")
    for field_name in ("tool_request_id", "tool_name", "provider_correlation_id"):
        if type(getattr(result, field_name)) is not str or not getattr(
            result, field_name
        ):
            raise TypeError(f"AgentToolResultMessage.{field_name} must be string")
    validate_product_content(result.content, "AgentToolResultMessage.content")
    if type(result.is_error) is not bool:
        raise TypeError("AgentToolResultMessage.is_error must be an exact bool")
    if type(result.added_tool_names) is not tuple or any(
        type(name) is not str or not name for name in result.added_tool_names
    ):
        raise TypeError("AgentToolResultMessage.added_tool_names must be exact names")


def validate_product_content(content: object, field_name: str) -> None:
    """Validate one exact full-content canonical payload."""

    if type(content) is not ProductContent:
        raise TypeError(f"{field_name} must be exact ProductContent")
    if type(content.value) is not str:
        raise TypeError(f"{field_name}.value must be an exact string")
