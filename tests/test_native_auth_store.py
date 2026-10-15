"""Tests for the pipy auth store + per-request auth resolution (M6)."""

from __future__ import annotations

import json
from typing import cast

from pipy_harness.native.auth_store import (
    AuthStore,
    ProviderAuthRequestConfig,
    env_api_key,
    find_env_keys,
    provider_auth_status,
    provider_available,
    resolve_config_value,
    resolve_request_auth,
)

# ---- resolve_config_value (literal / env-name / !command) ------------------


def test_resolve_config_value_literal():
    assert resolve_config_value("sk-literal", env={}) == "sk-literal"


def test_resolve_config_value_env_name():
    assert resolve_config_value("MY_KEY", env={"MY_KEY": "secret"}) == "secret"


def test_resolve_config_value_command_runs_and_strips():
    calls: list[str] = []

    def runner(command: str) -> str | None:
        calls.append(command)
        return "  cmd-secret\n"

    assert resolve_config_value("!echo hi", env={}, run_command=runner) == "cmd-secret"
    assert calls == ["echo hi"]


# ---- env credential detection ----------------------------------------------


def test_find_env_keys_anthropic_prefers_oauth_token():
    env = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_OAUTH_TOKEN": "t"}
    assert find_env_keys("anthropic", env) == [
        "ANTHROPIC_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
    ]


def test_env_api_key_openai():
    assert env_api_key("openai", {"OPENAI_API_KEY": "sk-x"}) == "sk-x"


def test_openai_codex_has_no_env_api_key():
    # openai-codex requires OAuth; env lookup must not return OPENAI_API_KEY.
    assert env_api_key("openai-codex", {"OPENAI_API_KEY": "sk-x"}) is None
    assert find_env_keys("openai-codex", {"OPENAI_API_KEY": "sk-x"}) is None


def test_google_uses_only_gemini_api_key():
    assert env_api_key("google", {"GEMINI_API_KEY": "g"}) == "g"
    assert env_api_key("google", {"GOOGLE_API_KEY": "g"}) is None


def test_azure_availability_depends_only_on_api_key():
    # No AZURE_OPENAI_ENDPOINT needed for availability (resolved later).
    assert env_api_key("azure-openai", {"AZURE_OPENAI_API_KEY": "k"}) == "k"
    assert env_api_key("azure-openai", {"AZURE_OPENAI_ENDPOINT": "x"}) is None


def test_cloudflare_availability_depends_only_on_api_key():
    assert env_api_key("cloudflare", {"CLOUDFLARE_API_KEY": "k"}) == "k"
    assert env_api_key("cloudflare", {"CLOUDFLARE_ACCOUNT_ID": "x"}) is None


def test_bedrock_ambient_credentials_count_as_available():
    assert (
        env_api_key("amazon-bedrock", {"AWS_PROFILE": "default"}) == "<authenticated>"
    )
    assert (
        env_api_key(
            "amazon-bedrock",
            {"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "b"},
        )
        == "<authenticated>"
    )
    assert env_api_key("amazon-bedrock", {}) is None
    # partial IAM creds do not count
    assert env_api_key("amazon-bedrock", {"AWS_ACCESS_KEY_ID": "a"}) is None


def test_vertex_adc_requires_creds_project_and_location(tmp_path):
    creds = tmp_path / "adc.json"
    creds.write_text("{}", encoding="utf-8")
    full = {
        "GOOGLE_APPLICATION_CREDENTIALS": str(creds),
        "GOOGLE_CLOUD_PROJECT": "proj",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
    }
    assert env_api_key("google-vertex", full) == "<authenticated>"
    # explicit api key path
    assert env_api_key("google-vertex", {"GOOGLE_CLOUD_API_KEY": "k"}) == "k"
    # missing location → not available via ADC
    no_loc = dict(full)
    del no_loc["GOOGLE_CLOUD_LOCATION"]
    assert env_api_key("google-vertex", no_loc) is None


# ---- auth store CRUD + availability ----------------------------------------


def _store(tmp_path) -> AuthStore:
    return AuthStore(path=tmp_path / "auth.json")


def test_auth_store_set_get_remove(tmp_path):
    store = _store(tmp_path)
    nested = {"items": [{"value": "original"}], "tuple": ({"n": 1},)}
    store.set("openai", {"type": "api_key", "key": "sk-stored", **nested})
    nested["items"][0]["value"] = "aliased"
    nested["tuple"][0]["n"] = 2
    stored = store.get("openai")
    assert stored == {
        "type": "api_key",
        "key": "sk-stored",
        "items": [{"value": "original"}],
        "tuple": ({"n": 1},),
    }
    assert isinstance(stored["items"], list)
    assert isinstance(stored["tuple"], tuple)
    store2 = _store(tmp_path)
    persisted = store2.get("openai")
    assert persisted == {**stored, "tuple": [{"n": 1}]}
    assert isinstance(persisted["tuple"], list)
    store2.remove("openai")
    assert _store(tmp_path).get("openai") is None


def test_prepared_auth_reload_is_redacted_detached_validated_and_live_shaped(
    tmp_path,
):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps({"openai": {"type": "api_key", "key": "private", "meta": [1]}}),
        encoding="utf-8",
    )
    store = AuthStore(path=path)
    expected = store.capture_reload_expected()
    prepared = store.prepare_reload_data_from_snapshot(expected)
    assert "private" not in repr(prepared)
    assert isinstance(prepared.validated_data["openai"]["meta"], tuple)
    assert store.validate_prepared_reload_data(prepared)
    assert not store.reload_data_matches_expected(object())
    meta = cast(list[object], cast(dict[str, object], prepared.data["openai"])["meta"])
    meta.append(2)
    assert not store.validate_prepared_reload_data(prepared)
    meta.pop()
    replacement_owner_token = prepared.replacement_owner_token
    store.publish_reload_data(prepared)
    live = store.get("openai")
    assert live == {"type": "api_key", "key": "private", "meta": [1]}
    assert prepared.data == prepared.validated_data == {}
    assert prepared.expected_owner_token is prepared.replacement_owner_token is None
    assert store._reload_identity is replacement_owner_token
    live_identity = store._reload_identity
    store.publish_reload_data(prepared)
    assert store.get("openai") == live and store._reload_identity is live_identity
    assert not store.reload_data_matches_expected(prepared)
    assert "private" not in repr(prepared)


def test_provider_available_reflects_store_env_and_models_json(tmp_path):
    store = _store(tmp_path)
    # via env
    assert provider_available("openai", store=store, env={"OPENAI_API_KEY": "k"})
    # via stored key
    assert not provider_available("openai", store=store, env={})
    store.set("openai", {"type": "api_key", "key": "sk"})
    assert provider_available("openai", store=store, env={})
    # via models.json key on an otherwise-unconfigured custom provider
    assert provider_available(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(api_key="local"),
    )


# ---- auth status labels (no !command exec, no refresh) ---------------------


def test_auth_status_stored(tmp_path):
    store = _store(tmp_path)
    store.set("openai", {"type": "api_key", "key": "sk"})
    status = provider_auth_status("openai", store=store, env={})
    assert status.source == "stored"
    assert status.configured is True


def test_auth_status_environment(tmp_path):
    store = _store(tmp_path)
    status = provider_auth_status("openai", store=store, env={"OPENAI_API_KEY": "k"})
    assert status.source == "environment"
    assert status.label == "OPENAI_API_KEY"


def test_auth_status_models_json_command_does_not_execute(tmp_path):
    store = _store(tmp_path)
    executed: list[str] = []

    status = provider_auth_status(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(api_key="!secret-tool"),
        run_command=lambda c: executed.append(c) or "x",
    )
    assert status.source == "models_json_command"
    assert executed == []  # status never runs the command


def test_auth_status_models_json_key(tmp_path):
    store = _store(tmp_path)
    status = provider_auth_status(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(api_key="local"),
    )
    assert status.source == "models_json_key"
    assert status.configured is True


def test_auth_status_models_json_env_name_reports_environment(tmp_path):
    store = _store(tmp_path)
    status = provider_auth_status(
        "ds4",
        store=store,
        env={"DS4_KEY": "secret"},
        models_json_config=ProviderAuthRequestConfig(api_key="DS4_KEY"),
    )
    assert status.source == "environment"
    assert status.label == "DS4_KEY"
    assert status.configured is True


def test_auth_status_ambient_credentials_not_labelled(tmp_path):
    store = _store(tmp_path)
    # Pi's status only labels actual API-key env vars, never ambient creds.
    status = provider_auth_status(
        "amazon-bedrock", store=store, env={"AWS_PROFILE": "default"}
    )
    assert status.source is None
    assert status.configured is False


# ---- request auth resolution (priority + headers + authHeader) -------------


def test_resolve_request_auth_priority_runtime_over_stored_over_env(tmp_path):
    store = _store(tmp_path)
    store.set("openai", {"type": "api_key", "key": "sk-stored"})
    resolved = resolve_request_auth(
        "openai",
        store=store,
        env={"OPENAI_API_KEY": "sk-env"},
        runtime_api_key="sk-runtime",
    )
    assert resolved.ok and resolved.api_key == "sk-runtime"


def test_resolve_request_auth_stored_over_env(tmp_path):
    store = _store(tmp_path)
    store.set("openai", {"type": "api_key", "key": "sk-stored"})
    resolved = resolve_request_auth(
        "openai", store=store, env={"OPENAI_API_KEY": "sk-env"}
    )
    assert resolved.ok and resolved.api_key == "sk-stored"


def test_resolve_request_auth_models_json_key_is_last(tmp_path):
    store = _store(tmp_path)
    resolved = resolve_request_auth(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(api_key="local"),
    )
    assert resolved.ok and resolved.api_key == "local"


def test_resolve_request_auth_authheader_and_headers(tmp_path):
    store = _store(tmp_path)
    resolved = resolve_request_auth(
        "ds4",
        store=store,
        env={"TOK": "abc"},
        models_json_config=ProviderAuthRequestConfig(
            api_key="TOK",
            auth_header=True,
            headers={"X-Org": "ORG_ENV"},
        ),
        env_for_headers={"ORG_ENV": "org-123"},
    )
    assert resolved.ok
    assert resolved.headers["Authorization"] == "Bearer abc"
    assert resolved.headers["X-Org"] == "org-123"


def test_resolve_request_auth_preserves_resolver_and_header_order(tmp_path):
    store = _store(tmp_path)
    store.set("ds4", {"type": "api_key", "key": "!stored-key"})
    calls: list[str] = []
    command_values = {
        "stored-key": None,
        "models-key": "resolved-key",
        "models-header": "models-value",
        "missing": None,
        "model-header": "model-value",
    }

    def run_command(command: str) -> str | None:
        calls.append(command)
        return command_values[command]

    resolved = resolve_request_auth(
        "ds4",
        store=store,
        env={"HEADER_ENV": "wrong-environment"},
        models_json_config=ProviderAuthRequestConfig(
            api_key="!models-key",
            auth_header=True,
            headers={
                "Authorization": "models-auth",
                "X-Shared": "!models-header",
                "X-Missing": "!missing",
            },
        ),
        model_headers={
            "X-Shared": "!model-header",
            "X-Env": "HEADER_ENV",
            "Authorization": "model-auth",
        },
        env_for_headers={"HEADER_ENV": "header-environment"},
        run_command=run_command,
    )

    assert resolved.ok is True
    assert resolved.api_key == "resolved-key"
    assert resolved.headers == {
        "Authorization": "Bearer resolved-key",
        "X-Shared": "model-value",
        "X-Env": "header-environment",
    }
    assert calls == [
        "stored-key",
        "models-key",
        "models-header",
        "missing",
        "model-header",
    ]


def test_resolve_request_auth_authheader_without_key_fails(tmp_path):
    store = _store(tmp_path)
    resolved = resolve_request_auth(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(auth_header=True),
    )
    assert resolved.ok is False
    assert resolved.error is not None and "ds4" in resolved.error


def test_resolve_request_auth_stored_oauth_via_resolver(tmp_path):
    store = _store(tmp_path)
    store.set(
        "anthropic",
        {"type": "oauth", "access": "tok", "refresh": "r", "expires": 9999999999000},
    )
    resolved = resolve_request_auth(
        "anthropic",
        store=store,
        env={},
        oauth_token_resolver=lambda provider, cred: cred["access"],
    )
    assert resolved.ok and resolved.api_key == "tok"


def test_command_invalid_utf8_is_unavailable(capsys):
    import shlex
    import sys

    command = shlex.join(
        [sys.executable, "-c", "import os; os.write(1, b'fake-marker\\xff')"]
    )
    assert resolve_config_value("!" + command, env={}) is None
    captured = capsys.readouterr()
    assert "fake-marker" not in captured.out + captured.err


def test_command_unicode_success_and_nonzero():
    import shlex
    import sys

    for script, expected in [
        ("import os; os.write(1, '  clé  '.encode('utf-8'))", "clé"),
        ("print('invented'); raise SystemExit(1)", None),
    ]:
        command = shlex.join([sys.executable, "-c", script])
        assert resolve_config_value("!" + command, env={}) == expected


def test_command_timeout_owns_descendant_group(monkeypatch):
    import signal
    from unittest.mock import MagicMock

    from pipy_harness.native import auth_store

    process = MagicMock()
    process.pid = 12345
    selector = MagicMock()
    selector.__enter__.return_value = selector
    selector.get_map.return_value = {1: object()}
    # The child keeps pipes open; no readiness arrives before the deadline.
    selector.select.return_value = []
    from types import SimpleNamespace

    clock = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(auth_store.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(auth_store.selectors, "DefaultSelector", lambda: selector)
    monkeypatch.setattr(
        auth_store, "time", SimpleNamespace(monotonic=lambda: next(clock, 10.0))
    )
    monkeypatch.setattr(auth_store.os, "set_blocking", lambda *a: None)
    signals = []
    monkeypatch.setattr(
        auth_store.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )
    assert resolve_config_value("!synthetic-child", env={}) is None
    # Signal 0 entries are liveness probes of the surviving group.
    assert [entry for entry in signals if entry[1] != 0] == [
        (12345, signal.SIGTERM),
        (12345, signal.SIGKILL),
    ]
    selector.select.assert_called_once_with(10.0)
    process.wait.assert_called_with(timeout=auth_store._COMMAND_CLEANUP_GRACE)
    process.stdout.close.assert_called_once()


def test_command_output_is_bounded(monkeypatch):
    from pipy_harness.native import auth_store

    monkeypatch.setattr(auth_store, "_COMMAND_OUTPUT_LIMIT", 32)
    assert resolve_config_value("!printf '%1000s' x", env={}) is None


def test_command_real_descendant_pipe_cleanup(monkeypatch, tmp_path):
    import os
    import shlex
    import sys

    import pytest

    from pipy_harness.native import auth_store

    if not hasattr(os, "fork"):
        pytest.skip("POSIX process groups required")
    pid_file = tmp_path / "child.pid"
    script = """
import os, signal
read_fd, write_fd = os.pipe()
child = os.fork()
if child == 0:
    os.close(write_fd)
    os.read(read_fd, 1)
    print("ready", flush=True)
    while True:
        signal.pause()
def stop(sig, frame):
    os.waitpid(child, 0)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
with open(os.environ["SYNTHETIC_CHILD_PID"], "w") as f:
    f.write(str(child))
os.close(read_fd)
os.write(write_fd, b"x")
os.close(write_fd)
while True:
    signal.pause()
"""
    monkeypatch.setenv("SYNTHETIC_CHILD_PID", str(pid_file))
    import time
    from types import SimpleNamespace

    original_read = auth_store._read_command_output
    started = time.monotonic()
    ready = False

    def read(fd, size):
        nonlocal ready
        data = original_read(fd, size)
        # Only advance after the child has started and holds stdout open.
        if data:
            ready = True
        return data

    monkeypatch.setattr(auth_store, "_read_command_output", read)
    monkeypatch.setattr(
        auth_store,
        "time",
        SimpleNamespace(
            monotonic=lambda: float("inf") if ready else time.monotonic() - started
        ),
    )
    monkeypatch.setattr(auth_store, "_COMMAND_CLEANUP_GRACE", 5.0)
    command = "exec " + shlex.join([sys.executable, "-c", script])
    assert resolve_config_value("!" + command, env={}) is None
    assert ready
    child = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_completed_command_does_not_terminate_group(monkeypatch):
    from pipy_harness.native import auth_store

    signals = []
    monkeypatch.setattr(auth_store.os, "killpg", lambda *args: signals.append(args))
    assert resolve_config_value("!printf synthetic", env={}) == "synthetic"
    assert signals == []


def test_command_exact_limit_and_ignored_stderr(monkeypatch):
    from pipy_harness.native import auth_store

    monkeypatch.setattr(auth_store, "_COMMAND_OUTPUT_LIMIT", 8)
    assert resolve_config_value("!printf 12345678", env={}) == "12345678"
    assert resolve_config_value("!printf 123456789", env={}) is None
    # As Pi (`stdio: [..., "pipe", "ignore"]`), stderr is not read and does
    # not count toward the limit.
    assert resolve_config_value("!printf 12345678; printf x >&2", env={}) == "12345678"


def test_command_output_limit_is_node_max_buffer():
    # Pi runs the command with `execSync` and Node's default `maxBuffer`.
    mib = 1024 * 1024
    big_stderr = "head -c 200000 /dev/zero | tr '\\0' e >&2"
    assert resolve_config_value(f"!{big_stderr}; printf key", env={}) == "key"
    exact = resolve_config_value(f"!head -c {mib} /dev/zero | tr '\\0' k", env={})
    assert exact == "k" * mib
    over = f"!head -c {mib + 1} /dev/zero | tr '\\0' k"
    assert resolve_config_value(over, env={}) is None


def test_command_cleanup_reaps_even_if_group_signal_fails(monkeypatch):
    from unittest.mock import MagicMock

    from pipy_harness.native import auth_store

    process = MagicMock()

    def denied(*args):
        raise PermissionError("synthetic")

    monkeypatch.setattr(auth_store.os, "killpg", denied)
    monkeypatch.setattr(auth_store, "_COMMAND_CLEANUP_GRACE", 0.02)
    auth_store._stop_command(process)
    assert process.poll.call_count >= 2
    process.wait.assert_called_once_with(timeout=0.02)


def test_command_cleanup_skips_kill_once_group_is_gone(monkeypatch):
    import signal
    from unittest.mock import MagicMock

    from pipy_harness.native import auth_store

    process = MagicMock()
    process.pid = 12345
    signals = []

    def killpg(pid, sig):
        signals.append((pid, sig))
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(auth_store.os, "killpg", killpg)
    auth_store._stop_command(process)
    assert signals == [(12345, signal.SIGTERM), (12345, 0)]
    process.wait.assert_not_called()


def test_command_timeout_grants_grace_to_descendant_of_exited_leader(
    monkeypatch, tmp_path
):
    import os
    import shlex
    import sys
    import time
    from types import SimpleNamespace

    import pytest

    from pipy_harness.native import auth_store

    if not hasattr(os, "fork"):
        pytest.skip("POSIX process groups required")
    marker = tmp_path / "cleaned"
    script = """
import os, signal, sys, time
def stop(sig, frame):
    time.sleep(0.05)
    with open(os.environ["SYNTHETIC_MARKER"], "w") as f:
        f.write(str(os.getpid()))
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
leader = int(sys.argv[1])
while os.getppid() == leader:
    time.sleep(0.005)
os.write(1, b"ready")
while True:
    signal.pause()
"""
    monkeypatch.setenv("SYNTHETIC_MARKER", str(marker))
    original_read = auth_store._read_command_output
    started = time.monotonic()
    ready = False

    def read(fd, size):
        nonlocal ready
        data = original_read(fd, size)
        # The descendant only writes once the shell leader has exited.
        if data:
            ready = True
        return data

    monkeypatch.setattr(auth_store, "_read_command_output", read)
    monkeypatch.setattr(
        auth_store,
        "time",
        SimpleNamespace(
            monotonic=lambda: float("inf") if ready else time.monotonic() - started
        ),
    )
    monkeypatch.setattr(auth_store, "_COMMAND_CLEANUP_GRACE", 5.0)
    command = shlex.join([sys.executable, "-c", script]) + ' "$$" &'
    assert resolve_config_value("!" + command, env={}) is None
    assert ready
    # The descendant finished its TERM handler instead of being killed at once.
    descendant = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(descendant, 0)
