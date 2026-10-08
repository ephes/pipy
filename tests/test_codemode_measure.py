"""Offline T10 instrumentation checks; never live provider evidence."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.agent import AgentAssistantMessage
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.codemode import selftest
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderToolCall

_spec = importlib.util.spec_from_file_location(
    "codemode_measure",
    Path(__file__).resolve().parents[1] / "scripts/codemode_measure.py",
)
assert _spec and _spec.loader
measure: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure)


def test_http_lengths_forwarding_and_privacy():
    calls = []
    response = object()

    class Client:
        def post_sse(self, url, **kwargs):
            calls.append((url, kwargs))
            return response

    wrapper = measure.CountingSseClient(Client())
    body = {"input": ["héllo"], "tools": [{"description": "schema"}]}
    headers = {"Authorization": "secret-sentinel"}
    cancel = object()
    for _ in range(2):
        assert (
            wrapper.post_sse(
                "url",
                headers=headers,
                body=body,
                timeout_seconds=17,
                cancel_token=cancel,
            )
            is response
        )
    assert calls[0][1]["headers"] is headers
    assert calls[0][1]["body"] is body
    assert calls[0][1]["cancel_token"] is cancel
    assert calls[0][1]["timeout_seconds"] == 17
    report = wrapper.report()
    assert (
        report["http_body_bytes"]["per_attempt"] == [len(json.dumps(body).encode())] * 2
    )
    assert report["http_body_bytes"]["total"] == len(json.dumps(body).encode()) * 2
    assert report["serialized_input_bytes"]["min"] == len(
        json.dumps(body["input"]).encode()
    )
    assert report["declared_schema_bytes"]["max"] == len(
        json.dumps(body["tools"]).encode()
    )
    assert "secret-sentinel" not in json.dumps(report)
    assert "héllo" not in json.dumps(report)
    assert set(vars(wrapper)) == {
        "delegate",
        "body_bytes",
        "schema_bytes",
        "input_bytes",
    }


def test_failed_attempt_counted_without_diagnostics():
    class Client:
        def post_sse(self, *args, **kwargs):
            raise RuntimeError("secret-sentinel")

    wrapper = measure.CountingSseClient(Client())
    with pytest.raises(RuntimeError):
        wrapper.post_sse("url", headers={}, body={}, timeout_seconds=None)
    assert wrapper.report()["http_body_bytes"]["count"] == 1
    assert "secret-sentinel" not in json.dumps(wrapper.report())


def test_preflight_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(measure, "version", lambda _: "49.0.0")
    monkeypatch.setattr(
        measure, "availability", lambda: selftest.Availability(False, "secret-sentinel")
    )
    monkeypatch.setattr(
        measure,
        "OpenAICodexResponsesProvider",
        lambda **_: pytest.fail("provider constructed"),
    )
    report = measure.execute(tmp_path)
    assert report["failure"] == "runtime_unavailable"
    assert not report["passed"] and not report["runs"]
    assert "secret-sentinel" not in json.dumps(report)


def test_exception_privacy(tmp_path, monkeypatch):
    monkeypatch.setattr(measure, "version", lambda _: "49.0.0")

    def broken():
        raise RuntimeError("secret-sentinel")

    monkeypatch.setattr(measure, "availability", broken)
    report = measure.execute(tmp_path)
    assert report["failure"] == "measurement_failed"
    assert "secret-sentinel" not in json.dumps(report)


def test_quality_requires_facts_and_actual_types():
    metrics = measure.Metrics()
    metrics.final = measure.EXPECTED
    metrics.outcome = "succeeded"
    metrics.compact_facts = True
    assert not metrics.report("codemode")["passed"]
    metrics.parents["codemode"] = 1
    metrics.children["read"] = 4
    assert metrics.report("codemode")["passed"]
    metrics.final = {"matches": [], "count": 0}
    assert not metrics.report("codemode")["passed"]
    metrics.message_completed(AgentAssistantMessage(ProductContent("secret-sentinel")))
    assert metrics.final is None
    assert not metrics.report("codemode")["exact_facts"]
    assert "secret-sentinel" not in json.dumps(metrics.report("codemode"))
    assert metrics.call_type("secret-sentinel") == "other"


@pytest.mark.parametrize("mode", ["direct", "codemode", "outside"])
def test_product_pipeline_metrics_and_fixture_confinement(tmp_path, monkeypatch, mode):
    from codemode_driver import real_runtime_skip_reason

    reason = real_runtime_skip_reason()
    if reason:
        pytest.skip(reason)
    from codemode_driver import REAL_RUNTIME

    from pipy_harness.native.codemode.runtime import CodemodePaths
    from pipy_harness.native.coding import codemode_runner

    paths = CodemodePaths(REAL_RUNTIME.parent.parent)
    original_probe = selftest.availability
    monkeypatch.setattr(selftest, "availability", lambda: original_probe(paths))
    original_init = codemode_runner.CodemodeCompositeRunner.__init__

    def init(self, **kwargs):
        original_init(self, paths=paths, **kwargs)

    monkeypatch.setattr(codemode_runner.CodemodeCompositeRunner, "__init__", init)
    workspace = tmp_path / "fixture"
    assert measure.prepare(workspace) > 80000
    for key in (
        "PIPY_CONFIG_HOME",
        "XDG_STATE_HOME",
        "PIPY_NATIVE_THEME_PATH",
        "PIPY_NATIVE_DEFAULTS_PATH",
        "PIPY_PROMPT_HISTORY_PATH",
        "PIPY_NATIVE_SESSIONS_ROOT",
    ):
        monkeypatch.setenv(key, str(tmp_path / key))
    if mode == "direct":
        calls = tuple(
            ProviderToolCall(str(i), "read", json.dumps({"path": name}))
            for i, name in enumerate(measure.FILES)
        )
    else:
        code = (
            "rows = []\nfor path in "
            + repr(measure.FILES)
            + ":\n    for line in tools.read(path=path).splitlines():\n        if line.startswith('KEEP '): rows.append({'file': path, 'value': line[5:]})\ntext({'matches': rows, 'count': len(rows)})"
        )
        if mode == "outside":
            code = "text(tools.read(path='../secret.txt'))"
        calls = (ProviderToolCall("parent", "codemode", json.dumps({"code": code})),)
    (tmp_path / "secret.txt").write_text("secret-sentinel")
    fake = FakeNativeProvider(
        supports_tool_calls=True,
        final_text=json.dumps(measure.EXPECTED),
        programmable_tool_calls=(calls, ()),
    )
    client = measure.CountingSseClient(None)
    report = measure.measure(
        workspace, "direct" if mode == "direct" else "codemode", fake, client
    )
    assert report["provider_turn_count"] == 2
    assert report["passed"] == (mode != "outside"), report
    assert report["http_body_bytes"]["count"] == 0  # Fake evidence cannot claim HTTP.
    if mode == "codemode":
        assert report["parent_calls"] == {"codemode": 1}
        assert report["nested_calls"] == {"read": 4}
        assert report["parent_result_bytes"][0] < 1000
        assert report["parent_duration_seconds"][0] > 0
    if mode == "outside":
        assert report["errors"] and report["nested_errors"]
        assert "secret-sentinel" not in "\n".join(
            p.read_text() for p in (workspace / "sessions").rglob("*.jsonl")
        )
    assert "secret-sentinel" not in json.dumps(report)


def test_missing_credentials_normal_provider_fails_closed(tmp_path, monkeypatch):
    from test_native_openai_codex_provider import auth_manager_with

    workspace = tmp_path / "fixture"
    measure.prepare(workspace)
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "defaults.json"))
    monkeypatch.setenv("PIPY_PROMPT_HISTORY_PATH", str(tmp_path / "history.json"))
    client = measure.CountingSseClient(None)
    provider = measure.OpenAICodexResponsesProvider(
        model_id="gpt-6.1-sol",
        reasoning_effort="low",
        transport="sse",
        auth_manager=auth_manager_with(None),
        http_client=client,
    )
    result = measure.measure(workspace, "direct", provider, client)
    assert not result["passed"]
    assert result["outcome"] == "failed"
    assert result["http_body_bytes"]["count"] == 0
    assert result["errors"] > 0


def test_execution_exception_retains_attempt_metrics(tmp_path, monkeypatch):
    workspace = tmp_path / "fixture"
    measure.prepare(workspace)
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "defaults.json"))
    monkeypatch.setenv("PIPY_PROMPT_HISTORY_PATH", str(tmp_path / "history.json"))

    class Broken(FakeNativeProvider):
        def complete(self, request, **kwargs):
            client.body_bytes.append(123)
            raise RuntimeError("secret-sentinel")

    client = measure.CountingSseClient(None)
    result = measure.measure(
        workspace, "direct", Broken(supports_tool_calls=True), client
    )
    assert not result["passed"]
    assert result["http_body_bytes"]["total"] == 123
    assert "secret-sentinel" not in json.dumps(result)


def test_normal_codex_serializer_measured_offline(tmp_path, monkeypatch):
    from test_native_openai_codex_provider import (
        auth_manager_with,
        credentials,
        sse_payload,
    )

    from pipy_harness.native.openai_codex_provider import SseResponse

    workspace = tmp_path / "fixture"
    measure.prepare(workspace)
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "defaults.json"))
    monkeypatch.setenv("PIPY_PROMPT_HISTORY_PATH", str(tmp_path / "history.json"))
    observed = []

    class Client:
        def post_sse(self, url, *, headers, body, **kwargs):
            # Synthetic unit-test auth only; retain no headers.
            observed.append(body)
            return SseResponse(
                status_code=200,
                body=sse_payload(
                    [
                        {
                            "type": "response.output_text.delta",
                            "delta": json.dumps(measure.EXPECTED),
                        },
                        {
                            "type": "response.completed",
                            "response": {
                                "status": "completed",
                                "usage": {
                                    "input_tokens": 19,
                                    "output_tokens": 7,
                                    "total_tokens": 26,
                                },
                            },
                        },
                    ]
                ),
            )

    client = measure.CountingSseClient(Client())
    provider = measure.OpenAICodexResponsesProvider(
        model_id="gpt-6.1-sol",
        reasoning_effort="low",
        transport="sse",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )
    report = measure.measure(workspace, "direct", provider, client)
    assert len(observed) == 1
    assert observed[0]["model"] == "gpt-6.1-sol"
    assert observed[0]["reasoning"]["effort"] == "low"
    assert [t["name"] for t in observed[0]["tools"]] == ["read"]
    assert report["http_body_bytes"]["total"] == len(json.dumps(observed[0]).encode())
    assert report["provider_usage"]["input_tokens"] == 19
    assert report["provider_usage"]["output_tokens"] == 7
    assert report["exact_facts"] and not report["passed"]  # No real reads executed.
    assert "Read every line" not in json.dumps(report)


def test_measure_uses_isolated_config_root(tmp_path, monkeypatch):
    workspace = tmp_path / "fixture"
    measure.prepare(workspace)
    config = tmp_path / "arm-state" / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "defaults.json"))
    monkeypatch.setenv("PIPY_PROMPT_HISTORY_PATH", str(tmp_path / "history.json"))
    seen = []
    real_session = measure.CodingSession

    def capture_session(**kwargs):
        seen.append(kwargs["settings_manager"].global_path)
        return real_session(**kwargs)

    monkeypatch.setattr(measure, "CodingSession", capture_session)
    fake = FakeNativeProvider(supports_tool_calls=True)
    measure.measure(workspace, "direct", fake, measure.CountingSseClient(None))
    assert seen == [config / "settings.json"]
    assert not (workspace / "config").exists()
