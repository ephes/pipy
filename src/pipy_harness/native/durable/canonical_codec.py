"""Private versioned canonical DTO trees; no runtime or task checkpoint wiring.

The flat node arena makes JSON payload depth independent of Python's stack.
Only exact builtins and an explicit closed DTO registry are ever traversed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from pipy_harness.native.agent.content import (
    ImageContent,
    NoStructuredContent,
    ProductContent,
    TextContent,
    ThinkingContent,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentMessageUsage,
    AgentStopReason,
    AgentSystemMessage,
    AgentToolCall,
    AgentToolDeclaration,
    AgentToolResultMessage,
    AgentUsageCost,
    AgentUserMessage,
)
from pipy_harness.native.agent.nested_record import NestedCallEvidence, NestedCallRecord
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    _ImmutableSchemaMapping,
    freeze_provider_request,
)
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.agent.tool_details import (
    LEGACY_TOOL_DETAILS_NONE,
    NO_TOOL_DETAILS,
    UNCHANGED_TOOL_DETAILS,
    ToolDetailsState,
)
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
)
from pipy_harness.native.client_identity import (
    _UNSET,
    ClientAttribution,
    ClientIdentity,
    CustomUserAgent,
)
from pipy_harness.native.durable.codec import JsonValueError, OwnedJson
from pipy_harness.native.image_attachment import ProviderImageAttachment
from pipy_harness.native.models import (
    ProviderPartial,
    ProviderRequest,
    ProviderResult,
    ProviderSystemMessage,
    ProviderToolCall,
)
from pipy_harness.native.tools.base import ToolDefinition
from pipy_harness.status import HarnessStatus


class CanonicalCodecError(ValueError):
    """Unsupported or malformed canonical data; diagnostics never quote payloads."""


# Schemas are deliberately explicit: adding a DTO field cannot silently change v1.
# A tuple spec is a union; [spec] describes an exact tuple of matching children.
# JSON and mapping specs permit only plain finite JSON, never embedded DTOs.
S = str
N = type(None)
MESSAGE = (AgentUserMessage, AgentAssistantMessage, AgentToolResultMessage)
BLOCK = (TextContent, ThinkingContent, AgentToolCall)
PBLOCK = (TextContent, ThinkingContent, ProviderToolCall)
SCHEMAS: dict[type[Any], dict[str, Any]] = {
    ProductContent: {"value": S},
    TextContent: {"text": S, "signature": (S, N)},
    ThinkingContent: {"thinking": S, "signature": (S, N), "redacted": bool},
    ImageContent: {"data": S, "mime_type": S},
    AgentToolCall: {
        "provider_correlation_id": S,
        "tool_name": S,
        "arguments_json": ProductContent,
        "thought_signature": (S, N),
    },
    AgentUserMessage: {"content": ProductContent},
    AgentUsageCost: dict.fromkeys(
        ("input", "output", "cache_read", "cache_write", "total"), (int, float)
    ),
    AgentMessageUsage: {
        **dict.fromkeys(
            ("input", "output", "cache_read", "cache_write", "total_tokens"), int
        ),
        "cost": AgentUsageCost,
        "reasoning": (int, N),
        "cache_write_1h": (int, N),
    },
    AgentAssistantMessage: {
        "content": ProductContent,
        "tool_calls": [AgentToolCall],
        "stop_reason": (AgentStopReason, N),
        "error_message": (S, N),
        "usage": (AgentMessageUsage, N),
        "provider": (S, N),
        "model": (S, N),
        "provider_thinking_level": (S, N),
        "blocks": [BLOCK],
        "api": (S, N),
    },
    AgentToolResultMessage: {
        "tool_request_id": S,
        "tool_name": S,
        "content": ProductContent,
        "provider_correlation_id": S,
        "is_error": bool,
        "details": "optional_mapping",
        "structured_content": "structured",
        "blocks": [(TextContent, ImageContent)],
        "terminate": bool,
    },
    AgentToolDeclaration: {"name": S, "description": S, "parameters_json": S},
    AgentSystemMessage: {
        "content": ProductContent,
        "sections": "sections",
        "tools_added": [AgentToolDeclaration],
        "tools_removed": [S],
    },
    ProviderToolCall: {
        "provider_correlation_id": S,
        "tool_name": S,
        "arguments_json": S,
        "thought_signature": (S, N),
    },
    ProviderResult: {
        "status": HarnessStatus,
        "provider_name": S,
        "model_id": S,
        "started_at": datetime,
        "ended_at": datetime,
        "final_text": (S, N),
        "usage": "optional_mapping",
        "metadata": "optional_mapping",
        "error_type": (S, N),
        "error_message": (S, N),
        "tool_calls": [ProviderToolCall],
        "provider_thinking_level": (S, N),
        "content_blocks": [PBLOCK],
        "api": (S, N),
    },
    ProviderPartial: {"content_blocks": [PBLOCK], "provider_thinking_level": (S, N)},
    ProviderTurnOutcome: {
        "result": (ProviderResult, N),
        "cancellation_reason": (AgentCancellationReason, N),
        "partial": (ProviderPartial, N),
        "retry_wait_cancelled": bool,
    },
    ToolExecutionOutcome: {
        "result": AgentToolResultMessage,
        "malformed_arguments": bool,
        "interruption": ToolExecutionInterruption,
        "cancelled": bool,
        "execution_seconds": (int, float, N),
    },
    ProviderSystemMessage: {"position": int, "message": AgentSystemMessage},
    ProviderImageAttachment: {
        "media_type": S,
        "data_base64": S,
        "byte_count": int,
        "sha256": S,
        "source_label": S,
    },
    ToolDefinition: {
        "name": S,
        "description": S,
        "input_schema": "schema",
        "schema_mode": S,
    },
    ProviderRequest: {
        "system_prompt": S,
        "user_prompt": S,
        "provider_name": S,
        "model_id": S,
        "cwd": type(Path()),
        "provider_turn_index": int,
        "provider_turn_label": S,
        "tool_observation": N,
        "messages": [MESSAGE],
        "available_tools": [ToolDefinition],
        "attachments": [ProviderImageAttachment],
        "provider_header_callback": N,
        "session_id": (S, N),
        "cache_retention": (S, N),
        "system_messages": [ProviderSystemMessage],
        "hidden_tool_names": [S],
        "undeclared_tool_names": [S],
    },
    AgentProviderRequestSnapshot: {
        "request": ProviderRequest,
        "advertised_tool_names": [S],
    },
}
SCHEMAS_V2 = {
    **SCHEMAS,
    AgentToolResultMessage: {
        **SCHEMAS[AgentToolResultMessage],
        "details": "tool_details",
        "nested_call_evidence": (NestedCallEvidence, N),
    },
    NestedCallRecord: {
        "id": S,
        "name": S,
        "status": S,
        "duration_ms": (int, float),
        "arguments_json": (S, N),
        "arguments_bytes": (int, N),
        "error": (S, N),
    },
    NestedCallEvidence: {
        "calls": [NestedCallRecord],
        "complete": bool,
        "argument_previews": "pairs",
    },
}


SCHEMAS_V3 = {
    **SCHEMAS_V2,
    ProviderRequest: {**SCHEMAS_V2[ProviderRequest], "identity": (ClientIdentity, N)},
    ClientIdentity: {
        "user_agent": (S, CustomUserAgent),
        "attribution": (ClientAttribution, N),
        "version": S,
    },
    ClientAttribution: {"name": S, "url": S, "categories": [S]},
    CustomUserAgent: {"custom": S},
}


class _NeedsV3(Exception):
    pass


class _NeedsV2(Exception):
    pass


def _schemas(codec_version: int) -> dict[type[Any], dict[str, Any]]:
    if codec_version == 1:
        return SCHEMAS
    if codec_version == 2:
        return SCHEMAS_V2
    if codec_version == 3:
        return SCHEMAS_V3
    _fail()


ENUMS: tuple[type[Any], ...] = (
    HarnessStatus,
    AgentStopReason,
    NoStructuredContent,
    ToolExecutionInterruption,
    AgentCancellationReason,
    ToolDetailsState,
)
TAGS: dict[str, type[Any]] = {cls.__name__: cls for cls in (*SCHEMAS_V3, *ENUMS)}


def _class(value: object) -> type[Any] | None:
    # Identity checks avoid foreign metaclass __hash__/__eq__ on ingress.
    return next((cls for cls in (*SCHEMAS_V3, *ENUMS) if type(value) is cls), None)


def _fail() -> NoReturn:
    raise CanonicalCodecError("Unsupported or malformed canonical value")


def _matches(value: object, spec: Any) -> bool:
    if type(spec) is tuple:
        return any(_matches(value, child) for child in spec)
    if type(spec) is list:
        return type(value) is tuple and all(_matches(child, spec[0]) for child in value)
    if type(spec) is not str:
        return type(value) is spec
    if spec in ("sections", "pairs"):
        return _matches_pairs(value, nullable=spec == "sections")
    if spec == "tool_details" and (
        value is NO_TOOL_DETAILS or value is LEGACY_TOOL_DETAILS_NONE
    ):
        return True
    if spec == "structured" and type(value) is NoStructuredContent:
        return True
    if spec == "optional_mapping" and value is None:
        return True
    if spec == "schema":
        value = _schema_lists(value)
    if spec in ("optional_mapping", "schema") and type(value) is not dict:
        return False
    return _matches_json(value)


def _matches_json(value: object) -> bool:
    try:
        OwnedJson(value)
    except JsonValueError:
        return False
    return True


def _matches_pairs(value: object, *, nullable: bool) -> bool:
    return type(value) is tuple and all(
        type(pair) is tuple
        and len(pair) == 2
        and type(pair[0]) is str
        and (type(pair[1]) is str or (nullable and pair[1] is None))
        for pair in value
    )


def _constructor_schema(
    cls: type[Any], values: dict[str, Any], codec_version: int
) -> dict[str, Any]:
    schema = _schemas(codec_version)[cls]
    if (
        cls is AgentToolResultMessage
        and codec_version >= 2
        and "nested_call_evidence" in values
        and values["nested_call_evidence"] is None
    ):
        _fail()
    if (
        cls is AgentToolResultMessage
        and codec_version >= 2
        and "nested_call_evidence" not in values
    ):
        schema = {
            key: spec for key, spec in schema.items() if key != "nested_call_evidence"
        }
    # Only these three V3 carriers have optional keys. Every other DTO stays
    # closed and exact; omission restores only its private singleton default.
    if cls is ClientIdentity:
        if not set(values).issubset(schema):
            _fail()
        schema = {key: schema[key] for key in values}
    elif cls is ClientAttribution:
        if not {"name", "url"}.issubset(values) or not set(values).issubset(schema):
            _fail()
        schema = {key: schema[key] for key in values}
    return schema


def _construct(cls: type[Any], values: dict[str, Any], codec_version: int) -> object:
    schema = _constructor_schema(cls, values, codec_version)
    if set(values) != set(schema) or not all(
        _matches(value, schema[key]) for key, value in values.items()
    ):
        _fail()
    try:
        if (
            codec_version == 1
            and cls is AgentToolResultMessage
            and values["details"] is None
        ):
            values = dict(values, details=LEGACY_TOOL_DETAILS_NONE)
        if cls is ToolDefinition:
            values = dict(values, input_schema=_schema_lists(values["input_schema"]))
        result = cls(**values)
        if cls is ProviderRequest:
            result = freeze_provider_request(result)
        if cls is ProviderImageAttachment and values["byte_count"] < 0:
            _fail()
        if cls is ProviderSystemMessage and values["position"] < 0:
            _fail()
    except (ValueError, TypeError, RecursionError):
        raise CanonicalCodecError("Invalid canonical DTO fields") from None
    return result


def _schema_lists(value: object) -> object:
    """Thaw the already-owned schema without calling foreign mappings."""
    # This boundary intentionally retains the existing DTO constructor's depth
    # limit; the codec's arbitrary product JSON payloads remain iterative.
    if type(value) is dict:
        return {key: _schema_lists(child) for key, child in value.items()}
    if type(value) is tuple:
        return [_schema_lists(child) for child in value]
    return value


def _dto_values(value: object, cls: type[Any], codec_version: int) -> dict[str, object]:
    if cls is ProviderRequest and value.identity is not None and codec_version < 3:  # type: ignore[attr-defined]
        raise _NeedsV3
    if cls is ProviderRequest and value.provider_header_callback is not None:  # type: ignore[attr-defined]
        raise CanonicalCodecError("Live provider header callbacks cannot be persisted")
    if cls is ToolDefinition:
        # Frozen request catalogs contain only model-visible declarations. Do not
        # silently discard registered metadata or live preparation/reach hooks.
        defaults: dict[str, object] = {
            "exposure": "direct",
            "namespace": None,
            "annotations": None,
            "output_schema": None,
            "reaches_tools": None,
            "default_active": True,
            "prepare_loadout": None,
            "prompt_snippet": None,
            "prompt_guidelines": (),
            "execution_mode": None,
        }
        for name, expected in defaults.items():
            child = getattr(value, name)
            if type(child) is not type(expected):
                raise CanonicalCodecError(
                    "Only model-visible tool declarations are supported"
                )
            if type(child) is tuple:
                same = len(child) == 0
            else:
                same = child == expected  # Only exact scalar types reach here.
            if not same:
                raise CanonicalCodecError(
                    "Only model-visible tool declarations are supported"
                )
    values = {name: getattr(value, name) for name in _schemas(codec_version)[cls]}
    if cls is ClientIdentity or cls is ClientAttribution:
        values = {name: child for name, child in values.items() if child is not _UNSET}
    return _versioned_values(value, cls, values, codec_version)


def _versioned_values(
    value: Any, cls: type[Any], values: dict[str, object], codec_version: int
) -> dict[str, object]:
    if cls is AgentToolResultMessage and codec_version == 1:
        if value.nested_call_evidence is not None:
            raise _NeedsV2
        details = values["details"]
        if details is LEGACY_TOOL_DETAILS_NONE:
            values["details"] = None
        elif type(details) is not dict:
            raise _NeedsV2
    if (
        cls is AgentToolResultMessage
        and codec_version >= 2
        and values["nested_call_evidence"] is None
    ):
        values.pop("nested_call_evidence")
    return values


def _leaf(value: object) -> dict[str, object] | None:
    if type(value) is str:
        return {"tag": "string", "value": value.encode("utf-8", "surrogatepass").hex()}
    if value is None or any(type(value) is cls for cls in (int, float, bool)):
        OwnedJson(value)
        return {"tag": "scalar", "value": value}
    if type(value) is type(Path()):
        return {
            "tag": "path",
            "value": str(value).encode("utf-8", "surrogatepass").hex(),
        }
    if type(value) is datetime:
        if value.tzinfo is not None and type(value.tzinfo) is not timezone:
            raise CanonicalCodecError(
                "Only naive or fixed-offset timestamps are supported"
            )
        timezone_name = value.tzname()
        return {
            "tag": "datetime",
            "value": value.isoformat(),
            "fold": value.fold,
            "timezone_name": (
                timezone_name.encode("utf-8", "surrogatepass").hex()
                if timezone_name is not None
                else None
            ),
        }
    cls = _class(value)
    if cls is not None and any(cls is enum for enum in ENUMS):
        return {"tag": cls.__name__, "value": value.value}  # type: ignore[attr-defined]
    return None


def _validate_key_identities(keys: list[str]) -> None:
    # These callers already require exact strings. A key-only JSON object has
    # no payload to own; retain its exact-key collapse and UTF-16 collision rule.
    identities: set[bytes] = set()
    for key in dict.fromkeys(keys):
        units = key.encode("utf-16-be", "surrogatepass")
        if units in identities:
            raise JsonValueError("JSON object keys need distinct UTF-16 identities")
        identities.add(units)


def _children(
    value: object, codec_version: int
) -> tuple[str, list[object], list[str] | None]:
    if type(value) is list or type(value) is tuple:
        return ("list" if type(value) is list else "tuple", list(value), None)
    if type(value) is dict or type(value) is _ImmutableSchemaMapping:
        pairs: Any
        if type(value) is _ImmutableSchemaMapping:
            pairs = value._items
            if type(pairs) is not tuple or any(
                type(pair) is not tuple or len(pair) != 2 for pair in pairs
            ):
                _fail()
        else:
            pairs = list(value.items())
        keys = [key for key, _ in pairs]
        if any(type(key) is not str for key in keys):
            _fail()
        _validate_key_identities(keys)
        return (
            "mapping",
            [child for _, child in pairs],
            [key.encode("utf-8", "surrogatepass").hex() for key in keys],
        )
    cls = _class(value)
    if cls is None:
        _fail()
    if cls not in _schemas(codec_version):
        if cls is ClientIdentity or cls is ClientAttribution or cls is CustomUserAgent:
            raise _NeedsV3
        raise _NeedsV2
    assert cls is not None
    values = _dto_values(value, cls, codec_version)
    return cls.__name__, list(values.values()), list(values)


def encode_canonical(
    value: object, *, codec_version: int | None = None
) -> dict[str, object]:
    """Choose the oldest lossless arena; saved v1 values retain their bytes."""
    if codec_version is not None and (
        type(codec_version) is not int or codec_version not in (1, 2, 3)
    ):
        _fail()
    selected = 1 if codec_version is None else codec_version
    while True:
        try:
            return _encode_canonical(value, selected)
        except (_NeedsV2, _NeedsV3) as needed:
            minimum = 3 if type(needed) is _NeedsV3 else 2
            if codec_version is not None or selected >= minimum:
                raise CanonicalCodecError(
                    f"Value requires canonical version {minimum}"
                ) from None
            selected = minimum


def _encode_canonical(value: object, codec_version: int) -> dict[str, object]:
    nodes: list[dict[str, object]] = []
    active: set[int] = set()
    work: list[tuple[object, list[int], bool]] = [(value, [], False)]
    try:
        while work:
            item, target, ending = work.pop()
            if ending:
                active.remove(id(item))
                continue
            target.append(len(nodes))
            if type(item) is ToolDetailsState:
                if codec_version == 1:
                    raise _NeedsV2
                if item is UNCHANGED_TOOL_DETAILS:
                    _fail()
            leaf = _leaf(item)
            if leaf is not None:
                nodes.append(leaf)
                continue
            if id(item) in active:
                _fail()
            active.add(id(item))
            tag, children, keys = _children(item, codec_version)
            refs: list[int] = []
            node: dict[str, object] = {"tag": tag, "children": refs}
            if keys is not None:
                node["keys"] = keys
            nodes.append(node)
            work.append((item, [], True))
            work.extend((child, refs, False) for child in reversed(children))
        tree: dict[str, object] = {"codec_version": codec_version, "nodes": nodes}
        decode_canonical(tree)  # Constructors enforce the normal DTO contracts.
        return tree
    except (JsonValueError, RecursionError):
        raise CanonicalCodecError("Unsupported finite canonical value") from None


def _shape(node: dict[str, Any], names: set[str]) -> None:
    if set(node) != names:
        _fail()


def _references(
    node: dict[str, Any], index: int, size: int, seen: set[int]
) -> list[int]:
    refs = node["children"]
    if type(refs) is not list:
        _fail()
    for ref in refs:
        if type(ref) is not int or not index < ref < size or ref in seen:
            _fail()
        seen.add(ref)
    return refs


def _decode_datetime(node: dict[str, Any]) -> datetime:
    _shape(node, {"tag", "value", "fold", "timezone_name"})
    if (
        type(node["value"]) is not str
        or type(node["fold"]) is not int
        or node["fold"] not in (0, 1)
    ):
        _fail()
    parsed = datetime.fromisoformat(node["value"]).replace(fold=node["fold"])
    if parsed.isoformat() != node["value"]:
        _fail()
    name = node["timezone_name"]
    if parsed.tzinfo is None:
        if name is not None:
            _fail()
        return parsed
    if type(name) is not str:
        _fail()
    offset = parsed.utcoffset()
    assert offset is not None
    decoded_name = bytes.fromhex(name).decode("utf-8", "surrogatepass")
    if decoded_name.encode("utf-8", "surrogatepass").hex() != name:
        _fail()
    return parsed.replace(tzinfo=timezone(offset, decoded_name))


def _canonical_path(text: str) -> Path:
    path = Path(text)
    if str(path) != text:
        _fail()
    return path


def _decode_leaf(node: dict[str, Any], tag: str, codec_version: int) -> object:
    if tag == "datetime":
        return _decode_datetime(node)
    _shape(node, {"tag", "value"})
    value = node["value"]
    if tag == "scalar":
        if value is not None and not any(
            type(value) is cls for cls in (bool, int, float)
        ):
            _fail()
        return value
    if tag in ("path", "string") and type(value) is str:
        text = bytes.fromhex(value).decode("utf-8", "surrogatepass")
        if text.encode("utf-8", "surrogatepass").hex() != value:
            _fail()
        return _canonical_path(text) if tag == "path" else text
    cls = TAGS.get(tag)
    if cls is None or not any(cls is enum for enum in ENUMS) or type(value) is not str:
        _fail()
    assert cls is not None
    if cls is ToolDetailsState and (codec_version == 1 or value == "unchanged"):
        _fail()
    return cls(value)


def _decode_node(
    node: dict[str, Any], children: list[object], tag: str, codec_version: int
) -> object:
    if tag in ("list", "tuple"):
        _shape(node, {"tag", "children"})
        return children if tag == "list" else tuple(children)
    _shape(node, {"tag", "children", "keys"})
    keys = node["keys"]
    if (
        type(keys) is not list
        or len(keys) != len(children)
        or any(type(key) is not str for key in keys)
    ):
        _fail()
    if len(set(keys)) != len(keys):
        _fail()
    values = dict(zip(keys, children, strict=True))
    _validate_key_identities(keys)
    if tag == "mapping":
        keys = [bytes.fromhex(key).decode("utf-8", "surrogatepass") for key in keys]
        _validate_key_identities(keys)
        if any(
            key.encode("utf-8", "surrogatepass").hex() != old
            for key, old in zip(keys, values, strict=True)
        ):
            _fail()
        return dict(zip(keys, children, strict=True))
    cls = TAGS.get(tag)
    if cls is None or cls not in _schemas(codec_version):
        _fail()
    assert cls is not None
    return _construct(cls, values, codec_version)


def decode_canonical(tree: object) -> object:
    """Validate a complete closed arena and reconstruct detached normal DTOs."""
    try:
        owned = OwnedJson(tree).thaw()
        if type(owned) is not dict or set(owned) != {"codec_version", "nodes"}:
            _fail()
        if type(owned["codec_version"]) is not int or owned["codec_version"] not in (
            1,
            2,
            3,
        ):
            _fail()
        codec_version = owned["codec_version"]
        nodes = owned["nodes"]
        if type(nodes) is not list or not nodes:
            _fail()
        built: list[object] = [None] * len(nodes)
        seen: set[int] = set()
        for index in reversed(range(len(nodes))):
            node = nodes[index]
            if type(node) is not dict or type(node.get("tag")) is not str:
                _fail()
            tag = node["tag"]
            if "children" in node:
                refs = _references(node, index, len(nodes), seen)
                built[index] = _decode_node(
                    node, [built[ref] for ref in refs], tag, codec_version
                )
            else:
                built[index] = _decode_leaf(node, tag, codec_version)
        if seen != set(range(1, len(nodes))):
            _fail()
        return built[0]
    except (JsonValueError, TypeError, ValueError, RecursionError):
        raise CanonicalCodecError("Unsupported or malformed canonical tree") from None
