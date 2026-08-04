from __future__ import annotations

import io
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pipy_harness.cli import (
    KNOWN_SUBCOMMANDS,
    _resolve_runtime_project_trust,
    _resolve_runtime_project_trust_startup,
    build_parser,
    main,
    route_argv,
)
from pipy_harness.native.extensions.packages import discover_extensions
from pipy_harness.native.package_runtime import compose_package_runtime
from pipy_harness.native.project_trust import (
    PROTECTED_PROJECT_ENTRIES,
    ProjectTrustError,
    ProjectTrustExtensionDecision,
    ProjectTrustResolution,
    ProjectTrustStore,
    get_project_trust_options,
    has_trust_requiring_project_resources,
    resolve_project_trust,
    resolve_project_trusted,
)
from pipy_harness.native.repl.reload import (
    ImplicitTrustState,
    maybe_save_implicit_trust_after_reload,
)
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.system_prompt_inputs import resolve_system_prompt


def _write_json(path: Path, body: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _write_resource(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {name}\n---\n{name}\n")


def test_store_uses_closest_ancestor_and_delete_reveals_parent(tmp_path: Path) -> None:
    store = ProjectTrustStore(tmp_path / "config" / "trust.json")
    parent = tmp_path / "work"
    child = parent / "child"
    child.mkdir(parents=True)
    store.set(parent, True)
    store.set(child, False)
    assert store.get_entry(child) is not None
    assert store.get_entry(child).path == child.resolve()  # type: ignore[union-attr]
    assert store.get(child) is False
    store.set(child, None)
    entry = store.get_entry(child)
    assert entry is not None
    assert entry.path == parent.resolve()
    assert entry.decision is True


def test_store_canonicalizes_symlink_aliases(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    store = ProjectTrustStore(tmp_path / "config" / "trust.json")
    store.set(alias, True)
    assert store.get(real) is True
    assert list(json.loads(store.path.read_text())) == [str(real.resolve())]


@pytest.mark.parametrize("body", [[], True, {"/tmp/x": 1}, {"/tmp/x": "yes"}])
def test_store_rejects_invalid_schema_without_overwriting(
    tmp_path: Path, body: object
) -> None:
    path = tmp_path / "config" / "trust.json"
    _write_json(path, body)
    before = path.read_bytes()
    store = ProjectTrustStore(path)
    with pytest.raises(ProjectTrustError):
        store.set(tmp_path, True)
    assert path.read_bytes() == before


def test_store_accepts_null_and_writes_sorted_private_json(tmp_path: Path) -> None:
    path = tmp_path / "config" / "trust.json"
    _write_json(path, {str(tmp_path / "unused"): None})
    store = ProjectTrustStore(path)
    store.set_many(((tmp_path / "z", False), (tmp_path / "a", True)))
    body = json.loads(path.read_text())
    assert list(body) == sorted(body)
    assert path.read_text().endswith("\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.get(tmp_path / "unused") is None


def test_store_lock_contention_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pipy_harness.native.project_trust._LOCK_TIMEOUT_SECONDS", 0.005
    )
    monkeypatch.setattr(
        "pipy_harness.native.project_trust._LOCK_INITIAL_BACKOFF_SECONDS", 0.001
    )
    monkeypatch.setattr(
        "pipy_harness.native.project_trust._LOCK_MAX_BACKOFF_SECONDS", 0.002
    )
    path = tmp_path / "config" / "trust.json"
    path.parent.mkdir(parents=True)
    lock = path.with_name("trust.json.lock")
    lock.write_text("held")
    with pytest.raises(ProjectTrustError):
        ProjectTrustStore(path).set(tmp_path, True)
    assert not path.exists()
    assert lock.exists()


def test_store_reads_ignore_writer_lock_and_dead_writer_lock_is_recovered(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config" / "trust.json"
    store = ProjectTrustStore(path)
    store.set(tmp_path / "project", True)
    lock = path.with_name("trust.json.lock")
    lock.write_text(json.dumps({"pid": 99_999_999, "created": 0}))
    assert store.get(tmp_path / "project") is True
    store.set(tmp_path / "other", False)
    assert store.get(tmp_path / "other") is False
    assert not lock.exists()


def test_store_serializes_concurrent_updates_without_losing_entries(
    tmp_path: Path,
) -> None:
    store = ProjectTrustStore(tmp_path / "config" / "trust.json")

    def write(index: int) -> None:
        store.set(tmp_path / f"project-{index}", index % 2 == 0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(16)))
    body = json.loads(store.path.read_text())
    assert len(body) == 16
    assert all(isinstance(value, bool) for value in body.values())


@pytest.mark.parametrize("entry", PROTECTED_PROJECT_ENTRIES)
def test_detector_recognizes_each_protected_entry(tmp_path: Path, entry: str) -> None:
    candidate = tmp_path / ".pipy" / entry
    candidate.parent.mkdir(parents=True, exist_ok=True)
    if "." in Path(entry).name:
        candidate.write_text("x")
    else:
        candidate.mkdir()
    assert has_trust_requiring_project_resources(tmp_path)


def test_detector_ignores_bare_config_and_context_files(tmp_path: Path) -> None:
    (tmp_path / ".pipy").mkdir()
    (tmp_path / "AGENTS.md").write_text("instructions")
    (tmp_path / "pipy.md").write_text("instructions")
    (tmp_path / ".agents" / "skills").mkdir(parents=True)
    (tmp_path / ".pipy" / "themes").mkdir()
    assert not has_trust_requiring_project_resources(tmp_path)


def test_resolver_order_and_no_resource_short_circuit(tmp_path: Path) -> None:
    path = tmp_path / "config" / "trust.json"
    store = ProjectTrustStore(path)
    assert resolve_project_trusted(tmp_path, trust_store=store) is True
    assert not path.parent.exists()

    (tmp_path / ".pipy").mkdir()
    (tmp_path / ".pipy" / "settings.json").write_text("{}")
    store.set(tmp_path, False)
    assert (
        resolve_project_trusted(
            tmp_path,
            trust_store=store,
            trust_override=True,
            default_project_trust="never",
        )
        is True
    )
    assert (
        resolve_project_trusted(
            tmp_path, trust_store=store, default_project_trust="always"
        )
        is False
    )


def test_extension_decision_rung_precedes_saved_default_and_ui(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    (cwd / ".pipy" / "skills").mkdir(parents=True)
    store = ProjectTrustStore(tmp_path / "config" / "trust.json")
    store.set(cwd, False)
    calls: list[Path] = []
    selected: list[Path] = []

    def decide(path: Path) -> ProjectTrustExtensionDecision:
        calls.append(path)
        return ProjectTrustExtensionDecision(True)

    def select(path: Path) -> bool:
        selected.append(path)
        return False

    resolution = resolve_project_trust(
        cwd,
        trust_store=store,
        default_project_trust="never",
        extension_decision=decide,
        select=select,
    )

    assert resolution.trusted is True
    assert resolution.source == "extension"
    assert calls == [cwd.resolve()]
    assert selected == []
    assert store.get(cwd) is False


def test_extension_decision_exact_remember_and_suppressed_rungs(tmp_path: Path) -> None:
    store = ProjectTrustStore(tmp_path / "config" / "trust.json")
    calls: list[Path] = []

    def undecided(path: Path) -> None:
        calls.append(path)

    assert (
        resolve_project_trust(
            tmp_path,
            trust_store=store,
            extension_decision=undecided,
        ).source
        == "no_resources"
    )
    (tmp_path / ".pipy" / "skills").mkdir(parents=True)
    assert (
        resolve_project_trust(
            tmp_path,
            trust_store=store,
            trust_override=False,
            extension_decision=undecided,
        ).source
        == "override"
    )
    assert calls == []

    decision = ProjectTrustExtensionDecision(False, remember=True)

    def decide(path: Path) -> ProjectTrustExtensionDecision:
        calls.append(path)
        return decision

    resolution = resolve_project_trust(
        tmp_path,
        trust_store=store,
        extension_decision=decide,
    )
    assert resolution.source == "extension"
    assert resolution.trusted is False
    assert calls == [tmp_path.resolve()]
    assert store.get(tmp_path) is False


def test_extension_remember_store_error_diagnoses_then_fails_closed(
    tmp_path: Path,
) -> None:
    (tmp_path / ".pipy" / "skills").mkdir(parents=True)
    calls: list[str] = []

    class FailingRememberStore(ProjectTrustStore):
        def set(self, cwd: Path | str, decision: bool | None) -> None:
            calls.append(f"store:{decision}")
            raise ProjectTrustError("remember failed")

        def get(self, cwd: Path | str) -> bool | None:
            raise AssertionError("saved trust rung ran after remember failure")

    def decide(_path: Path) -> ProjectTrustExtensionDecision:
        calls.append("extension")
        return ProjectTrustExtensionDecision(True, remember=True)

    def unexpected_select(_path: Path) -> bool:
        raise AssertionError("selection ran after remember failure")

    resolution = resolve_project_trust(
        tmp_path,
        trust_store=FailingRememberStore(tmp_path / "trust.json"),
        extension_decision=decide,
        select=unexpected_select,
        on_diagnostic=lambda message: calls.append(f"diagnostic:{message}"),
    )

    assert resolution == ProjectTrustResolution(False, "store_error", True)
    assert calls == ["extension", "store:True", "diagnostic:remember failed"]


def test_resolver_global_default_selector_and_malformed_store(tmp_path: Path) -> None:
    (tmp_path / ".pipy").mkdir()
    (tmp_path / ".pipy" / "skills").mkdir()
    store = ProjectTrustStore(tmp_path / "config" / "trust.json")
    assert resolve_project_trusted(
        tmp_path, trust_store=store, default_project_trust="always"
    )
    assert not resolve_project_trusted(
        tmp_path, trust_store=store, default_project_trust="never"
    )
    assert resolve_project_trusted(
        tmp_path, trust_store=store, select=lambda _cwd: True
    )
    assert not resolve_project_trusted(tmp_path, trust_store=store)
    _write_json(store.path, [])
    diagnostics: list[str] = []
    assert not resolve_project_trusted(
        tmp_path, trust_store=store, on_diagnostic=diagnostics.append
    )
    assert diagnostics and "expected an object" in diagnostics[0]


def test_trust_options_match_pi_order_and_parent_updates_are_atomic(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "parent" / "child"
    cwd.mkdir(parents=True)
    options = get_project_trust_options(cwd, include_session_only=True)
    assert [option.label for option in options] == [
        "Trust",
        f"Trust parent folder ({cwd.parent.resolve()})",
        "Trust (this session only)",
        "Do not trust",
        "Do not trust (this session only)",
    ]
    assert options[1].updates == (
        (cwd.parent.resolve(), True),
        (cwd.resolve(), None),
    )


def test_interactive_option_is_saved_before_its_resolution_wins(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "project"
    (cwd / ".pipy" / "skills").mkdir(parents=True)
    store = ProjectTrustStore(tmp_path / "config" / "trust.json")
    selected = get_project_trust_options(cwd, include_session_only=True)[1]
    resolution = resolve_project_trust(
        cwd,
        trust_store=store,
        select=lambda _cwd: selected,
    )
    assert resolution.trusted
    assert resolution.source == "selection"
    entry = store.get_entry(cwd)
    assert entry is not None
    assert entry.path == cwd.parent.resolve()
    assert entry.decision is True


def test_reload_auto_persists_only_newly_materialized_project_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    settings = SettingsManager.for_workspace(cwd, project_trusted=True)
    implicit_trust = ImplicitTrustState(cwd=cwd)
    error = io.StringIO()
    assert not maybe_save_implicit_trust_after_reload(
        implicit_trust, cwd=cwd, settings=settings, terminal_ui=None, error_stream=error
    )
    assert not (config / "trust.json").exists()
    (cwd / ".pipy" / "skills").mkdir(parents=True)
    assert maybe_save_implicit_trust_after_reload(
        implicit_trust, cwd=cwd, settings=settings, terminal_ui=None, error_stream=error
    )
    assert ProjectTrustStore(config / "trust.json").get(cwd) is True
    assert implicit_trust.cwd is None


def test_reload_auto_persist_never_overrides_a_saved_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "project"
    (cwd / ".pipy" / "skills").mkdir(parents=True)
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    store = ProjectTrustStore(config / "trust.json")
    store.set(cwd.parent, False)
    implicit_trust = ImplicitTrustState(cwd=cwd)
    assert not maybe_save_implicit_trust_after_reload(
        implicit_trust,
        cwd=cwd,
        settings=SettingsManager.for_workspace(cwd, project_trusted=True),
        terminal_ui=None,
        error_stream=io.StringIO(),
    )
    assert store.get(cwd) is False
    assert implicit_trust.cwd is None


def test_untrusted_settings_never_open_project_and_refuse_writes(
    tmp_path: Path,
) -> None:
    global_path = tmp_path / "config" / "settings.json"
    project_path = tmp_path / "project" / ".pipy" / "settings.json"
    _write_json(global_path, {"theme": "global", "defaultProjectTrust": "always"})
    project_path.parent.mkdir(parents=True)
    project_path.write_text("{broken")
    manager = SettingsManager(
        global_path=global_path,
        project_path=project_path,
        project_trusted=False,
    )
    assert manager.get_theme() == "global"
    assert manager.get_default_project_trust() == "always"
    assert manager.raw_scope("project") == {}
    assert "project" not in manager.load_errors()
    before = project_path.read_bytes()
    with pytest.raises(RuntimeError, match="while untrusted"):
        manager.set_theme("changed", scope="project")
    assert project_path.read_bytes() == before
    manager.set_project_trusted(True)
    assert "project" in manager.load_errors()
    manager.set_project_trusted(False)
    assert "project" not in manager.load_errors()


def test_default_project_trust_is_global_only_and_invalid_maps_to_ask(
    tmp_path: Path,
) -> None:
    global_path = tmp_path / "config" / "settings.json"
    project_path = tmp_path / "project" / ".pipy" / "settings.json"
    _write_json(global_path, {"defaultProjectTrust": "invalid"})
    _write_json(project_path, {"defaultProjectTrust": "always"})
    manager = SettingsManager(global_path=global_path, project_path=project_path)
    assert manager.get_default_project_trust() == "ask"
    manager.set_default_project_trust("never")
    assert manager.get_default_project_trust() == "never"
    assert json.loads(global_path.read_text())["defaultProjectTrust"] == "never"
    assert json.loads(project_path.read_text())["defaultProjectTrust"] == "always"
    with pytest.raises(ValueError, match="ask, always, or never"):
        manager.set_default_project_trust("invalid")  # type: ignore[arg-type]


def test_untrusted_resource_provenance_keeps_global_package_and_cli(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    workspace.mkdir()
    _write_resource(workspace / ".pipy" / "skills" / "ws.md", "ws-skill")
    _write_resource(workspace / ".pipy" / "templates" / "ws.md", "ws-prompt")
    _write_resource(workspace / ".pipy" / "commands" / "ws.md", "ws-command")
    _write_resource(config / "skills" / "global.md", "global-skill")
    _write_resource(config / "templates" / "global.md", "global-prompt")
    _write_resource(config / "commands" / "global.md", "global-command")
    explicit_skill = tmp_path / "explicit-skill.md"
    explicit_prompt = tmp_path / "explicit-prompt.md"
    _write_resource(explicit_skill, "explicit-skill")
    _write_resource(explicit_prompt, "explicit-prompt")

    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={"PIPY_CONFIG_HOME": str(config)},
        explicit_skill_paths=(explicit_skill,),
        explicit_prompt_template_paths=(explicit_prompt,),
    )
    assert resources.skill_names() == ("explicit-skill", "global-skill")
    assert resources.template_names() == ("explicit-prompt", "global-prompt")
    assert resources.custom_command_slash_names() == ("/global-command",)

    (workspace / ".pipy" / "extensions").mkdir()
    (workspace / ".pipy" / "extensions" / "ws.py").write_text(
        "def activate(api): pass\n"
    )
    (config / "extensions").mkdir()
    (config / "extensions" / "global.py").write_text("def activate(api): pass\n")
    explicit_ext = tmp_path / "explicit.py"
    explicit_ext.write_text("def activate(api): pass\n")
    descriptors = discover_extensions(
        workspace,
        config_home_env={"PIPY_CONFIG_HOME": str(config)},
        explicit_paths=(explicit_ext,),
    )
    assert [item.name for item in descriptors] == ["explicit", "global"]

    explicit_only = WorkspaceResources.discover(
        workspace,
        config_home_env={"PIPY_CONFIG_HOME": str(config)},
        explicit_skill_paths=(explicit_skill,),
        include_skills_defaults=False,
        include_workspace_defaults=False,
    )
    assert explicit_only.skill_names() == ("explicit-skill",)


def test_untrusted_settings_remove_project_packages_but_keep_global_packages(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    global_package = tmp_path / "global-package"
    project_package = tmp_path / "project-package"
    workspace.mkdir()
    (global_package / "skills").mkdir(parents=True)
    (project_package / "skills").mkdir(parents=True)
    (global_package / "themes").mkdir()
    (project_package / "themes").mkdir()
    global_path = tmp_path / "config" / "settings.json"
    project_path = workspace / ".pipy" / "settings.json"
    _write_json(global_path, {"packages": [str(global_package)]})
    _write_json(project_path, {"packages": [str(project_package)]})
    manager = SettingsManager(
        global_path=global_path,
        project_path=project_path,
        project_trusted=False,
    )
    roots = compose_package_runtime(manager, workspace, install_theme_registry=False)
    assert [root.path for root in roots.skills] == [global_package / "skills"]
    assert [root.path for root in roots.themes] == [global_package / "themes"]


def test_untrusted_system_prompt_skips_project_but_keeps_global_and_explicit(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    (workspace / ".pipy").mkdir(parents=True)
    config.mkdir()
    (workspace / ".pipy" / "SYSTEM.md").write_text("PROJECT")
    (config / "SYSTEM.md").write_text("GLOBAL")
    result = resolve_system_prompt(
        "DEFAULT",
        cwd=workspace,
        config_home=config,
        include_project_defaults=False,
    )
    assert result.base_prompt == "GLOBAL"
    explicit = resolve_system_prompt(
        "DEFAULT",
        cwd=workspace,
        config_home=config,
        system_prompt_source="EXPLICIT",
        include_project_defaults=False,
    )
    assert explicit.base_prompt == "EXPLICIT"


def test_trust_cli_flags_are_available_through_top_level_and_last_wins() -> None:
    parser = build_parser()
    args = parser.parse_args(
        route_argv(["--approve", "--no-approve", "--approve"], KNOWN_SUBCOMMANDS)
    )
    assert args.command == "repl"
    assert args.trust_override is True
    args = parser.parse_args(route_argv(["-a", "-na"], KNOWN_SUBCOMMANDS))
    assert args.trust_override is False


def test_runtime_trust_selector_is_interactive_only_and_cancel_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".pipy" / "skills").mkdir(parents=True)
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))

    class Args:
        trust_override = None

    calls: list[Path] = []

    def cancel_selector(*, cwd: Path, options: object) -> None:
        calls.append(cwd)

    monkeypatch.setattr(
        "pipy_harness.native.tui.run_startup_project_trust_selector",
        cancel_selector,
    )
    assert not _resolve_runtime_project_trust(Args(), workspace)
    silent = capsys.readouterr()
    assert silent.out == ""
    assert silent.err == ""
    assert not _resolve_runtime_project_trust(Args(), workspace, interactive_tty=True)
    diagnostic = capsys.readouterr()
    assert diagnostic.out == ""
    assert diagnostic.err == ""
    assert calls == [workspace.resolve()]


def test_bool_runtime_trust_resolver_does_not_activate_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".pipy" / "skills").mkdir(parents=True)
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))

    class Args:
        trust_override = None

    def unexpected_activation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("bool trust resolver activated extensions")

    monkeypatch.setattr(
        "pipy_harness.cli._build_extension_activation_batch",
        unexpected_activation,
    )

    assert not _resolve_runtime_project_trust(Args(), workspace)


def test_extension_trust_decision_drives_and_closes_interactive_startup_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".pipy" / "skills").mkdir(parents=True)
    config = tmp_path / "config"
    extension = config / "extensions" / "trust.py"
    extension.parent.mkdir(parents=True)
    extension.write_text(
        "def activate(api):\n"
        "    @api.on('project_trust')\n"
        "    def decide(event, ctx):\n"
        "        assert ctx.has_ui is True and ctx.hasUI is True\n"
        "        assert ctx.ui.select('select', ['a', 'b']) == 'a'\n"
        "        assert ctx.ui.input('input', 'hint') == 'typed'\n"
        "        assert ctx.ui.confirm('confirm', 'message') is True\n"
        "        return {'trusted': 'yes'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    calls: list[tuple[object, ...]] = []

    class FakeTerminalUi:
        def __init__(
            self,
            *,
            input_stream: object,
            terminal_stream: object,
            cwd: Path,
        ) -> None:
            calls.append(("open", input_stream, terminal_stream, cwd))

        def run_extension_select(self, title: str, options: object) -> str:
            calls.append(("select", title, options))
            return "a"

        def run_extension_input(
            self, title: str, placeholder: str | None = None
        ) -> str:
            calls.append(("input", title, placeholder))
            return "typed"

        def run_extension_confirm(self, title: str, message: str) -> bool:
            calls.append(("confirm", title, message))
            return True

        def close(self) -> None:
            calls.append(("close",))

    monkeypatch.setattr("pipy_harness.native.tui.ToolLoopTerminalUi", FakeTerminalUi)

    class Args:
        trust_override = None

    resolution, batch = _resolve_runtime_project_trust_startup(
        Args(),
        workspace,
        interactive_tty=True,
        app_mode="interactive",
    )

    assert resolution.trusted is True
    assert resolution.source == "extension"
    assert batch is not None and batch.pending
    assert [call[0] for call in calls] == [
        "open",
        "select",
        "input",
        "confirm",
        "close",
    ]
    assert calls[0][3] == workspace.resolve()


def test_repl_resolves_trust_for_session_header_cwd_before_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell_cwd = tmp_path / "shell"
    session_cwd = tmp_path / "session"
    shell_cwd.mkdir()
    session_cwd.mkdir()
    observed: list[Path] = []

    class Header:
        cwd = str(session_cwd)

    class Session:
        def get_header(self) -> Header:
            return Header()

    monkeypatch.setattr(
        "pipy_harness.cli._resolve_native_startup_session", lambda _args: Session()
    )

    def stop_after_trust(_args: object, cwd: Path, **_kwargs: object) -> bool:
        observed.append(cwd)
        raise RuntimeError("stop after trust")

    monkeypatch.setattr(
        "pipy_harness.cli._resolve_runtime_project_trust_startup", stop_after_trust
    )
    with pytest.raises(RuntimeError, match="stop after trust"):
        main(["repl", "--cwd", str(shell_cwd), "--no-session"])
    assert observed == [session_cwd.resolve()]
