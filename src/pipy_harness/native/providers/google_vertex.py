"""Google Vertex AI provider for the native pipy runtime.

This provider targets Gemini models served via Google Cloud's Vertex AI
``generateContent`` endpoint. The request/response body shape is the same as
the Google Generative AI surface (see ``providers/google_generative_ai.py``)
because both
front the same Gemini models. The two providers differ in:

- **Endpoint**: Vertex uses
  ``https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/``
  ``locations/{location}/publishers/google/models/{model_id}:generateContent``
  whereas the generative-language surface uses an API key in the URL.
- **Auth**: two modes, matching Pi's ``google-vertex.ts``. (1) **Vertex Express**
  (API key): when a ``GOOGLE_CLOUD_API_KEY`` (or a forwarded resolved key) is
  present and not a placeholder/sentinel, the request goes to the global
  ``https://aiplatform.googleapis.com/v1/publishers/google/models/{model}``
  ``:generateContent`` host (no project/location path) with the
  ``x-goog-api-key`` header. (2) **ADC** (OAuth bearer): otherwise Vertex requires
  an OAuth2 Bearer access token in the ``Authorization`` header against the
  regional endpoint. The production ADC path obtains the token by signing a
  Google service-account JWT (RS256) and exchanging it at
  ``https://oauth2.googleapis.com/token``. Implementing RS256 in pure stdlib
  requires hand-rolled ASN.1 parsing of PKCS#8 private keys, which the parity
  track's "no new runtime dependencies" invariant rules out for this slice.
  Instead the ADC path accepts a pre-obtained access token via the
  ``GOOGLE_ACCESS_TOKEN`` environment variable (produce one with
  ``gcloud auth print-access-token`` or any other external means). Native
  JWT/service-account auth is left as a future extension.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pipy_harness.models import HarnessStatus
from pipy_harness.native._provider_helpers import failed_provider_result, utc_now
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.http import (
    ApiErrorField,
    JsonHTTPClient,
    ProviderHTTPError,
    UrllibJsonHTTPClient,
)
from pipy_harness.native.http import (
    JsonResponse as JsonResponse,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import StreamChunkSink, apply_provider_headers
from pipy_harness.native.providers.google_generate_content_wire import (
    gemini_contents,
    parse_response,
    serialize_tool_for_gemini,
)

GOOGLE_VERTEX_ENDPOINT_TEMPLATE = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/"
    "locations/{location}/publishers/google/models/{model_id}:generateContent"
)
# Vertex Express (API-key) mode: the @google/genai SDK routes an API key to the
# global aiplatform host with no project/location path segment
# (index.cjs:12978-12982 baseUrl + shouldPrependVertexProjectPath:13104-13105).
GOOGLE_VERTEX_EXPRESS_ENDPOINT_TEMPLATE = (
    "https://aiplatform.googleapis.com/v1/publishers/google/models/"
    "{model_id}:generateContent"
)
# Stored-credential sentinel that signals "use ADC", not an express API key
# (Pi's GCP_VERTEX_CREDENTIALS_MARKER, google-vertex.ts:50).
GCP_VERTEX_CREDENTIALS_MARKER = "gcp-vertex-credentials"
# Placeholder pattern (e.g. "<authenticated>") rejected as an express key, per
# Pi's resolveApiKey (google-vertex.ts:405-407).
_PLACEHOLDER_API_KEY_RE = re.compile(r"^<[^>]+>$")
# Model-family regexes for the per-model thinking shape (see _build_thinking_config
# below). Defined next to _PLACEHOLDER_API_KEY_RE so all module-level compiled
# patterns (which rely on the module's ``import re``) live together.
_GEMINI3_PRO_RE = re.compile(r"gemini-3(?:\.\d+)?-pro")
_GEMINI3_FLASH_RE = re.compile(r"gemini-3(?:\.\d+)?-flash")
GOOGLE_VERTEX_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("promptTokenCount", "input_tokens"),
    ("candidatesTokenCount", "output_tokens"),
    ("totalTokenCount", "total_tokens"),
)

# Per-model thinking shape, ported from Pi's ``google-vertex.ts``. Vertex injects
# ``generationConfig.thinkingConfig`` per model family: a ``thinkingLevel`` enum
# for Gemini 3 Pro/Flash and a ``thinkingBudget`` token count otherwise. This is
# the ``THINKING_LEVEL_MAP`` variant of the generative-language surface and
# deliberately diverges from ``google_generative_ai`` in two places
# (google-vertex.ts):
#   * No ``2.5-flash-lite`` budget table — flash-lite falls into the ``2.5-flash``
#     branch (``getGoogleBudget``: minimal ``128``, not ``512``).
#   * No Gemma 4 special-casing — Gemma is not a Vertex Gemini model, so it never
#     uses the level path nor a dedicated disabled config.
# ``THINKING_LEVEL_MAP`` is identity at the wire level (the vendored ``@google/genai``
# ``ThinkingLevel`` enum serializes to ``"MINIMAL"``/``"LOW"``/``"MEDIUM"``/``"HIGH"``),
# so the bare uppercase strings are emitted directly. The ``_GEMINI3_*_RE`` patterns
# are defined above next to ``_PLACEHOLDER_API_KEY_RE``.


def _is_gemini3_pro(model_id: str) -> bool:
    return bool(_GEMINI3_PRO_RE.search(model_id.lower()))


def _is_gemini3_flash(model_id: str) -> bool:
    return bool(_GEMINI3_FLASH_RE.search(model_id.lower()))


def _uses_thinking_level(model_id: str) -> bool:
    """Families that take a ``thinkingLevel`` enum rather than a token budget.

    Only Gemini 3 Pro/Flash on Vertex (google-vertex.ts:312-320); no Gemma 4.
    """

    return _is_gemini3_pro(model_id) or _is_gemini3_flash(model_id)


def _google_thinking_level(effort: str, model_id: str) -> str:
    """Map a thinking effort to a ``thinkingLevel`` (Pi's getGemini3ThinkingLevel)."""

    if _is_gemini3_pro(model_id):
        return "LOW" if effort in ("minimal", "low") else "HIGH"
    # Gemini 3 Flash: pass the effort through as the enum.
    return {
        "minimal": "MINIMAL",
        "low": "LOW",
        "medium": "MEDIUM",
        "high": "HIGH",
    }.get(effort, "HIGH")


def _google_thinking_budget(model_id: str, effort: str) -> int:
    """Map a thinking effort to a token budget (Pi's getGoogleBudget).

    Returns ``-1`` (Gemini "dynamic"/auto budget) for models without a known
    budget table or for an unrecognized effort. Vertex has **no** separate
    ``2.5-flash-lite`` table, so flash-lite matches the ``2.5-flash`` branch.
    """

    lowered = model_id.lower()
    if "2.5-pro" in lowered:
        table = {"minimal": 128, "low": 2048, "medium": 8192, "high": 32768}
    elif "2.5-flash" in lowered:
        table = {"minimal": 128, "low": 2048, "medium": 8192, "high": 24576}
    else:
        return -1
    return table.get(effort, -1)


def _disabled_thinking_config(model_id: str) -> dict[str, Any]:
    """Per-model disabled thinking config (Pi's getDisabledThinkingConfig).

    Gemini 3 models cannot fully disable thinking, so Pi pins the lowest
    supported ``thinkingLevel`` without ``includeThoughts``. Gemini 2.x disables
    via ``thinkingBudget: 0``. No Gemma 4 branch (not a Vertex model).
    """

    if _is_gemini3_pro(model_id):
        return {"thinkingLevel": "LOW"}
    if _is_gemini3_flash(model_id):
        return {"thinkingLevel": "MINIMAL"}
    return {"thinkingBudget": 0}


def _build_thinking_config(
    model_id: str,
    reasoning_effort: str | None,
    thinking_disabled: bool,
) -> dict[str, Any] | None:
    """Resolve ``generationConfig.thinkingConfig`` or ``None`` to omit it.

    Mirrors Pi's ``buildParams`` thinking block (google-vertex.ts:458-468): when
    thinking is enabled emit ``includeThoughts: true`` plus a per-family
    ``thinkingLevel`` or ``thinkingBudget``; when a reasoning model runs with
    thinking off/unset emit the per-model disabled config; otherwise (a
    non-reasoning model, or a bare-default construction) omit. ``reasoning_effort``
    is only set for reasoning models with active thinking, so it takes precedence
    over ``thinking_disabled``.
    """

    if reasoning_effort:
        config: dict[str, Any] = {"includeThoughts": True}
        if _uses_thinking_level(model_id):
            config["thinkingLevel"] = _google_thinking_level(reasoning_effort, model_id)
        else:
            config["thinkingBudget"] = _google_thinking_budget(
                model_id, reasoning_effort
            )
        return config
    if thinking_disabled:
        return _disabled_thinking_config(model_id)
    return None


def google_vertex_http_client() -> UrllibJsonHTTPClient:
    """Build the shared JSON client wired with Google Vertex AI error types."""

    return UrllibJsonHTTPClient(
        provider_label="Google Vertex AI",
        status_error_class=GoogleVertexHTTPStatusError,
        transport_error_class=GoogleVertexTransportError,
        parse_error_class=GoogleVertexResponseParseError,
    )


@dataclass(frozen=True, slots=True)
class _VertexRequestConfiguration:
    url: str
    auth_header_name: Literal["x-goog-api-key", "Authorization"]
    auth_header_value: str = field(repr=False)
    auth_mode: Literal["api-key", "adc"]
    location: str


@dataclass(frozen=True, slots=True)
class _VertexPreflightFailure:
    error_type: str
    error_message: str


@dataclass(frozen=True, slots=True)
class GoogleVertexProvider:
    """Google Vertex AI ``generateContent`` provider behind ProviderPort.

    Targets Gemini models on Vertex AI. The request/response body shape is the
    same as the Google Generative AI surface, but the endpoint is built from
    ``project_id``, ``location``, and ``model_id``, and auth is an OAuth2
    Bearer access token (not an API key embedded in the URL).

    For PIPY this provider accepts a pre-obtained access token via the
    ``GOOGLE_ACCESS_TOKEN`` environment variable to keep within the parity
    track's "no new runtime dependencies" invariant; native service-account
    JWT signing is a future extension. The access token is never logged or
    archived; only sanitized metadata leaves the provider boundary.

    The adapter supports two auth modes, matching Pi's ``google-vertex.ts``:

    - **Express (API key)**: when an ``api_key`` resolves (runtime ``--api-key``,
      stored key, or ``GOOGLE_CLOUD_API_KEY``) and is not a placeholder/sentinel,
      the request goes to the global ``aiplatform.googleapis.com`` host with no
      project/location path segment and the ``x-goog-api-key`` header, mirroring
      the ``@google/genai`` Vertex Express path. No bearer token, project, or
      location is required.
    - **ADC (OAuth bearer)**: otherwise the request uses the regional endpoint
      built from ``project_id`` + ``location`` and the ``Authorization: Bearer``
      header carrying ``GOOGLE_ACCESS_TOKEN`` (a pre-obtained access token; native
      service-account JWT signing remains a future extension).

    The API key / access token is never logged or archived; only sanitized
    metadata leaves the provider boundary.
    """

    model_id: str
    project_id: str | None = field(
        default_factory=lambda: (
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GOOGLE_PROJECT_ID")
        )
    )
    location: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1"
    )
    access_token: str | None = field(
        default_factory=lambda: os.environ.get("GOOGLE_ACCESS_TOKEN"), repr=False
    )
    # Vertex Express API key. Defaults to ``GOOGLE_CLOUD_API_KEY`` for direct
    # construction; catalog construction forwards the resolved key (which may be
    # the ``<authenticated>`` sentinel — rejected by ``_resolve_express_api_key``
    # so it falls back to ADC, exactly as Pi's ``resolveApiKey`` filters it).
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("GOOGLE_CLOUD_API_KEY"), repr=False
    )
    http_client: JsonHTTPClient = field(default_factory=google_vertex_http_client)
    endpoint_template: str = GOOGLE_VERTEX_ENDPOINT_TEMPLATE
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True
    provider_name: str = "google-vertex"
    # Catalog-resolved request config. The OAuth2 access token and
    # project/location stay env-resolved (ADC-style, not an api key); catalog
    # construction injects ``extra_headers`` plus the mapped thinking effort.
    # ``reasoning_effort``/``thinking_disabled`` drive the per-model
    # ``generationConfig.thinkingConfig`` shape (level enum vs token budget), the
    # ``THINKING_LEVEL_MAP`` variant of ``google.ts`` (no flash-lite table, no
    # Gemma 4); defaults omit thinking. Applies in both Express and ADC modes.
    extra_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    reasoning_effort: str | None = None
    thinking_disabled: bool = False

    @property
    def name(self) -> str:
        return self.provider_name

    def _resolve_express_api_key(self) -> str | None:
        """Return a usable Vertex Express API key, or ``None`` for ADC.

        Mirrors Pi's ``resolveApiKey`` (google-vertex.ts:397-407): trim, and
        reject an empty string, the ``gcp-vertex-credentials`` marker, or a
        ``<placeholder>`` value (e.g. the ``<authenticated>`` ambient sentinel).
        """

        if self.api_key is None:
            return None
        trimmed = self.api_key.strip()
        if (
            not trimmed
            or trimmed == GCP_VERTEX_CREDENTIALS_MARKER
            or _PLACEHOLDER_API_KEY_RE.match(trimmed)
        ):
            return None
        return trimmed

    def _preflight_configuration(
        self,
    ) -> _VertexRequestConfiguration | _VertexPreflightFailure:
        """Resolve model and ordered Express/ADC configuration validation."""

        if not self.model_id or not self.model_id.strip():
            return _VertexPreflightFailure(
                error_type="GoogleVertexConfigurationError",
                error_message=(
                    "--native-model is required for native provider google-vertex."
                ),
            )
        quoted_model_id = urllib.parse.quote(self.model_id, safe="")
        express_key = self._resolve_express_api_key()
        if express_key is not None:
            # Express needs neither a project nor a location.
            return _VertexRequestConfiguration(
                url=GOOGLE_VERTEX_EXPRESS_ENDPOINT_TEMPLATE.format(
                    model_id=quoted_model_id,
                ),
                auth_header_name="x-goog-api-key",
                auth_header_value=express_key,
                auth_mode="api-key",
                location="",
            )

        project_id = self.project_id.strip() if self.project_id is not None else ""
        if not project_id:
            return _VertexPreflightFailure(
                error_type="GoogleVertexConfigurationError",
                error_message=(
                    "Google Cloud project id is required in the environment "
                    "for native provider google-vertex."
                ),
            )
        access_token = (
            self.access_token.strip() if self.access_token is not None else ""
        )
        if not access_token:
            return _VertexPreflightFailure(
                error_type="GoogleVertexAuthError",
                error_message=(
                    "Google Vertex AI bearer access value must be set in "
                    "the environment for native provider google-vertex."
                ),
            )
        location = self.location.strip() if self.location else ""
        if not location:
            return _VertexPreflightFailure(
                error_type="GoogleVertexConfigurationError",
                error_message=(
                    "Google Cloud location is required for native provider "
                    "google-vertex."
                ),
            )
        return _VertexRequestConfiguration(
            url=self.endpoint_template.format(
                location=urllib.parse.quote(location, safe=""),
                project_id=urllib.parse.quote(project_id, safe=""),
                model_id=quoted_model_id,
            ),
            auth_header_name="Authorization",
            auth_header_value=f"Bearer {access_token}",
            auth_mode="adc",
            location=location,
        )

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
        configuration = self._preflight_configuration()
        if isinstance(configuration, _VertexPreflightFailure):
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type=configuration.error_type,
                error_message=configuration.error_message,
            )

        body: dict[str, Any] = {
            "contents": gemini_contents(
                request,
                parse_error_class=GoogleVertexResponseParseError,
            ),
        }
        if request.system_prompt:
            body["systemInstruction"] = {
                "parts": [{"text": request.system_prompt}],
            }
        if request.available_tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        serialize_tool_for_gemini(tool)
                        for tool in request.available_tools
                    ],
                }
            ]
        # Per-model thinking shape (both Express and ADC modes share this body).
        thinking_config = _build_thinking_config(
            self.model_id, self.reasoning_effort, self.thinking_disabled
        )
        if thinking_config is not None:
            generation_config = body.setdefault("generationConfig", {})
            generation_config["thinkingConfig"] = thinking_config
        headers = {
            "Content-Type": "application/json",
            configuration.auth_header_name: configuration.auth_header_value,
        }
        # Merged models.json/model headers.
        for header_name, header_value in self.extra_headers.items():
            headers[header_name] = header_value
        headers = apply_provider_headers(request, headers)

        try:
            response = self.http_client.post_json(
                configuration.url,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
                cancel_token=cancel_token,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise GoogleVertexHTTPStatusError(
                    "Google Vertex AI request failed with HTTP status "
                    f"{response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = parse_response(
                response.body,
                parse_error_class=GoogleVertexResponseParseError,
                response_label="Google Vertex AI",
                usage_fields=GOOGLE_VERTEX_USAGE_FIELDS,
                tool_call_provider_prefix="google-vertex",
            )
        except GoogleVertexProviderError as exc:
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
                "provider_response_store_requested": False,
                "finish_reason": result.finish_reason,
                "vertex_auth_mode": configuration.auth_mode,
                # Express mode has no region; only ADC sends a location.
                **(
                    {"google_cloud_location": configuration.location}
                    if configuration.location
                    else {}
                ),
            },
            tool_calls=result.tool_calls,
        )


class GoogleVertexProviderError(ProviderHTTPError):
    """Base class for sanitized Google Vertex AI provider errors."""


class GoogleVertexHTTPStatusError(GoogleVertexProviderError):
    """Raised when Vertex AI returns a non-success HTTP status."""

    provider_label = "Google Vertex AI"
    api_error_fields = (
        ApiErrorField("status", "api_error_status", sanitize=True, allow_int=False),
        ApiErrorField("code", "api_error_code", sanitize=True, allow_int=True),
    )


class GoogleVertexTransportError(GoogleVertexProviderError):
    """Raised when the HTTP request cannot reach Vertex AI."""


class GoogleVertexResponseParseError(GoogleVertexProviderError):
    """Raised when the Vertex AI response shape is unsupported."""
