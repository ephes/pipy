"""Actual CLI profile/catalog owner and all native fake-wire family paths."""

import json
import os
import signal
from pathlib import Path

import pytest
from test_product_profiles import FAMILIES
from test_provider_checkpoint import Wire

from pipy_harness import cli, durable_cli
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.http import JsonResponse


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        location = tmp_path / name
        location.mkdir()
        monkeypatch.setenv(name, str(location))
    prior = {
        s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
    }
    for sig in prior:
        signal.signal(
            sig, signal.default_int_handler if sig == signal.SIGINT else signal.SIG_DFL
        )
    try:
        yield
    finally:
        for sig, handler in prior.items():
            signal.signal(sig, handler)


@pytest.mark.parametrize("api,provider,model,environment", FAMILIES)
def test_actual_cli_constructs_selected_catalog_profile_family_wire_and_private_output(
    tmp_path, monkeypatch, capsys, api, provider, model, environment
):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    config = Path(os.environ["PIPY_CONFIG_HOME"]) / "models.json"
    config.write_text(
        json.dumps(
            {
                "providers": {
                    provider: {
                        "api": api,
                        "baseUrl": "https://wire.invalid/private-target",
                        "models": [{"id": model, "name": "Test"}],
                    }
                }
            }
        )
    )
    if api not in ("amazon-bedrock", "google-vertex"):
        AuthStore().set(provider, {"type": "api_key", "key": "family-key"})

    class CompleteWire(Wire):
        def post_json(self, *args, **kwargs):
            response = super().post_json(*args, **kwargs)
            return JsonResponse(
                response.status_code, {**response.body, "status": "completed"}
            )

    wire = CompleteWire()
    original = durable_cli.DurableProviderProfile

    def selected(*args, **kwargs):
        assert kwargs["runtime_api_key"] is None
        assert kwargs["auth_path"] == Path(os.environ["PIPY_AUTH_DIR"]) / "auth.json"
        return original(*args, **kwargs, http_client=wire)

    monkeypatch.setattr(durable_cli, "DurableProviderProfile", selected)
    path = tmp_path / "family.sqlite"
    common = ["--store", str(path), "--workspace", str(tmp_path), "--format", "json"]
    code = cli.main(
        [
            "durable",
            "start",
            *common,
            "--provider",
            provider,
            "--model",
            model,
            "--no-tools",
            "--prompt",
            "input",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    output = json.loads(captured.out)
    assert output["inspection"]["outcome"] == "completed" and len(wire.calls) == 1
    assert output["completed"]["messages"][-1]["message"]["content"] == "done"
    for sentinel in ("family-key", "private-target", *environment.values()):
        assert sentinel not in captured.out

    def poison(*args, **kwargs):
        raise AssertionError("terminal profile reconstructed")

    monkeypatch.setattr(durable_cli, "build_profile", poison)
    assert cli.main(["durable", "resume", *common]) == 0
    again = json.loads(capsys.readouterr().out)
    assert (
        again["completed"]["messages"] == output["completed"]["messages"]
        and len(wire.calls) == 1
    )


def test_actual_cli_catalog_failure_unknown_model_and_no_profile_refuse_without_store(
    tmp_path, capsys
):
    config = Path(os.environ["PIPY_CONFIG_HOME"]) / "models.json"
    config.write_text("{invalid-private-configuration")
    path = tmp_path / "missing.sqlite"
    assert (
        cli.main(
            [
                "durable",
                "start",
                "--store",
                str(path),
                "--workspace",
                str(tmp_path),
                "--provider",
                "fake",
                "--model",
                "fake-native-bootstrap",
                "--prompt",
                "input",
            ]
        )
        == 1
    )
    assert capsys.readouterr().err == "Durable command unavailable: incompatible\n"
    assert not path.exists()


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_actual_cli_codex_auto_fallback_fragmented_utf8_crlf_terminal_without_eof(
    tmp_path, monkeypatch, capsys, terminal
):
    import io

    from test_native_openai_codex_provider import credentials

    from pipy_harness.native import openai_codex_provider as codex

    config = Path(os.environ["PIPY_CONFIG_HOME"]) / "models.json"
    config.write_text(
        json.dumps(
            {
                "providers": {
                    "openai-codex": {
                        "api": "openai-codex-responses",
                        "models": [{"id": "gpt-test", "name": "Test"}],
                    }
                }
            }
        )
    )
    auth = Path(os.environ["PIPY_AUTH_DIR"]) / "openai-codex.json"
    codex.FileOpenAICodexCredentialStore(auth).save(credentials())
    events = [
        {"type": "response.output_text.delta", "delta": "Unicode Ä\n"},
        {"type": "response." + terminal, "response": {"status": terminal}},
    ]
    payload = "".join(
        "data: " + json.dumps(e, ensure_ascii=False) + "\r\n\r\n" for e in events
    ).encode()
    responses = []
    requests = []
    websocket = []

    class Fragmented(io.RawIOBase):
        def __init__(self):
            self.offset = 0

        def readable(self):
            return True

        def readinto(self, target):
            assert self.offset < len(payload), "read EOF after actual terminal"
            target[0] = payload[self.offset]
            self.offset += 1
            return 1

    class Response(io.BufferedReader):
        def __init__(self):
            super().__init__(Fragmented())
            responses.append(self)

        def getcode(self):
            return 200

    def open_wire(request, **kwargs):
        requests.append(request)
        return Response()

    def no_websocket(*args, **kwargs):
        websocket.append(True)
        raise codex.OpenAICodexTransportError("Synthetic pre-event WebSocket failure")

    monkeypatch.setattr(codex, "open_url_cancellable", open_wire)
    monkeypatch.setattr(codex.WebsocketsSyncClient, "post_events", no_websocket)
    path = tmp_path / "codex.sqlite"
    common = ["--store", str(path), "--workspace", str(tmp_path), "--format", "json"]
    code = cli.main(
        [
            "durable",
            "start",
            *common,
            "--provider",
            "openai-codex",
            "--model",
            "gpt-test",
            "--no-tools",
            "--prompt",
            "input",
        ]
    )
    captured = capsys.readouterr()
    assert code == (0 if terminal == "completed" else 1), captured.err
    output = json.loads(captured.out)
    assert output["inspection"]["outcome"] == (
        "completed" if terminal == "completed" else "failed"
    )
    assert len(requests) == len(responses) == len(websocket) == 1
    assert responses[0].closed
    assert (
        next(v for k, v in requests[0].header_items() if k.lower() == "authorization")
        == "Bearer " + credentials().access_token
    )
    assert "Unicode Ä" in output["completed"]["messages"][-1]["message"]["content"]
    assert credentials().access_token not in captured.out + captured.err
    assert credentials().refresh_token not in captured.out + captured.err
    assert "openai-codex.json" not in captured.out + captured.err
    # Terminal receipt reuse needs no auth/catalog/current handler and performs
    # neither a new websocket attempt nor a second SSE request, also on failure.
    auth.unlink()
    monkeypatch.setattr(
        durable_cli,
        "build_profile",
        lambda *_: pytest.fail("terminal auth construction"),
    )
    assert cli.main(["durable", "resume", *common]) == code
    again = json.loads(capsys.readouterr().out)
    assert again["completed"]["messages"] == output["completed"]["messages"]
    assert len(requests) == len(websocket) == 1
