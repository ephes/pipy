"""Public opt-in composition and Pi default selection semantics."""

from types import SimpleNamespace

import pytest

from pipy_harness import cli
from pipy_harness.native.codemode import selftest
from pipy_harness.native.codemode.selftest import Availability
from pipy_harness.native.settings import deep_merge_settings
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tools.registry import production_tool_registry


def build(
    tmp_path,
    monkeypatch,
    defaults=None,
    options=None,
    extensions=None,
    registry=None,
    available=True,
):
    probes = []
    warnings = []

    def probe():
        probes.append(True)
        return Availability(available, "missing\nruntime")

    monkeypatch.setattr(selftest, "availability", probe)
    caps = NativeToolCapabilities(
        production_tool_registry() if registry is None else registry,
        extensions or {},
        workspace_root=tmp_path,
        filter_options=options or ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
        default_tools=lambda: defaults,
        diagnostic=warnings.append,
    )
    caps.publish(caps.prepare_extensions(extensions or {}))
    return caps, probes, warnings


def names(caps):
    return {d.name for d in caps.definitions()}


def test_disabled_never_probes(tmp_path, monkeypatch):
    caps, probes, warnings = build(tmp_path, monkeypatch)
    assert names(caps) == set(production_tool_registry())
    assert probes == warnings == []


@pytest.mark.parametrize("available", [True, False])
def test_selected_gated_before_advertisement(tmp_path, monkeypatch, available):
    caps, probes, warnings = build(
        tmp_path, monkeypatch, ["read", "codemode"], available=available
    )
    assert names(caps) == ({"read", "codemode"} if available else {"read"})
    assert len(probes) == 1
    assert caps.unknown_filter_names == ()
    assert len(warnings) == (0 if available else 1)
    assert all("\n" not in warning and len(warning) < 500 for warning in warnings)
    caps.publish(caps.prepare_extensions({}))
    assert len(probes) == 1


def test_custom_registry_owned_by_caller(tmp_path, monkeypatch):
    registry = dict(production_tool_registry())
    caps, probes, _ = build(tmp_path, monkeypatch, ["codemode"], registry=registry)
    assert names(caps) == set(registry)
    assert probes == []


def test_cli_modifiers_and_mixed_error():
    options = cli._tool_filter_options_from_args(
        SimpleNamespace(tools="+codemode,-read")
    )
    assert options.modifiers == ("+codemode", "-read")
    assert options.allow == ()
    with pytest.raises(ValueError, match="cannot be mixed"):
        cli._tool_filter_options_from_args(SimpleNamespace(tools="read,+codemode"))


def test_default_tools_merge():
    base = {"defaultTools": ["read"], "other": [1], "obj": {"a": 1}}
    assert deep_merge_settings(
        base, {"defaultTools": ["+codemode", "-read"], "other": [2], "obj": {"b": 2}}
    ) == {
        "defaultTools": ["read", "+codemode", "-read"],
        "other": [2],
        "obj": {"a": 1, "b": 2},
    }
    assert deep_merge_settings(base, {"defaultTools": []})["defaultTools"] == ["read"]
    assert deep_merge_settings({}, {"defaultTools": []})["defaultTools"] == []
    assert (
        deep_merge_settings({"defaultTools": 42}, {"defaultTools": []})["defaultTools"]
        == []
    )
    assert deep_merge_settings(base, {"defaultTools": ["write"]})["defaultTools"] == [
        "write"
    ]


@pytest.mark.parametrize(
    ("defaults", "options", "expected", "probe_count"),
    [
        ([], ToolFilterOptions(), set(), 0),
        (["read"], ToolFilterOptions(), {"read"}, 0),
        (["codemode"], ToolFilterOptions(allow=("write",)), {"write"}, 0),
        (
            None,
            ToolFilterOptions(modifiers=("+codemode", "-read")),
            set(production_tool_registry()) - {"read"} | {"codemode"},
            1,
        ),
        (
            None,
            ToolFilterOptions(modifiers=("+codemode", "-codemode")),
            set(production_tool_registry()),
            0,
        ),
        (["codemode"], ToolFilterOptions(no_tools=True), set(), 0),
        (["codemode"], ToolFilterOptions(no_builtin_tools=True), set(), 0),
        (["codemode"], ToolFilterOptions(exclude=("codemode",)), set(), 0),
    ],
)
def test_selection_boundaries(
    tmp_path, monkeypatch, defaults, options, expected, probe_count
):
    caps, probes, _ = build(tmp_path, monkeypatch, defaults, options)
    assert names(caps) == expected
    assert len(probes) == probe_count


def test_extension_codemode_keeps_ordinary_execution(tmp_path, monkeypatch):
    from test_native_tool_capabilities import _call, _RecordingTool

    extension = _RecordingTool("codemode")
    caps, probes, _ = build(
        tmp_path, monkeypatch, ["codemode"], extensions={"codemode": extension}
    )
    snapshot = caps.snapshot_for_projection(caps.state)
    assert names(caps) == {"codemode"}
    assert not snapshot.composite_enabled()
    assert probes == []
    snapshot.execute(_call("codemode"))
    assert len(extension.requests) == 1


def test_extension_defaults_remain_visible(tmp_path, monkeypatch):
    from test_native_tool_capabilities import _RecordingTool

    caps, _, _ = build(
        tmp_path, monkeypatch, [], extensions={"extra": _RecordingTool("extra")}
    )
    assert names(caps) == {"extra"}


def test_unknown_selected_names_still_fail(tmp_path, monkeypatch):
    caps, _, _ = build(tmp_path, monkeypatch, ["codemode", "typo"], available=False)
    assert caps.unknown_filter_names == ("typo",)


def test_reload_default_delta_preserves_live_selection_and_frozen_parent(
    tmp_path, monkeypatch
):
    selected = ["read", "codemode"]
    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    caps = NativeToolCapabilities(
        production_tool_registry(),
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
        default_tools=lambda: selected,
    )
    caps.publish(caps.prepare_extensions({}))
    frozen = caps.snapshot_for_projection(caps.state)
    definition = frozen.definitions()
    assert frozen.composite_enabled()
    selected[:] = ["write"]
    candidate = caps.prepare_extensions({})
    assert caps.set_active_tools(["codemode"])
    caps.publish(candidate)
    assert names(caps) == {"codemode", "write"}
    assert frozen.definitions() == definition
    assert frozen.composite_enabled()
    assert caps.set_active_tools(["write"])
    caps.publish(caps.prepare_extensions({}))
    assert names(caps) == {"write"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ()),
        (42, ()),
        ({"bad": True}, ()),
        ([42, "write"], ("write",)),
        ([], ()),
        (
            ["+codemode", "-read"],
            tuple(name for name in production_tool_registry() if name != "read")
            + ("codemode",),
        ),
    ],
)
def test_settings_typed_default_tools(tmp_path, value, expected):
    import json

    from pipy_harness.native.settings import SettingsManager

    global_path = tmp_path / "settings.json"
    global_path.write_text(json.dumps({"defaultTools": value, "unknown": {"a": 1}}))
    manager = SettingsManager(
        global_path=global_path, project_path=tmp_path / "project.json"
    )
    assert manager.get_default_tools() == expected
    assert manager.raw_scope("global")["defaultTools"] == value
    assert manager.raw_scope("global")["unknown"] == {"a": 1}


def test_settings_layers_and_overrides(tmp_path):
    import json

    from pipy_harness.native.settings import SettingsManager

    global_path = tmp_path / "global.json"
    project_path = tmp_path / "project.json"
    global_path.write_text(
        json.dumps({"defaultTools": ["read"], "enabledModels": ["a"]})
    )
    project_path.write_text(
        json.dumps({"defaultTools": ["+write"], "enabledModels": ["b"]})
    )
    manager = SettingsManager(
        global_path=global_path,
        project_path=project_path,
        overrides={"defaultTools": ["-read", "+codemode"]},
    )
    assert manager.get_default_tools() == ("write", "codemode")
    assert manager.effective()["enabledModels"] == ["b"]
    empty_override = SettingsManager(
        global_path=global_path,
        project_path=project_path,
        overrides={"defaultTools": []},
    )
    assert empty_override.get_default_tools() == ("read", "write")
    project_path.write_text(json.dumps({"defaultTools": []}))
    empty_project = SettingsManager(
        global_path=global_path,
        project_path=project_path,
    )
    assert empty_project.get_default_tools() == ("read",)


@pytest.mark.parametrize("available", [True, False])
def test_library_session_advertisement_and_prompt(tmp_path, monkeypatch, available):
    import io

    from test_native_coding_session import _UsageScriptProvider

    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.settings import SettingsManager
    from pipy_harness.native.system_prompt_sections import SystemPromptTemplate

    monkeypatch.setattr(
        selftest, "availability", lambda: Availability(available, "missing extra")
    )
    manager = SettingsManager(
        global_path=tmp_path / "global.json",
        project_path=tmp_path / "project.json",
        overrides={"defaultTools": ["+codemode"]},
    )
    provider = _UsageScriptProvider(((None, ()),))
    errors = io.StringIO()
    session = CodingSession(
        provider=provider,
        settings_manager=manager,
        system_prompt_sections=SystemPromptTemplate(cwd=str(tmp_path)),
    )
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    request = provider.requests[0]
    assert ("codemode" in {tool.name for tool in request.available_tools}) == available
    prompt = str(request.system_prompt)
    assert ("Run Python that calls other tools" in prompt) == available
    assert ("Use codemode to chain" in prompt) == available
    assert errors.getvalue().count("codemode unavailable:") == (0 if available else 1)


def test_public_session_write_then_raise_retains_effect(tmp_path, monkeypatch):
    import io
    import json

    from codemode_driver import real_runtime_skip_reason
    from test_native_coding_session import (
        _CollectingAgentEventSink,
        _make_call,
        _UsageScriptProvider,
    )

    from pipy_harness.native.agent import ToolCallCompleted
    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.settings import SettingsManager

    reason = real_runtime_skip_reason()
    if reason:
        pytest.skip(reason)
    # Use the real installed runtime and self-test, never install from a test.
    from codemode_driver import REAL_RUNTIME

    from pipy_harness.native.codemode.runtime import CodemodePaths
    from pipy_harness.native.coding import codemode_runner

    paths = CodemodePaths(REAL_RUNTIME.parent.parent)
    original_probe = selftest.availability
    monkeypatch.setattr(selftest, "availability", lambda: original_probe(paths))
    original_runner = codemode_runner.CodemodeCompositeRunner.__init__

    def init(self, **kwargs):
        original_runner(self, paths=paths, **kwargs)

    monkeypatch.setattr(codemode_runner.CodemodeCompositeRunner, "__init__", init)
    code = 'tools.write({"path": "effect.txt", "content": "retained"})\nraise RuntimeError("C")'
    provider = _UsageScriptProvider(
        ((None, (_make_call("codemode", json.dumps({"code": code})),)), (None, ()))
    )
    sink = _CollectingAgentEventSink()
    manager = SettingsManager(
        global_path=tmp_path / "global.json",
        project_path=tmp_path / "project.json",
        overrides={"defaultTools": ["+codemode"]},
    )
    session = CodingSession(
        provider=provider, settings_manager=manager, agent_event_sink=sink
    )
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("run\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert (tmp_path / "effect.txt").read_text() == "retained"
    parent = next(
        event for event in sink.events if isinstance(event, ToolCallCompleted)
    )
    assert parent.result.is_error
    assert "write (ok)" in parent.result.content.value
    assert "not undone" in parent.result.content.value
    assert parent.result.details["nestedCalls"]["calls"][0]["status"] == "ok"


@pytest.mark.parametrize("mode", ["print", "json", "rpc"])
@pytest.mark.parametrize("selection", ["settings", "cli"])
@pytest.mark.parametrize("available", [True, False])
def test_cli_product_modes_use_composed_definition(
    tmp_path, monkeypatch, capfd, mode, selection, available
):
    import io
    import json
    import sys

    from pipy_harness.native.fake import AutomationFakeProvider

    config = tmp_path / "config"
    config.mkdir()
    if selection == "settings":
        (config / "settings.json").write_text(
            json.dumps({"defaultTools": ["+codemode"]})
        )
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "defaults.json"))
    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("PIPY_OFFLINE", "1")
    monkeypatch.setattr(
        sys, "stdin", io.StringIO('{"type":"prompt","message":"hello"}\n')
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    probes = []

    def probe():
        probes.append(True)
        return Availability(available, "missing runtime")

    monkeypatch.setattr(selftest, "availability", probe)
    requests = []
    original = AutomationFakeProvider.complete

    def complete(self, request, **kwargs):
        requests.append(request)
        return original(self, request, **kwargs)

    monkeypatch.setattr(AutomationFakeProvider, "complete", complete)
    if mode == "rpc":
        import threading

        completed = threading.Event()

        class PromptInput(io.StringIO):
            def readline(self, *args):
                line = super().readline(*args)
                if not line:
                    assert completed.wait(5), "RPC provider turn did not complete"
                return line

        def rpc_complete(self, request, **kwargs):
            result = complete(self, request, **kwargs)
            completed.set()
            return result

        monkeypatch.setattr(AutomationFakeProvider, "complete", rpc_complete)
        monkeypatch.setattr(
            sys, "stdin", PromptInput('{"type":"prompt","message":"hello"}\n')
        )
    args = [
        "repl",
        "--cwd",
        str(tmp_path),
        "--native-provider",
        "fake",
        "--native-model",
        "fake-tools",
        "--no-session",
        "--offline",
    ]
    if selection == "cli":
        args += ["--tools", "+codemode"]
    args += (
        ["--print", "hello"]
        if mode == "print"
        else ["--mode", mode] + ([] if mode == "rpc" else ["hello"])
    )
    assert cli.main(args) == 0
    assert requests
    assert (
        "codemode" in {tool.name for tool in requests[0].available_tools}
    ) == available
    assert (
        "Run Python that calls other tools" in requests[0].system_prompt
    ) == available
    assert len(probes) == 1
    assert capfd.readouterr().err.count("codemode unavailable:") == (
        0 if available else 1
    )


@pytest.mark.parametrize("failure", ["extra", "runtime", "selftest"])
def test_actual_availability_failures_omit_builtin_without_install(
    tmp_path, monkeypatch, failure
):
    from pipy_harness.native.codemode import runtime

    def never_install(*args, **kwargs):
        pytest.fail("startup must never install a runtime")

    monkeypatch.setattr(runtime, "install_runtime", never_install)
    original = selftest.availability
    if failure == "selftest":
        monkeypatch.setattr(selftest, "verify_runtime", lambda *args: tmp_path)
        monkeypatch.setattr(selftest, "_run_selftest", lambda *args: (1, {}))

    def probe():
        return original(
            runtime.CodemodePaths(tmp_path),
            find_spec=lambda name: None if failure == "extra" else object(),
        )

    monkeypatch.setattr(selftest, "availability", probe)
    warnings = []
    caps = NativeToolCapabilities(
        production_tool_registry(),
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions(modifiers=("+codemode",)),
        cancel_join_timeout_seconds=0.1,
        diagnostic=warnings.append,
    )
    caps.publish(caps.prepare_extensions({}))
    assert names(caps) == set(production_tool_registry())
    assert caps.unknown_filter_names == ()
    assert len(warnings) == 1 and "\n" not in warnings[0]


def test_reload_new_defaults_does_not_remove_implicit_defaults(tmp_path, monkeypatch):
    selected = [None]
    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    caps = NativeToolCapabilities(
        production_tool_registry(),
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
        default_tools=lambda: selected[0],
    )
    caps.publish(caps.prepare_extensions({}))
    selected[0] = ("codemode",)
    caps.publish(caps.prepare_extensions({}))
    assert names(caps) == set(production_tool_registry()) | {"codemode"}


@pytest.mark.parametrize("flag", ["--tools", "-t"])
def test_cli_consumes_minus_modifier_value(flag):
    argv = cli._bind_tool_modifier_values(["repl", flag, "-read", "--print", "hello"])
    args = cli.build_parser().parse_args(argv)
    assert cli._tool_filter_options_from_args(args).modifiers == ("-read",)
    assert cli._bind_tool_modifier_values(["run", "--", flag, "-read"]) == [
        "run",
        "--",
        flag,
        "-read",
    ]


def test_new_extensions_visible_with_defaults_but_custom_selection_preserved(
    tmp_path, monkeypatch
):
    from test_native_tool_capabilities import _RecordingTool

    caps, _, _ = build(tmp_path, monkeypatch, ["read"])
    caps.publish(caps.prepare_extensions({"extra": _RecordingTool("extra")}))
    assert names(caps) == {"read", "extra"}
    assert caps.set_active_tools(["read"])
    candidate = caps.prepare_extensions(
        {"extra": _RecordingTool("extra"), "later": _RecordingTool("later")}
    )
    caps.publish(candidate)
    assert names(caps) == {"read"}


def test_library_reload_adds_setting_at_published_generation(tmp_path, monkeypatch):
    import io
    import json

    from test_native_coding_session import _UsageScriptProvider

    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({"defaultTools": ["read"]}))
    manager = SettingsManager(
        global_path=global_path, project_path=tmp_path / "project.json"
    )

    class Provider(_UsageScriptProvider):
        def complete(self, request, **kwargs):
            result = super().complete(request, **kwargs)
            global_path.write_text(json.dumps({"defaultTools": ["codemode"]}))
            return result

    provider = Provider(((None, ()), (None, ())))
    session = CodingSession(provider=provider, settings_manager=manager)
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("one\n/reload\ntwo\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert len(provider.requests) == 2
    assert {tool.name for tool in provider.requests[0].available_tools} == {"read"}
    assert {tool.name for tool in provider.requests[1].available_tools} == {
        "read",
        "codemode",
    }


def test_adapter_custom_registry_is_not_widened(tmp_path, monkeypatch):
    import io

    from test_native_coding_session import _UsageScriptProvider

    from pipy_harness.adapters.native import CodingSessionAdapter
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.setattr(
        selftest, "availability", lambda: pytest.fail("custom registry must not probe")
    )
    registry = {"read": production_tool_registry()["read"]}
    manager = SettingsManager(
        global_path=tmp_path / "global.json",
        project_path=tmp_path / "project.json",
        overrides={"defaultTools": ["+codemode"]},
    )
    provider = _UsageScriptProvider(((None, ()),))
    adapter = CodingSessionAdapter(
        provider=provider,
        tool_registry=registry,
        settings_manager=manager,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    context = adapter.prepare_session_context(tmp_path)
    session = adapter.build_session(context)
    session.run(
        workspace_root=tmp_path,
        input_stream=adapter.input_stream,
        output_stream=adapter.output_stream,
        error_stream=adapter.error_stream,
    )
    assert {tool.name for tool in provider.requests[0].available_tools} == {"read"}


def test_extension_named_codemode_has_no_builtin_prompt_contribution(
    tmp_path, monkeypatch
):
    from test_native_tool_capabilities import _RecordingTool

    from pipy_harness.native.repl.wiring import _system_sections_source
    from pipy_harness.native.system_prompt_sections import (
        SystemPromptTemplate,
        render_system_prompt,
    )

    caps, probes, _ = build(
        tmp_path, monkeypatch, extensions={"codemode": _RecordingTool("codemode")}
    )
    sections = _system_sections_source(
        source=SystemPromptTemplate(cwd=str(tmp_path)),
        fixed=(),
        tool_capabilities=caps,
        ctl=SimpleNamespace(workspace_resources=SimpleNamespace(skills=())),
    )()
    prompt = render_system_prompt(sections)
    assert "Run Python that calls other tools" not in prompt
    assert "Use codemode to chain" not in prompt
    assert probes == []


def test_malformed_default_tools_layer_replaces_without_object_merge():
    assert deep_merge_settings(
        {"defaultTools": {"old": True}}, {"defaultTools": {"new": True}}
    )["defaultTools"] == {"new": True}


@pytest.mark.parametrize("available", [True, False])
def test_concurrent_probe_keeps_state_and_presentation_unlocked(
    tmp_path, monkeypatch, available
):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    probes = []
    warnings = []
    selected = ["read"]
    lock = threading.RLock()

    def probe():
        probes.append(True)
        entered.set()
        assert release.wait(5)
        return Availability(available, "missing\nruntime")

    def diagnostic(message):
        # A separate thread must be able to acquire the shared mutex while the
        # callback is running; a same-thread RLock acquisition would prove nothing.
        with ThreadPoolExecutor(max_workers=1) as callback_pool:
            callback_pool.submit(lambda: caps.state).result(timeout=2)
        # Reentrant preparation also proves presentation owns no probe mutex.
        caps.prepare_extensions({})
        warnings.append(message)

    caps = NativeToolCapabilities(
        production_tool_registry(),
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
        state_lock=lock,
        default_tools=lambda: selected,
        diagnostic=diagnostic,
    )
    caps.publish(caps.prepare_extensions({}))
    selected[:] = ["read", "codemode"]
    monkeypatch.setattr(selftest, "availability", probe)

    def second_prepare():
        second_started.set()
        return caps.prepare_extensions({})

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(caps.prepare_extensions, {})
        try:
            assert entered.wait(2)
            second = pool.submit(second_prepare)
            assert second_started.wait(2)

            def select():
                assert "codemode" not in caps.state.registry
                return caps.set_active_tools(["write"])

            assert pool.submit(select).result(timeout=2)
        finally:
            release.set()
        candidate = first.result(timeout=3)
        other = second.result(timeout=3)
    assert probes == [True]
    assert len(warnings) == (0 if available else 1)
    assert ("codemode" in candidate.registry) == available
    assert ("codemode" in other.registry) == available
    caps.publish(candidate)
    # Newly added defaults are appended, while the accepted live selection is kept.
    assert names(caps) == ({"write", "codemode"} if available else {"write"})
    caps.publish(other)
    assert len(warnings) == (0 if available else 1)


@pytest.mark.parametrize("with_ui", [False, True])
def test_reload_newly_unavailable_codemode_routes_one_notice(
    tmp_path, monkeypatch, with_ui
):
    import io
    import json

    from test_native_coding_session import _UsageScriptProvider

    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.settings import SettingsManager
    from pipy_harness.native.tui import TerminalUi

    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({"defaultTools": ["read"]}))
    manager = SettingsManager(
        global_path=global_path, project_path=tmp_path / "project.json"
    )
    probes = []

    def probe():
        probes.append(True)
        return Availability(False, "missing\nruntime")

    monkeypatch.setattr(selftest, "availability", probe)

    class Provider(_UsageScriptProvider):
        def complete(self, request, **kwargs):
            result = super().complete(request, **kwargs)
            global_path.write_text(json.dumps({"defaultTools": ["read", "codemode"]}))
            return result

    notices = []
    if with_ui:
        ui = TerminalUi(
            input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
        )
        original_notice = type(ui.components.transcript).add_notice

        def notice(self, message):
            notices.append(message)
            original_notice(self, message)

        monkeypatch.setattr(type(ui.components.transcript), "add_notice", notice)
        monkeypatch.setattr(
            CodingSession, "_build_terminal_ui", lambda self, **kwargs: ui
        )

        def wait_for_turn(self, done_event, abort_event, **kwargs):
            assert done_event.wait(5)
            return "settled"

        monkeypatch.setattr(TerminalUi, "wait_for_active_turn_interrupt", wait_for_turn)
        lines = iter(("one", "/reload", "/reload", "two", "/exit"))
        monkeypatch.setattr(
            TerminalUi, "read_line", lambda self, *args, **kwargs: next(lines)
        )
    provider = Provider(((None, ()), (None, ())))
    errors = io.StringIO()
    CodingSession(provider=provider, settings_manager=manager).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("one\n/reload\n/reload\ntwo\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    assert len(provider.requests) == 2
    assert all(
        {tool.name for tool in request.available_tools} == {"read"}
        for request in provider.requests
    )
    assert probes == [True]
    assert errors.getvalue().count("codemode unavailable:") == (0 if with_ui else 1)
    assert sum("codemode unavailable:" in notice for notice in notices) == (
        1 if with_ui else 0
    )
