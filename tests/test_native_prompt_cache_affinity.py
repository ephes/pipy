"""OpenAI prompt-cache affinity (PC1), mirroring Pi ``4df157433``.

Pi derives ``prompt_cache_key`` and the session-affinity headers from the
durable session id (``openai-codex-responses.ts:275-289,561,1661-1696``,
``openai-responses.ts:58-100,246-316``, ``azure-openai-responses.ts:308``) and
forces ``cacheRetention: "none"`` with a fresh routing id on private summary
calls (``compaction/compaction.ts:649-655``).
"""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_native_branch_summary_retry import (
    _PreparedProductProvider,
    _result,
    _settings,
)
from test_native_openai_codex_provider import (
    FakeSseHTTPClient,
    FakeWebSocketClient,
    auth_manager_with,
    completed_events,
    credentials,
    sse_payload,
)
from test_native_semantic_compaction import _result as _summary_result
from test_native_semantic_compaction_retry import _prepared_fixture

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.request import freeze_provider_request
from pipy_harness.native.catalog_data import BUILTIN_MODEL_ROWS
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexResponsesProvider,
    SseResponse,
)
from pipy_harness.native.provider_construction import (
    resolve_responses_prompt_cache,
)
from pipy_harness.native.providers.azure_openai_responses import (
    AzureOpenAIResponsesProvider,
)
from pipy_harness.native.providers.openai_prompt_cache import (
    OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH,
    clamp_openai_prompt_cache_key,
    resolve_cache_retention,
)
from pipy_harness.native.providers.openai_responses import OpenAIResponsesProvider
from pipy_harness.native.session_tree import NativeSessionTree

LONG_ID = "s" * 70 + "-tail"
CLAMPED = "s" * 64


def _request(
    tmp_path: Path,
    *,
    session_id: str | None = None,
    cache_retention: Any = None,
    provider_name: str = "openai-codex",
    header_callback: Any = None,
) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM",
        user_prompt="GOAL",
        provider_name=provider_name,
        model_id="gpt-test",
        cwd=tmp_path,
        session_id=session_id,
        cache_retention=cache_retention,
        provider_header_callback=header_callback,
    )


# ---- shared helper ---------------------------------------------------------


def test_clamp_counts_code_points_and_keeps_none() -> None:
    assert OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH == 64
    assert clamp_openai_prompt_cache_key(None) is None
    assert clamp_openai_prompt_cache_key("abc") == "abc"
    assert clamp_openai_prompt_cache_key("x" * 64) == "x" * 64
    assert clamp_openai_prompt_cache_key("x" * 65) == "x" * 64
    emoji = "\N{GRINNING FACE}" * 70
    assert clamp_openai_prompt_cache_key(emoji) == "\N{GRINNING FACE}" * 64


def test_resolve_cache_retention_explicit_wins_then_env_then_short() -> None:
    assert resolve_cache_retention(None, {}) == "short"
    assert resolve_cache_retention(None, {"PIPY_CACHE_RETENTION": "long"}) == "long"
    assert resolve_cache_retention(None, {"PIPY_CACHE_RETENTION": "none"}) == "short"
    assert resolve_cache_retention("none", {"PIPY_CACHE_RETENTION": "long"}) == "none"
    assert resolve_cache_retention("short", {"PIPY_CACHE_RETENTION": "long"}) == "short"


def test_request_validator_rejects_bad_cache_fields(tmp_path: Path) -> None:
    freeze_provider_request(_request(tmp_path, session_id="s", cache_retention="none"))
    with pytest.raises(TypeError):
        freeze_provider_request(_request(tmp_path, cache_retention="forever"))
    with pytest.raises(TypeError):
        freeze_provider_request(_request(tmp_path, session_id=7))  # type: ignore[arg-type]


# ---- openai-codex-responses ------------------------------------------------


def _codex(
    transport: str = "sse", ws_outcomes: list[Any] | None = None
) -> tuple[OpenAICodexResponsesProvider, FakeSseHTTPClient, FakeWebSocketClient]:
    sse = FakeSseHTTPClient(
        SseResponse(status_code=200, body=sse_payload(completed_events("ok")))
    )
    ws = FakeWebSocketClient(ws_outcomes or [completed_events("ok")])
    ids = iter(f"fresh-{index}" for index in range(10))
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        transport=transport,
        request_id_factory=lambda: next(ids),
    )
    return provider, sse, ws


def test_codex_sse_sends_session_key_and_headers(tmp_path: Path) -> None:
    provider, sse, _ws = _codex()

    result = provider.complete(_request(tmp_path, session_id="sess-1"))

    assert result.status == HarnessStatus.SUCCEEDED
    sent = sse.requests[0]
    assert sent["body"]["prompt_cache_key"] == "sess-1"
    assert sent["headers"]["session-id"] == "sess-1"
    assert sent["headers"]["x-client-request-id"] == "sess-1"


def test_codex_clamps_long_session_id_everywhere(tmp_path: Path) -> None:
    provider, sse, _ws = _codex()
    provider.complete(_request(tmp_path, session_id=LONG_ID))
    ws_provider, _sse, ws = _codex("websocket")
    ws_provider.complete(_request(tmp_path, session_id=LONG_ID))

    assert sse.requests[0]["body"]["prompt_cache_key"] == CLAMPED
    assert sse.requests[0]["headers"]["session-id"] == CLAMPED
    assert sse.requests[0]["headers"]["x-client-request-id"] == CLAMPED
    assert ws.requests[0]["body"]["prompt_cache_key"] == CLAMPED
    assert ws.requests[0]["headers"]["session-id"] == CLAMPED
    assert ws.requests[0]["headers"]["x-client-request-id"] == CLAMPED


@pytest.mark.parametrize(
    ("session_id", "retention"),
    [("sess-1", "none"), (None, None)],
    ids=["retention-none", "no-session"],
)
def test_codex_without_cache_session_omits_key_and_sse_headers(
    tmp_path: Path, session_id: str | None, retention: str | None
) -> None:
    provider, sse, _ws = _codex()
    provider.complete(
        _request(tmp_path, session_id=session_id, cache_retention=retention)
    )
    ws_provider, _sse, ws = _codex("websocket")
    ws_provider.complete(
        _request(tmp_path, session_id=session_id, cache_retention=retention)
    )

    assert "prompt_cache_key" not in sse.requests[0]["body"]
    assert "session-id" not in sse.requests[0]["headers"]
    assert "x-client-request-id" not in sse.requests[0]["headers"]
    assert "prompt_cache_key" not in ws.requests[0]["body"]
    assert ws.requests[0]["headers"]["session-id"] == "fresh-0"
    assert ws.requests[0]["headers"]["x-client-request-id"] == "fresh-0"


def test_codex_long_and_short_retention_keep_the_key(tmp_path: Path) -> None:
    for retention in ("short", "long"):
        provider, sse, _ws = _codex()
        provider.complete(
            _request(tmp_path, session_id="sess-1", cache_retention=retention)
        )
        assert sse.requests[0]["body"]["prompt_cache_key"] == "sess-1"


def test_codex_websocket_request_id_is_stable_within_one_call(tmp_path: Path) -> None:
    limit = [{"type": "error", "code": "websocket_connection_limit_reached"}]
    provider, _sse, ws = _codex("websocket", [limit, completed_events("ok")])

    result = provider.complete(_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert len(ws.requests) == 2
    assert {request["headers"]["session-id"] for request in ws.requests} == {"fresh-0"}


def test_codex_session_headers_override_extension_headers(tmp_path: Path) -> None:
    def hook(headers: Any) -> None:
        headers["session-id"] = "from-extension"
        headers["x-client-request-id"] = "from-extension"

    provider, sse, _ws = _codex()
    provider.complete(_request(tmp_path, session_id="sess-1", header_callback=hook))

    assert sse.requests[0]["headers"]["session-id"] == "sess-1"
    assert sse.requests[0]["headers"]["x-client-request-id"] == "sess-1"


# ---- openai-responses --------------------------------------------------------


class _JsonClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: Any,
        body: Any,
        timeout_seconds: float,
        cancel_token: object = None,
    ) -> JsonResponse:
        self.requests.append({"headers": dict(headers), "body": dict(body)})
        return JsonResponse(
            status_code=200,
            body={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
            },
        )


def _responses(**fields: Any) -> tuple[OpenAIResponsesProvider, _JsonClient]:
    client = _JsonClient()
    provider = OpenAIResponsesProvider(
        model_id="gpt-test", api_key="key", http_client=client, **fields
    )
    return provider, client


@pytest.fixture(autouse=True)
def _no_cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIPY_CACHE_RETENTION", raising=False)
    for name in (
        "AZURE_OPENAI_BASE_URL",
        "AZURE_OPENAI_RESOURCE_NAME",
        "AZURE_OPENAI_DEPLOYMENT_NAME_MAP",
    ):
        monkeypatch.delenv(name, raising=False)


def test_responses_default_sends_clamped_key_and_unclamped_headers(
    tmp_path: Path,
) -> None:
    provider, client = _responses()

    result = provider.complete(_request(tmp_path, session_id=LONG_ID))

    assert result.status == HarnessStatus.SUCCEEDED
    sent = client.requests[0]
    assert sent["body"]["prompt_cache_key"] == CLAMPED
    assert "prompt_cache_retention" not in sent["body"]
    assert "prompt_cache_options" not in sent["body"]
    assert sent["headers"]["session_id"] == LONG_ID
    assert sent["headers"]["x-client-request-id"] == LONG_ID
    assert "x-session-id" not in sent["headers"]


def test_responses_without_session_sends_no_cache_fields(tmp_path: Path) -> None:
    provider, client = _responses()
    provider.complete(_request(tmp_path))

    sent = client.requests[0]
    assert "prompt_cache_key" not in sent["body"]
    assert "session_id" not in sent["headers"]
    assert "x-client-request-id" not in sent["headers"]


def test_responses_affinity_formats(tmp_path: Path) -> None:
    provider, client = _responses(session_affinity_format="openrouter")
    provider.complete(_request(tmp_path, session_id="sess-1"))
    headers = client.requests[0]["headers"]
    assert headers["x-session-id"] == "sess-1"
    assert "session_id" not in headers and "x-client-request-id" not in headers

    provider, client = _responses(session_affinity_format="openai-nosession")
    provider.complete(_request(tmp_path, session_id="sess-1"))
    headers = client.requests[0]["headers"]
    assert headers["x-client-request-id"] == "sess-1"
    assert "session_id" not in headers and "x-session-id" not in headers


def test_responses_request_hook_can_override_affinity_headers(tmp_path: Path) -> None:
    def hook(headers: Any) -> None:
        headers["session_id"] = "from-extension"

    provider, client = _responses()
    provider.complete(_request(tmp_path, session_id="sess-1", header_callback=hook))

    assert client.requests[0]["headers"]["session_id"] == "from-extension"


@pytest.mark.parametrize("explicit_mode", [False, True])
def test_responses_retention_none_omits_key_and_headers(
    tmp_path: Path, explicit_mode: bool
) -> None:
    provider, client = _responses(supports_explicit_prompt_cache_mode=explicit_mode)
    provider.complete(_request(tmp_path, session_id="sess-1", cache_retention="none"))

    sent = client.requests[0]
    assert "prompt_cache_key" not in sent["body"]
    assert "prompt_cache_retention" not in sent["body"]
    assert "session_id" not in sent["headers"]
    assert "x-client-request-id" not in sent["headers"]
    if explicit_mode:
        assert sent["body"]["prompt_cache_options"] == {"mode": "explicit"}
    else:
        assert "prompt_cache_options" not in sent["body"]


def test_responses_long_retention_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CACHE_RETENTION", "long")

    provider, client = _responses()
    provider.complete(_request(tmp_path, session_id="sess-1"))
    body = client.requests[0]["body"]
    assert body["prompt_cache_key"] == "sess-1"
    assert body["prompt_cache_retention"] == "24h"
    assert "prompt_cache_options" not in body

    provider, client = _responses(supports_explicit_prompt_cache_mode=True)
    provider.complete(_request(tmp_path, session_id="sess-1"))
    body = client.requests[0]["body"]
    assert body["prompt_cache_options"] == {"ttl": "30m"}
    assert "prompt_cache_retention" not in body

    for explicit_mode in (False, True):
        provider, client = _responses(
            supports_long_cache_retention=False,
            supports_explicit_prompt_cache_mode=explicit_mode,
        )
        provider.complete(_request(tmp_path, session_id="sess-1"))
        body = client.requests[0]["body"]
        assert "prompt_cache_retention" not in body
        assert "prompt_cache_options" not in body
        assert body["prompt_cache_key"] == "sess-1"

    provider, client = _responses()
    provider.complete(_request(tmp_path, session_id="sess-1", cache_retention="short"))
    assert "prompt_cache_retention" not in client.requests[0]["body"]


def test_resolve_responses_prompt_cache_detection_and_explicit_compat() -> None:
    rows = {
        row.model_id: row for row in BUILTIN_MODEL_ROWS if row.provider_name == "openai"
    }
    base = rows["gpt-4o"]

    default = resolve_responses_prompt_cache(base, base.base_url)
    assert default.session_affinity_format == "openai"
    assert default.supports_long_cache_retention is True
    assert default.supports_explicit_prompt_cache_mode is False

    via_url = resolve_responses_prompt_cache(base, "https://openrouter.ai/api/v1")
    assert via_url.session_affinity_format == "openrouter"

    via_name = resolve_responses_prompt_cache(
        replace(base, provider_name="openrouter"), base.base_url
    )
    assert via_name.session_affinity_format == "openrouter"

    explicit = resolve_responses_prompt_cache(
        replace(
            base,
            compat={
                "sessionAffinityFormat": "openai-nosession",
                "supportsLongCacheRetention": False,
                "supportsExplicitPromptCacheMode": True,
            },
        ),
        "https://openrouter.ai/api/v1",
    )
    assert explicit.session_affinity_format == "openai-nosession"
    assert explicit.supports_long_cache_retention is False
    assert explicit.supports_explicit_prompt_cache_mode is True

    assert resolve_responses_prompt_cache(
        rows["gpt-6-sol"], rows["gpt-6-sol"].base_url
    ).supports_explicit_prompt_cache_mode


def test_catalog_marks_explicit_cache_mode_like_pi_generator() -> None:
    for row in BUILTIN_MODEL_ROWS:
        flag = bool(row.compat and row.compat.get("supportsExplicitPromptCacheMode"))
        expected = (
            row.provider_name == "openai"
            and row.api == "openai-responses"
            and row.cost.cache_write > 0
        )
        assert flag is expected, row.model_id
    marked = {
        row.model_id
        for row in BUILTIN_MODEL_ROWS
        if row.compat and row.compat.get("supportsExplicitPromptCacheMode")
    }
    assert marked == {
        "gpt-6.1-sol",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-6-astra",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    }


# ---- azure-openai-responses ----------------------------------------------------


def _azure() -> tuple[AzureOpenAIResponsesProvider, _JsonClient]:
    client = _JsonClient()
    provider = AzureOpenAIResponsesProvider(
        model_id="gpt-4o",
        endpoint_url="https://res.openai.azure.com",
        api_key="azure-key",
        http_client=client,  # type: ignore[arg-type]
    )
    return provider, client


@pytest.mark.parametrize("retention", [None, "none", "long"])
def test_azure_sends_clamped_key_regardless_of_retention(
    tmp_path: Path, retention: str | None
) -> None:
    provider, client = _azure()
    result = provider.complete(
        _request(
            tmp_path,
            session_id=LONG_ID,
            cache_retention=retention,
            provider_name="azure-openai",
        )
    )

    assert result.status == HarnessStatus.SUCCEEDED
    sent = client.requests[0]
    assert sent["body"]["prompt_cache_key"] == CLAMPED
    assert "session_id" not in sent["headers"]
    assert "x-client-request-id" not in sent["headers"]


def test_azure_without_session_sends_no_key(tmp_path: Path) -> None:
    provider, client = _azure()
    provider.complete(_request(tmp_path, provider_name="azure-openai"))

    assert "prompt_cache_key" not in client.requests[0]["body"]


# ---- callers -------------------------------------------------------------------


def test_main_turns_carry_session_id_and_branch_summary_a_private_one(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, max_retries=0)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    provider = _PreparedProductProvider(
        scripts=[
            [_result(HarnessStatus.SUCCEEDED, text="root answer")],
            [_result(HarnessStatus.SUCCEEDED, text="main answer")],
            [_result(HarnessStatus.SUCCEEDED, text="branch summary")],
        ]
    )
    session = CodingSession(
        provider=provider, settings_manager=settings, native_session=tree
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(
            "ROOT\nMAIN\n/tree select 1 summarize:unfinished\n/exit\n"
        ),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    root, main, summary = provider.prepared_requests
    assert root.session_id == main.session_id == tree.session_id
    assert root.cache_retention is None and main.cache_retention is None
    assert summary.cache_retention == "none"
    assert summary.session_id
    assert summary.session_id != tree.session_id


def test_compaction_request_is_private_with_fresh_routing_id(tmp_path: Path) -> None:
    effects, provider = _prepared_fixture(tmp_path, [_summary_result("SUMMARY")])

    outcome = effects.compact_context("manual")

    assert "compacted conversation" in outcome.notice
    (request,) = provider.requests
    assert request.cache_retention == "none"
    assert request.session_id
    assert request.session_id != effects.ctl.session_tree.session_id


def test_new_session_switches_the_cache_session_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_retries=0)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    provider = _PreparedProductProvider(
        scripts=[
            [_result(HarnessStatus.SUCCEEDED, text="first")],
            [_result(HarnessStatus.SUCCEEDED, text="second")],
        ]
    )
    session = CodingSession(
        provider=provider, settings_manager=settings, native_session=tree
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("FIRST\n/new\nSECOND\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    first, second = provider.prepared_requests
    assert first.session_id == tree.session_id
    assert second.session_id
    assert second.session_id != first.session_id
