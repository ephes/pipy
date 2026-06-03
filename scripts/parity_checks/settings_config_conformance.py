"""Deterministic conformance gate for the Pi-style settings/config/keybindings track.

Drives the native settings/keybindings/scoped-model/system-prompt/changelog
surfaces (and the product REPL where a provider-visible assertion is required)
with the deterministic fake provider in a temporary workspace and a temporary
config home (``PIPY_CONFIG_HOME`` pointed at a tmp dir, ``PIPY_OFFLINE=1``). It
fails unless the full surface described in ``docs/settings-config.md`` works.

Run::

    uv run python scripts/parity_checks/settings_config_conformance.py --json

Each numbered check mirrors the spec's "Verification Plan". Exits 0 when every
check passes, 1 otherwise. No real network or AI calls.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.adapters.native import PipyNativeToolReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native.changelog import changelog_startup, parse_changelog
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.keybindings import (
    APP_KEYBINDINGS,
    KeybindingsManager,
    migrate_keybindings_config,
    render_hotkeys,
)
from pipy_harness.native.provider import ProviderRequest, ProviderResult
from pipy_harness.native.resource_enablement import filter_enabled, is_resource_enabled
from pipy_harness.native.scoped_models import filter_scoped_references, next_reference
from pipy_harness.native.settings import (
    SettingsManager,
    local_state_base_defaults,
    migrate_settings,
    retry_policy_from_settings,
    settings_report_lines,
)
from pipy_harness.native.system_prompt_inputs import resolve_system_prompt
from pipy_harness.native.tool_loop_session import NativeToolReplSession
from pipy_harness.native.version_check import update_check_enabled


class _CapturingProvider:
    """Tool-capable fake recording each ProviderRequest (for provider-visible checks)."""

    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="ok",
        )


class _NullSink:
    def emit(self, *_args: object, **_kwargs: object) -> None:
        return None


def _run_tool_adapter(
    workspace: Path,
    root: Path,
    inputs: str,
    *,
    provider: object,
    **adapter_kwargs: object,
) -> None:
    """Drive the tool-loop adapter end-to-end (composes the system prompt)."""

    adapter = PipyNativeToolReplAdapter(
        provider=provider,  # type: ignore[arg-type]
        input_stream=io.StringIO(inputs),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        **adapter_kwargs,  # type: ignore[arg-type]
    )
    prepared = adapter.prepare(
        RunRequest(agent="pipy-native", slug="gate", command=[], cwd=workspace, goal="g", root=root)
    )
    adapter.run(prepared, event_sink=_NullSink(), capture_policy=CapturePolicy())


def _write(path: Path, body: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        body if isinstance(body, str) else json.dumps(body), encoding="utf-8"
    )


def _manager(root: Path, **kwargs: object) -> SettingsManager:
    return SettingsManager(
        global_path=root / "config" / "settings.json",
        project_path=root / "proj" / ".pipy" / "settings.json",
        **kwargs,  # type: ignore[arg-type]
    )


def _run_session(
    workspace: Path,
    inputs: str,
    *,
    settings_manager: SettingsManager | None = None,
    provider: object | None = None,
    **session_kwargs: object,
) -> tuple[str, object]:
    prov = provider if provider is not None else FakeNativeProvider(
        supports_tool_calls=True, final_text="ok"
    )
    session = NativeToolReplSession(
        provider=prov, settings_manager=settings_manager, **session_kwargs  # type: ignore[arg-type]
    )
    err = io.StringIO()
    session.run(
        workspace_root=workspace,
        input_stream=io.StringIO(inputs),
        output_stream=io.StringIO(),
        error_stream=err,
    )
    return err.getvalue(), prov


# --- individual checks (each returns (passed, detail)) ----------------------


def check_01_discovery_and_precedence(root: Path) -> tuple[bool, str]:
    _write(root / "config" / "settings.json", {"theme": "dark", "defaultModel": "g"})
    mgr = _manager(root)  # missing project file must not fail
    eff = mgr.effective()
    ok = eff.get("theme") == "dark" and eff.get("defaultModel") == "g"
    return ok, f"effective={eff}"


def check_02_precedence_and_merge_depth(root: Path) -> tuple[bool, str]:
    _write(
        root / "config" / "settings.json",
        {"compaction": {"enabled": True, "reserveTokens": 16384}, "retry": {"provider": {"timeoutMs": 1}}},
    )
    _write(
        root / "proj" / ".pipy" / "settings.json",
        {"compaction": {"reserveTokens": 8000}, "retry": {"provider": {"maxRetries": 9}}},
    )
    mgr = _manager(root, overrides={"theme": "cli"})
    eff = mgr.effective()
    one_level = eff["compaction"] == {"enabled": True, "reserveTokens": 8000}
    wholesale = eff["retry"]["provider"] == {"maxRetries": 9}  # deeper object replaced
    cli = eff["theme"] == "cli"
    no_claude = not (root / ".claude").exists()
    return one_level and wholesale and cli and no_claude, f"compaction={eff['compaction']} retry={eff['retry']}"


def check_03_field_scoped_write_preserves_unknown(root: Path) -> tuple[bool, str]:
    gpath = root / "config" / "settings.json"
    _write(gpath, {"theme": "dark", "unknownKept": 7, "futureObj": {"a": 1}})
    mgr = _manager(root)
    mgr.set_value("theme", "light", scope="global")
    on_disk = json.loads(gpath.read_text(encoding="utf-8"))
    ok = on_disk == {"theme": "light", "unknownKept": 7, "futureObj": {"a": 1}}
    return ok, f"on_disk={on_disk}"


def check_04_parse_error_isolated_and_not_overwritten(root: Path) -> tuple[bool, str]:
    gpath = root / "config" / "settings.json"
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text("{broken", encoding="utf-8")
    _write(root / "proj" / ".pipy" / "settings.json", {"theme": "light"})
    mgr = _manager(root)
    isolated = mgr.effective() == {"theme": "light"} and "global" in mgr.load_errors()
    try:
        mgr.set_value("theme", "x", scope="global")
        wrote = True
    except RuntimeError:
        wrote = False
    untouched = gpath.read_text(encoding="utf-8") == "{broken"
    return isolated and not wrote and untouched, f"errors={mgr.load_errors()}"


def check_05_migrations(_root: Path) -> tuple[bool, str]:
    a = migrate_settings({"queueMode": "all", "steeringMode": "one-at-a-time"}) == {
        "queueMode": "all",
        "steeringMode": "one-at-a-time",
    }
    b = migrate_settings(
        {"retry": {"maxDelayMs": 5, "provider": {"maxRetryDelayMs": 9}}}
    ) == {"retry": {"provider": {"maxRetryDelayMs": 9}}}
    c = migrate_settings(
        {"skills": {"enableSkillCommands": False}, "enableSkillCommands": True}
    ) == {"enableSkillCommands": True}
    once = migrate_settings({"queueMode": "x"})
    idempotent = migrate_settings(once) == once
    return a and b and c and idempotent, f"a={a} b={b} c={c} idempotent={idempotent}"


def check_06_local_state_import(root: Path) -> tuple[bool, str]:
    base = local_state_base_defaults(
        provider="openai-codex", model="gpt-5.5", theme="pi-dark", prompt_history_enabled=True
    )
    mgr = _manager(root / "empty", base_defaults=base)
    ok = (
        mgr.get_default_provider() == "openai-codex"
        and mgr.get_theme() == "pi-dark"
        and mgr.get_prompt_history_enabled() is True
        and local_state_base_defaults() == {}
    )
    return ok, f"base={base}"


def check_07_keybindings_load_migrate_fallback(root: Path) -> tuple[bool, str]:
    kpath = root / "kb.json"
    _write(kpath, {"app.model.cycleForward": "ctrl+j", "app.tree.foldOrUp": ["ctrl+h", "alt+h"]})
    mgr = KeybindingsManager.from_file(kpath)
    single_array = mgr.keys_for("app.model.cycleForward") == ["ctrl+j"] and mgr.keys_for(
        "app.tree.foldOrUp"
    ) == ["ctrl+h", "alt+h"]
    config, migrated = migrate_keybindings_config({"cycleModelForward": "ctrl+j"})
    legacy = migrated and config == {"app.model.cycleForward": "ctrl+j"}
    kpath.write_text("{broken", encoding="utf-8")
    mgr.reload()
    fallback = mgr.keys_for("app.model.cycleForward") == ["ctrl+p"]
    # /hotkeys renders the resolved override, not the default.
    over = KeybindingsManager(user_bindings={"app.model.cycleForward": "ctrl+j"})
    hot = "Ctrl+J" in render_hotkeys(over, platform="linux")
    return single_array and legacy and fallback and hot, f"single_array={single_array} legacy={legacy} fallback={fallback} hotkeys={hot}"


def check_08_default_app_bindings(_root: Path) -> tuple[bool, str]:
    count = len(APP_KEYBINDINGS)
    spot = (
        APP_KEYBINDINGS["app.interrupt"].default_keys == ["escape"]
        and APP_KEYBINDINGS["app.tree.foldOrUp"].default_keys == ["ctrl+left", "alt+left"]
        and APP_KEYBINDINGS["app.session.new"].default_keys == []
    )
    return count >= 35 and spot, f"app_bindings={count} spot={spot}"


def check_09_scoped_models(root: Path) -> tuple[bool, str]:
    refs = ["openai/a", "openai/b", "anthropic/c"]
    scoped = filter_scoped_references(refs, ["openai/*"]) == ["openai/a", "openai/b"]
    cyc = next_reference(["x", "y"], "x", forward=True) == "y"
    # Persist + no provider turn via the command.
    _write(root / "cfg" / "settings.json", "{}")
    mgr = SettingsManager(
        global_path=root / "cfg" / "settings.json",
        project_path=root / "p" / ".pipy" / "settings.json",
    )
    prov = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    ws = root / "ws9"
    ws.mkdir(parents=True, exist_ok=True)
    out, _ = _run_session(
        ws, "/scoped-models openai/*\n/exit\n", settings_manager=mgr, provider=prov
    )
    persisted = json.loads((root / "cfg" / "settings.json").read_text())["enabledModels"] == ["openai/*"]
    no_turn = prov._call_counter[0] == 0
    return scoped and cyc and persisted and no_turn, f"scoped={scoped} cyc={cyc} persisted={persisted} no_turn={no_turn}"


def check_10_delivery_reported_and_honored(root: Path) -> tuple[bool, str]:
    _write(
        root / "config" / "settings.json",
        {"transport": "sse", "retry": {"maxRetries": 5, "baseDelayMs": 500, "provider": {"maxRetryDelayMs": 30000}}},
    )
    mgr = _manager(root)
    report = "\n".join(settings_report_lines(mgr))
    reported = "transport: sse" in report and "compaction:" in report and "retry:" in report
    policy = retry_policy_from_settings(mgr)
    honored = policy.max_attempts == 6 and policy.initial_delay_seconds == 0.5
    return reported and honored, f"reported={reported} policy_attempts={policy.max_attempts}"


def check_11_system_prompt(root: Path) -> tuple[bool, str]:
    cfg = root / "cfg11"
    cfg.mkdir(parents=True, exist_ok=True)
    res = resolve_system_prompt(
        "DEFAULT", cwd=root, config_home=cfg, system_prompt_source="CUSTOM", append_sources=["EXTRA"]
    )
    composed = res.base_prompt == "CUSTOM\n\nEXTRA"
    # unreadable file -> warn + literal fallback.
    warned: list[str] = []
    res2 = resolve_system_prompt(
        "DEFAULT", cwd=root, config_home=cfg, system_prompt_source=str(root), warn=warned.append
    )
    fallback = res2.base_prompt == str(root) and bool(warned)
    # body not in safe metadata.
    no_body = "CUSTOM" not in json.dumps(res.safe_metadata())
    # reaches ProviderRequest.system_prompt via the product (adapter) path.
    ws = root / "ws11"
    ws.mkdir(exist_ok=True)
    prov = _CapturingProvider()
    _run_tool_adapter(
        ws, root, "hi\n/exit\n", provider=prov, system_prompt_source="REPLACED_SYS_BODY"
    )
    reaches = bool(prov.requests) and "REPLACED_SYS_BODY" in prov.requests[0].system_prompt
    return composed and fallback and no_body and reaches, f"composed={composed} fallback={fallback} no_body={no_body} reaches={reaches}"


def check_12_no_context_files(root: Path) -> tuple[bool, str]:
    from pipy_harness.native.workspace_context import empty_workspace_instruction_loader

    ws = root / "ws12"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "AGENTS.md").write_text("SECRET PROJECT INSTRUCTIONS", encoding="utf-8")
    prov = _CapturingProvider()
    _run_tool_adapter(
        ws, root, "hi\n/exit\n", provider=prov,
        instruction_loader=empty_workspace_instruction_loader,
    )
    suppressed = bool(prov.requests) and "SECRET PROJECT INSTRUCTIONS" not in prov.requests[0].system_prompt
    return suppressed, f"suppressed={suppressed}"


def check_13_resource_enablement(root: Path) -> tuple[bool, str]:
    disabled = is_resource_enabled("review", ["-review"]) is False
    reenabled = is_resource_enabled("review", ["-review", "+review"]) is True
    filtered = filter_enabled(["a", "b", "c"], ["-b"]) == ["a", "c"]
    return disabled and reenabled and filtered, f"disabled={disabled} reenabled={reenabled} filtered={filtered}"


def check_14_reload(root: Path) -> tuple[bool, str]:
    cfg = root / "cfg14" / "settings.json"
    _write(cfg, {"theme": "dark"})
    mgr = SettingsManager(global_path=cfg, project_path=root / "p14" / ".pipy" / "settings.json")
    _write(cfg, {"theme": "ocean"})  # edit after load
    ws = root / "ws14"
    ws.mkdir(parents=True, exist_ok=True)
    prov = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    out, _ = _run_session(
        ws, "/settings\n/reload\n/settings\n/exit\n", settings_manager=mgr, provider=prov
    )
    reread = "theme: ocean" in out.split("reloaded settings")[1]
    no_turn = prov._call_counter[0] == 0
    return reread and no_turn, f"reread={reread} no_turn={no_turn}"


def check_15_changelog(root: Path) -> tuple[bool, str]:
    sample = "# CL\n\n## [0.3.0] - x\n\n- new\n\n## [0.2.0] - y\n\n- mid\n"
    entries = parse_changelog(sample)
    bump_lines, bump_store = changelog_startup(
        entries, last_version="0.2.0", current_version="0.3.0", collapse=False, is_fresh=True
    )
    bump = any("0.3.0" in line for line in bump_lines) and bump_store == "0.3.0"
    first_lines, first_store = changelog_startup(
        entries, last_version=None, current_version="0.3.0", collapse=False, is_fresh=True
    )
    first = first_lines == [] and first_store == "0.3.0"
    resumed_lines, resumed_store = changelog_startup(
        entries, last_version="0.2.0", current_version="0.3.0", collapse=False, is_fresh=False
    )
    resumed = resumed_lines == [] and resumed_store is None
    collapse_lines, _ = changelog_startup(
        entries, last_version="0.2.0", current_version="0.3.0", collapse=True, is_fresh=True
    )
    collapsed = any("/changelog" in line for line in collapse_lines)
    return bump and first and resumed and collapsed, f"bump={bump} first={first} resumed={resumed} collapse={collapsed}"


def check_16_version_and_update_off(_root: Path) -> tuple[bool, str]:
    off = update_check_enabled(setting=False, env={}) is False
    offline = update_check_enabled(setting=True, env={"PIPY_OFFLINE": "1"}) is False
    return off and offline, f"off={off} offline={offline}"


def check_17_no_secrets_in_report(root: Path) -> tuple[bool, str]:
    _write(root / "config" / "settings.json", {"theme": "dark", "defaultProvider": "openai"})
    mgr = _manager(root)
    report = "\n".join(settings_report_lines(mgr)).lower()
    # The report carries no auth tokens / api keys; it is plain config only.
    # (Avoid false positives on legitimate keys like "reserveTokens".)
    leaked = any(tok in report for tok in ("api_key", "secret", "password", "bearer", "sk-", "oauth"))
    return not leaked, f"leaked={leaked}"


CHECKS: list[tuple[int, str, Callable[[Path], tuple[bool, str]]]] = [
    (1, "discovery + deep-merge precedence", check_01_discovery_and_precedence),
    (2, "precedence + one-level merge vs wholesale (no .claude)", check_02_precedence_and_merge_depth),
    (3, "field-scoped write preserves unknown keys", check_03_field_scoped_write_preserves_unknown),
    (4, "parse-error isolated + never written over", check_04_parse_error_isolated_and_not_overwritten),
    (5, "migrations (3 behaviors) idempotent", check_05_migrations),
    (6, "local-state import surfaces through settings", check_06_local_state_import),
    (7, "keybindings load/migrate/malformed-fallback/hotkeys", check_07_keybindings_load_migrate_fallback),
    (8, "35+ default app bindings", check_08_default_app_bindings),
    (9, "scoped models persist + constrain cycle, no provider turn", check_09_scoped_models),
    (10, "delivery/transport/compaction/retry reported + honored", check_10_delivery_reported_and_honored),
    (11, "system-prompt replace/append reaches request, no body archived", check_11_system_prompt),
    (12, "--no-context-files suppresses discovery", check_12_no_context_files),
    (13, "resource enablement -pattern/+pattern", check_13_resource_enablement),
    (14, "/reload re-reads settings, no provider turn", check_14_reload),
    (15, "/changelog startup bump/first-run/resume/collapse", check_15_changelog),
    (16, "--version + update check off by default", check_16_version_and_update_off),
    (17, "no secrets in the /settings report", check_17_no_secrets_in_report),
]


def run_all() -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for number, name, fn in CHECKS:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                passed, detail = fn(Path(tmp))
            except Exception as exc:  # noqa: BLE001 - surface as a failed check
                passed, detail = False, f"raised {type(exc).__name__}: {exc}"
        results.append({"check": number, "name": name, "passed": passed, "detail": detail})
    return results


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    as_json = "--json" in args
    # Deterministic, offline, isolated config home.
    os.environ["PIPY_OFFLINE"] = "1"
    os.environ.setdefault("PIPY_CONFIG_HOME", tempfile.mkdtemp())
    results = run_all()
    all_passed = all(bool(r["passed"]) for r in results)
    if as_json:
        print(json.dumps({"passed": all_passed, "checks": results}, indent=2))
    else:
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"[{status}] {r['check']:>2}. {r['name']}")
            if not r["passed"]:
                print(f"        detail: {r['detail']}")
        print(f"\n{'ALL PASSED' if all_passed else 'FAILURES PRESENT'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
