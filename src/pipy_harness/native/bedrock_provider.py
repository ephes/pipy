"""Amazon Bedrock provider for the native pipy runtime.

This provider targets Claude on Bedrock via the InvokeModel endpoint, which
takes an Anthropic Messages shaped body wrapped with the Bedrock-specific
`anthropic_version` field. Auth uses AWS Signature Version 4, implemented in
pure stdlib (`hmac`, `hashlib`, `urllib`) so the harness avoids a `boto3`
runtime dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native.anthropic_provider import (
    ANTHROPIC_DEFAULT_THINKING_BUDGET,
    ANTHROPIC_THINKING_BUDGETS,
)
from pipy_harness.native._provider_helpers import utc_now, failed_provider_result, JsonResponse, JsonHTTPClient, serialize_tool_for_anthropic, decode_json_object, urlopen_read_cancellable
from pipy_harness.models import HarnessStatus
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)
from pipy_harness.native.usage import NORMALIZED_PROVIDER_USAGE_KEYS, normalize_provider_usage

BEDROCK_ENDPOINT_TEMPLATE = "https://bedrock-runtime.{region}.amazonaws.com/model/{model_id}/invoke"
BEDROCK_ANTHROPIC_VERSION = "bedrock-2023-05-31"
BEDROCK_DEFAULT_MAX_TOKENS = 4096
BEDROCK_SIGV4_SERVICE = "bedrock"
BEDROCK_SIGV4_ALGORITHM = "AWS4-HMAC-SHA256"
# Headers SigV4 owns; a custom header must never collide with these when merged
# into the signed request (Pi filters the same set before signing).
_BEDROCK_RESERVED_HEADERS = frozenset({"authorization", "host"})
# Claude model families that take adaptive thinking (``output_config.effort``)
# on Bedrock rather than the ``budget_tokens`` path (Pi: supportsAdaptiveThinking).
_BEDROCK_ADAPTIVE_MODEL_MARKERS = ("opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6")
# Adaptive effort accepts low/medium/high/xhigh/max; minimal clamps to low
# (Pi: mapThinkingLevelToEffort).
_BEDROCK_ADAPTIVE_EFFORT = {"minimal": "low"}


def _supports_adaptive_thinking(model_id: str) -> bool:
    lowered = model_id.lower()
    return any(marker in lowered for marker in _BEDROCK_ADAPTIVE_MODEL_MARKERS)
BEDROCK_USAGE_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("input_tokens", "input_tokens"),
    ("output_tokens", "output_tokens"),
    ("cache_creation_input_tokens", "cache_write_tokens"),
    ("cache_read_input_tokens", "cached_tokens"),
)


@dataclass(frozen=True, slots=True)
class UrllibJsonHTTPClient:
    """Standard-library JSON client for Bedrock InvokeModel calls."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: CancelToken | None = None,
    ) -> JsonResponse:
        encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=dict(headers),
            method="POST",
        )
        try:
            status_code, payload = urlopen_read_cancellable(
                request,
                timeout_seconds=timeout_seconds,
                cancel_token=cancel_token,
            )
        except urllib.error.HTTPError as exc:
            raise BedrockHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = (
                sanitize_text(str(exc.reason))
                if getattr(exc, "reason", None)
                else "request failed"
            )
            raise BedrockTransportError(
                f"Bedrock API request failed: {reason}"
            ) from exc

        return JsonResponse(status_code=status_code, body=decode_json_object(payload, error_class=BedrockResponseParseError, provider_label="Bedrock API"))


@dataclass(frozen=True, slots=True)
class AmazonBedrockProvider:
    """Amazon Bedrock InvokeModel provider behind ProviderPort.

    Targets Claude on Bedrock, which speaks the Anthropic Messages
    request/response shape inside the Bedrock InvokeModel envelope. Auth is
    AWS Signature Version 4 with credentials sourced from environment by
    default.
    """

    model_id: str
    region: str = field(
        default_factory=lambda: os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    # ``repr=False`` on credential-bearing fields so a stray repr never leaks
    # the AWS signing keys.
    access_key: str | None = field(
        default_factory=lambda: os.environ.get("AWS_ACCESS_KEY_ID"), repr=False
    )
    secret_key: str | None = field(
        default_factory=lambda: os.environ.get("AWS_SECRET_ACCESS_KEY"), repr=False
    )
    session_token: str | None = field(
        default_factory=lambda: os.environ.get("AWS_SESSION_TOKEN"), repr=False
    )
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
    endpoint_template: str = BEDROCK_ENDPOINT_TEMPLATE
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True
    anthropic_version: str = BEDROCK_ANTHROPIC_VERSION
    max_tokens: int = BEDROCK_DEFAULT_MAX_TOKENS
    provider_name: str = "amazon-bedrock"
    # Catalog-resolved request config. AWS SigV4 credentials stay env-resolved
    # (they are not api keys); catalog construction injects only ``extra_headers``
    # (merged into the signed request) and ``reasoning_effort`` (Bedrock Claude
    # speaks the Anthropic body, so thinking is the ``thinking.budget_tokens``
    # shape, mapped via the shared anthropic budgets).
    extra_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    reasoning_effort: str | None = None
    _clock: Any = None

    @property
    def name(self) -> str:
        return self.provider_name

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del stream_sink, reasoning_sink
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        started_at = utc_now()
        if not self.model_id:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="BedrockConfigurationError",
                error_message=f"--native-model is required for native provider {self.name}.",
            )
        if not self.region:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="BedrockConfigurationError",
                error_message=f"AWS region is required for native provider {self.name}.",
            )
        if not self.access_key or not self.secret_key:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="BedrockAuthError",
                error_message=(
                    "AWS signing keys must be set in the environment for native "
                    f"provider {self.name}."
                ),
            )

        url = self.endpoint_template.format(
            region=self.region,
            model_id=urllib.parse.quote(self.model_id, safe=""),
        )
        body: dict[str, Any] = {
            "anthropic_version": self.anthropic_version,
            "max_tokens": self.max_tokens,
            "system": request.system_prompt,
            "messages": _messages_payload(request),
        }
        if request.available_tools:
            body["tools"] = [
                serialize_tool_for_anthropic(tool)
                for tool in request.available_tools
            ]
        # Bedrock Claude speaks the Anthropic body (InvokeModel carries the raw
        # Anthropic request), so thinking is placed at the body top level. Pi
        # uses adaptive thinking (``type: adaptive`` + ``output_config.effort``)
        # for the adaptive-capable Claude models (Opus 4.6/4.7/4.8, Sonnet 4.6)
        # and the ``budget_tokens`` path otherwise; we mirror that split.
        if self.reasoning_effort is not None:
            if _supports_adaptive_thinking(self.model_id):
                body["thinking"] = {"type": "adaptive"}
                body["output_config"] = {
                    "effort": _BEDROCK_ADAPTIVE_EFFORT.get(
                        self.reasoning_effort, self.reasoning_effort
                    )
                }
            else:
                body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": ANTHROPIC_THINKING_BUDGETS.get(
                        self.reasoning_effort, ANTHROPIC_DEFAULT_THINKING_BUDGET
                    ),
                }

        encoded_body = json.dumps(body).encode("utf-8")
        base_headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Merge models.json/model headers into the signed request (so they are
        # covered by the SigV4 signature). Reserved headers that SigV4 owns
        # (``authorization``, ``host``, ``x-amz-*``) are dropped so a custom
        # header can never collide with the signing headers (Pi filters the same
        # set before signing).
        for header_name, header_value in self.extra_headers.items():
            lowered = header_name.lower()
            if lowered in _BEDROCK_RESERVED_HEADERS or lowered.startswith("x-amz-"):
                continue
            base_headers[header_name] = header_value
        try:
            signed_headers = _sigv4_sign(
                method="POST",
                url=url,
                headers=base_headers,
                body=encoded_body,
                region=self.region,
                service=BEDROCK_SIGV4_SERVICE,
                access_key=self.access_key,
                secret_key=self.secret_key,
                session_token=self.session_token,
                now=self._utc_now_for_signing(),
            )
        except BedrockProviderError as exc:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type=type(exc).__name__,
                error_message=str(exc),
                metadata=exc.metadata,
            )

        headers: dict[str, str] = {**base_headers, **signed_headers}

        try:
            response = self.http_client.post_json(
                url,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
                cancel_token=cancel_token,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise BedrockHTTPStatusError(
                    f"Bedrock API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
        except BedrockProviderError as exc:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type=type(exc).__name__,
                error_message=str(exc),
                metadata=exc.metadata,
            )

        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=started_at,
            ended_at=utc_now(),
            final_text=result.final_text,
            usage=result.usage,
            metadata={
                "stop_reason": result.stop_reason,
                "aws_region": self.region,
            },
            tool_calls=result.tool_calls,
        )

    def _utc_now_for_signing(self) -> datetime:
        if self._clock is not None:
            value = self._clock()
            if not isinstance(value, datetime):
                raise BedrockProviderError("clock must return a datetime")
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return utc_now()


def _messages_payload(request: ProviderRequest) -> list[dict[str, object]]:
    if request.messages:
        return [_envelope_to_message(envelope) for envelope in request.messages]
    return [
        {
            "role": "user",
            "content": [{"type": "text", "text": request.user_prompt}],
        }
    ]


def _envelope_to_message(envelope: Any) -> dict[str, object]:
    """Translate one LoopMessage into one Anthropic-shape message dict."""

    if isinstance(envelope, UserMessage):
        return {
            "role": "user",
            "content": [{"type": "text", "text": envelope.content}],
        }
    if isinstance(envelope, AssistantMessage):
        content: list[dict[str, object]] = []
        if envelope.content:
            content.append({"type": "text", "text": envelope.content})
        for call in envelope.tool_calls:
            try:
                parsed_input: Any = (
                    json.loads(call.arguments_json) if call.arguments_json else {}
                )
            except json.JSONDecodeError:
                parsed_input = {}
            if not isinstance(parsed_input, Mapping):
                parsed_input = {}
            content.append(
                {
                    "type": "tool_use",
                    "id": call.provider_correlation_id,
                    "name": call.tool_name,
                    "input": dict(parsed_input),
                }
            )
        return {"role": "assistant", "content": content}
    if isinstance(envelope, ToolResultMessage):
        correlation = _require_provider_correlation_id(envelope)
        block: dict[str, object] = {
            "type": "tool_result",
            "tool_use_id": correlation,
            "content": envelope.output_text,
        }
        if envelope.is_error:
            block["is_error"] = True
        return {"role": "user", "content": [block]}
    raise BedrockResponseParseError(
        f"unsupported message envelope: {type(envelope).__name__}"
    )


def _require_provider_correlation_id(envelope: ToolResultMessage) -> str:
    if envelope.provider_correlation_id:
        return envelope.provider_correlation_id
    raise BedrockResponseParseError(
        "ToolResultMessage is missing provider_correlation_id."
    )


@dataclass(frozen=True, slots=True)
class ParsedBedrockResponse:
    final_text: str | None
    usage: dict[str, int | float]
    stop_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class BedrockProviderError(Exception):
    """Base class for sanitized Bedrock provider errors."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class BedrockHTTPStatusError(BedrockProviderError):
    """Raised when Bedrock returns a non-success HTTP status."""

    @classmethod
    def from_http_error(cls, exc: urllib.error.HTTPError) -> BedrockHTTPStatusError:
        metadata: dict[str, Any] = {"http_status": exc.code}
        try:
            body = decode_json_object(exc.read(), error_class=BedrockResponseParseError, provider_label="Bedrock API")
        except BedrockResponseParseError:
            body = {}
        message = body.get("message")
        if isinstance(message, str):
            metadata["api_error_type"] = sanitize_text(message)
        error_type = body.get("__type") or body.get("type")
        if isinstance(error_type, str):
            metadata.setdefault("api_error_type", sanitize_text(error_type))
        return cls(
            f"Bedrock API request failed with HTTP status {exc.code}.",
            metadata=metadata,
        )


class BedrockTransportError(BedrockProviderError):
    """Raised when the HTTP request cannot reach Bedrock."""


class BedrockResponseParseError(BedrockProviderError):
    """Raised when the Bedrock response shape is unsupported."""


class BedrockAuthError(BedrockProviderError):
    """Raised when AWS credentials are missing or invalid before signing."""


def _parse_response(body: Mapping[str, Any]) -> ParsedBedrockResponse:
    stop_reason_raw = body.get("stop_reason")
    stop_reason = (
        sanitize_text(stop_reason_raw)
        if isinstance(stop_reason_raw, str)
        else "unknown"
    )

    content = body.get("content")
    final_text = _extract_final_text(content)
    tool_calls = _extract_tool_calls(content)

    if not final_text and not tool_calls:
        raise BedrockResponseParseError(
            "Bedrock response did not include final output text or tool calls.",
            metadata={"stop_reason": stop_reason},
        )

    return ParsedBedrockResponse(
        final_text=final_text,
        usage=_extract_usage(body.get("usage")),
        stop_reason=stop_reason,
        tool_calls=tool_calls,
    )


def _extract_final_text(content: Any) -> str | None:
    if not isinstance(content, list):
        return None
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)
    if not chunks:
        return None
    return "".join(chunks)


def _extract_tool_calls(content: Any) -> tuple[ProviderToolCall, ...]:
    """Parse Anthropic-shape `content` items of type `tool_use`."""

    if not isinstance(content, list):
        return ()
    calls: list[ProviderToolCall] = []
    for index, item in enumerate(content):
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "tool_use":
            continue
        name = item.get("name")
        tool_input = item.get("input")
        call_id = item.get("id")
        if not isinstance(name, str) or not name:
            continue
        if isinstance(tool_input, Mapping):
            arguments_json = json.dumps(dict(tool_input), sort_keys=True)
        else:
            arguments_json = "{}"
        correlation: str
        if isinstance(call_id, str) and call_id:
            correlation = call_id
        else:
            correlation = f"bedrock-tool-{index}"
        try:
            calls.append(
                ProviderToolCall(
                    provider_correlation_id=correlation[
                        : ProviderToolCall.PROVIDER_CORRELATION_ID_MAX_LENGTH
                    ],
                    tool_name=name[: ProviderToolCall.TOOL_NAME_MAX_LENGTH],
                    arguments_json=arguments_json[
                        : ProviderToolCall.ARGUMENTS_JSON_MAX_LENGTH
                    ],
                )
            )
        except ValueError:
            continue
    return tuple(calls)


def _extract_usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    usage: dict[str, Any] = {}
    for key in NORMALIZED_PROVIDER_USAGE_KEYS:
        usage[key] = value.get(key)

    for provider_key, normalized_key in BEDROCK_USAGE_FIELD_MAP:
        if provider_key == normalized_key:
            continue
        item = value.get(provider_key)
        if item is not None and usage.get(normalized_key) is None:
            usage[normalized_key] = item

    if usage.get("total_tokens") is None:
        input_tokens = _usage_int(usage.get("input_tokens"))
        output_tokens = _usage_int(usage.get("output_tokens"))
        if input_tokens is not None and output_tokens is not None:
            usage["total_tokens"] = (
                input_tokens
                + output_tokens
                + (_usage_int(usage.get("cached_tokens")) or 0)
                + (_usage_int(usage.get("cache_write_tokens")) or 0)
            )

    return normalize_provider_usage(usage)


def _usage_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


# ---------------------------------------------------------------------------
# AWS Signature Version 4 — pure stdlib implementation.
# ---------------------------------------------------------------------------


def _sigv4_sign(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    region: str,
    service: str,
    access_key: str,
    secret_key: str,
    session_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Compute SigV4 headers for ``method`` ``url`` with ``body``.

    Returns a header dict containing ``Authorization``, ``X-Amz-Date``,
    ``Host``, ``X-Amz-Content-Sha256``, and optionally
    ``X-Amz-Security-Token``. The caller is expected to merge this dict on
    top of the base request headers.

    The algorithm follows the AWS docs' four-step procedure:

    1. Canonical request = METHOD + canonical URI + canonical query +
       canonical headers + signed headers + sha256_hex(body).
    2. String to sign = "AWS4-HMAC-SHA256" + amz_date + credential_scope +
       sha256_hex(canonical_request).
    3. Signing key = HMAC chain over (secret, date, region, service,
       "aws4_request").
    4. Signature = hex(HMAC-SHA256(signing_key, string_to_sign)).
    """

    if not access_key or not secret_key:
        raise BedrockAuthError("AWS signing keys are required for SigV4 signing.")
    if not region:
        raise BedrockAuthError("AWS region is required for SigV4 signing.")
    if not service:
        raise BedrockAuthError("AWS service name is required for SigV4 signing.")

    signing_time = now if now is not None else utc_now()
    if signing_time.tzinfo is None:
        signing_time = signing_time.replace(tzinfo=UTC)
    amz_date = signing_time.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = signing_time.strftime("%Y%m%d")

    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname:
        raise BedrockAuthError("SigV4 signing requires a URL with a host component.")
    host = parsed.hostname
    if parsed.port is not None and not (
        (parsed.scheme == "https" and parsed.port == 443)
        or (parsed.scheme == "http" and parsed.port == 80)
    ):
        host = f"{host}:{parsed.port}"

    canonical_uri = _canonical_uri(parsed.path)
    canonical_query = _canonical_query(parsed.query)
    payload_hash = hashlib.sha256(body).hexdigest()

    request_headers: dict[str, str] = {}
    for key, value in headers.items():
        request_headers[key.lower()] = _normalize_header_value(value)
    request_headers["host"] = host
    request_headers["x-amz-date"] = amz_date
    request_headers["x-amz-content-sha256"] = payload_hash
    if session_token:
        request_headers["x-amz-security-token"] = session_token

    sorted_header_keys = sorted(request_headers)
    canonical_headers = "".join(
        f"{key}:{request_headers[key]}\n" for key in sorted_header_keys
    )
    signed_headers = ";".join(sorted_header_keys)

    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri,
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            BEDROCK_SIGV4_ALGORITHM,
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    signing_key = _derive_signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"{BEDROCK_SIGV4_ALGORITHM} "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    result: dict[str, str] = {
        "Authorization": authorization,
        "X-Amz-Date": amz_date,
        "Host": host,
        "X-Amz-Content-Sha256": payload_hash,
    }
    if session_token:
        result["X-Amz-Security-Token"] = session_token
    return result


def _normalize_header_value(value: str) -> str:
    """Trim and collapse runs of whitespace per AWS SigV4 rules."""

    return " ".join(value.split()).strip()


def _canonical_uri(path: str) -> str:
    """Return the SigV4 canonical URI for ``path``.

    AWS spec: URI encode each segment using RFC 3986 unreserved characters
    only, then re-join with `/`. Bedrock's path includes a model id that may
    contain `:` and `.` characters; those must be percent-encoded in the
    canonical URI. We deliberately do *not* double-encode an already-encoded
    URL — the path arriving here is the raw path from `urlsplit`, which is
    expected to be unencoded.
    """

    if not path:
        return "/"
    segments = path.split("/")
    encoded_segments = [
        urllib.parse.quote(segment, safe="-._~") for segment in segments
    ]
    return "/".join(encoded_segments)


def _canonical_query(query: str) -> str:
    """Return the SigV4 canonical query string for ``query``."""

    if not query:
        return ""
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    encoded = [
        (
            urllib.parse.quote(name, safe="-._~"),
            urllib.parse.quote(value, safe="-._~"),
        )
        for name, value in pairs
    ]
    encoded.sort()
    return "&".join(f"{name}={value}" for name, value in encoded)


def _derive_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    """Derive the SigV4 signing key per the AWS spec.

    ``kSecret`` is `"AWS4" + secret_key`. Each subsequent step HMACs the
    previous key over the date, region, service, and the literal terminator
    `"aws4_request"`.
    """

    k_date = hmac.new(
        ("AWS4" + secret_key).encode("utf-8"),
        date_stamp.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    k_signing = hmac.new(
        k_service, b"aws4_request", hashlib.sha256
    ).digest()
    return k_signing
