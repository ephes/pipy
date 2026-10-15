"""Activating ``codemode`` and ``tool_search`` for MCP servers (MCP parity
plan S19, rows SES-11 and SES-13), and the MCP switches (EXP-11, EXP-12).

``McpDiscovery`` is tested against a real capability value over a production
registry with a stubbed runtime probe. The session level is in
``test_native_mcp_session.py``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from test_native_tool_filter_patterns import _Tool
from test_native_tool_search_tool import _capabilities, _declared

from pipy_harness.cli import _resource_options_from_args, build_parser, main
from pipy_harness.native.codemode import selftest
from pipy_harness.native.mcp.discovery import McpDiscovery, unreachable_warning
from pipy_harness.native.resource_enablement import is_builtin_extension_enabled
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
)

_WARNING = (
    "MCP tools are only reachable from the codemode or tool_search tool, "
    "but neither is active; they cannot be called."
)
_WARNING_AUTO = (
    "MCP tools are only reachable from the codemode or tool_search tool, "
    "but neither is active (autoEnableCodemode is false); they cannot be called."
)


class _Probe:
    """Stands in for the codemode runtime probe and counts its runs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, available: bool) -> None:
        self.runs = 0
        self._available = available
        monkeypatch.setattr(selftest, "availability", self)

    def __call__(self) -> selftest.Availability:
        self.runs += 1
        return selftest.Availability(
            available=self._available,
            reason=None if self._available else "no runtime here",
        )


class _Discovery:
    def __init__(
        self,
        tools: NativeToolCapabilities,
        *exposures: str,
        auto_enable_codemode: bool = True,
    ) -> None:
        self.notices: list[tuple[str, str]] = []
        self.exposures = set(exposures)
        self.discovery = McpDiscovery(
            configured_exposures=lambda: frozenset(self.exposures),
            auto_enable_codemode=auto_enable_codemode,
            tools=tools,
            notify=lambda level, text: self.notices.append((level, text)),
        )

    def ensure(self) -> list[tuple[str, str]]:
        self.discovery.ensure_active()
        return self.notices


def test_the_warning_texts_are_pis() -> None:
    assert unreachable_warning(auto_enable_codemode_is_cause=False) == _WARNING
    assert unreachable_warning(auto_enable_codemode_is_cause=True) == _WARNING_AUTO


# -- SES-11: activation from the configuration --------------------------------


def test_codemode_exposure_registers_and_activates_codemode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path)
    assert "codemode" not in tools.registered_names
    generation = tools.state.extension_generation

    assert _Discovery(tools, "codemode").ensure() == []

    assert "codemode" in _declared(tools)
    assert "tool_search" not in _declared(tools)
    assert probe.runs == 1
    # The value keeps its extension generation and counts as not customized,
    # so a provider turn binds to it and a reload still activates new tools.
    assert tools.state.extension_generation is generation
    assert not tools.state.active_selection_customized


def test_deferred_exposure_activates_tool_search_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path)
    before = _declared(tools)

    assert _Discovery(tools, "deferred", "direct").ensure() == []

    assert _declared(tools) == (*before, "tool_search")
    assert "codemode" not in tools.registered_names
    assert probe.runs == 0


def test_both_exposures_activate_both_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path)

    assert _Discovery(tools, "codemode", "deferred").ensure() == []

    assert {"codemode", "tool_search"} <= set(_declared(tools))


@pytest.mark.parametrize("exposures", [(), ("direct",), ("direct", "hidden")])
def test_servers_without_undeclared_tools_change_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exposures: tuple[str, ...]
) -> None:
    probe = _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path)
    state = tools.state

    assert _Discovery(tools, *exposures).ensure() == []

    assert tools.state is state
    assert probe.runs == 0


def test_activation_adds_to_a_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path, default_tools=("read", "bash"))

    assert _Discovery(tools, "codemode", "deferred").ensure() == []

    assert set(_declared(tools)) == {"read", "bash", "codemode", "tool_search"}


def test_activation_with_an_exclusion_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path, options=ToolFilterOptions(exclude=("bash",)))

    assert _Discovery(tools, "codemode").ensure() == []

    declared = _declared(tools)
    assert "codemode" in declared and "bash" not in declared


def test_a_second_call_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path)
    discovery = _Discovery(tools, "codemode", "deferred")
    discovery.ensure()
    state = tools.state

    assert discovery.ensure() == []

    assert tools.state is state
    assert probe.runs == 1


def test_a_reload_after_activation_still_activates_new_extension_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path)
    _Discovery(tools, "codemode", "deferred").ensure()

    tools.publish(tools.prepare_extensions({"new": _Tool("new", "direct", "New.")}))

    declared = set(_declared(tools))
    assert {"new", "codemode", "tool_search", "read"} <= declared


# -- SES-13: the warning ------------------------------------------------------


def test_does_not_activate_codemode_when_auto_enable_codemode_is_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pi: "does not activate codemode when autoEnableCodemode is false".
    probe = _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path)
    before = _declared(tools)

    notices = _Discovery(tools, "codemode", auto_enable_codemode=False).ensure()

    assert notices == [("warning", _WARNING_AUTO)]
    assert _declared(tools) == before
    # pipy does not probe the runtime for a tool it will not activate.
    assert probe.runs == 0 and "codemode" not in tools.registered_names


def test_codemode_tools_count_as_reachable_through_an_active_tool_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pi: "treats codemode MCP tools as reachable through an active
    # tool_search".
    _Probe(monkeypatch, available=True)
    tools = _capabilities(
        tmp_path, options=ToolFilterOptions(modifiers=("+tool_search",))
    )

    notices = _Discovery(tools, "codemode", auto_enable_codemode=False).ensure()

    assert notices == []
    assert "tool_search" in _declared(tools) and "codemode" not in _declared(tools)


def test_deferred_tools_count_as_reachable_through_an_active_codemode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pi: "reaches deferred MCP tools through codemode when tool_search is
    # not available".
    _Probe(monkeypatch, available=True)
    tools = _capabilities(
        tmp_path,
        options=ToolFilterOptions(exclude=("tool_search",), modifiers=("+codemode",)),
    )
    assert "codemode" in _declared(tools)

    assert _Discovery(tools, "deferred").ensure() == []

    assert "tool_search" not in _declared(tools)


def test_warns_when_deferred_tools_have_neither_tool_search_nor_codemode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pi: "warns when deferred MCP tools have neither tool_search nor
    # codemode".
    probe = _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path, options=ToolFilterOptions(exclude=("tool_search",)))

    notices = _Discovery(tools, "deferred").ensure()

    assert notices == [("warning", _WARNING)]
    # Pi does not turn on codemode for `deferred` tools.
    assert probe.runs == 0 and "codemode" not in tools.registered_names


def test_warns_when_the_codemode_runtime_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Probe(monkeypatch, available=False)
    diagnostics: list[str] = []
    tools = _capabilities(tmp_path)
    monkeypatch.setattr(tools, "_diagnostic", diagnostics.append)

    notices = _Discovery(tools, "codemode").ensure()

    assert notices == [("warning", _WARNING)]
    assert diagnostics == ["pipy: codemode unavailable: no runtime here"]
    assert "codemode" not in tools.registered_names


@pytest.mark.parametrize(
    "options",
    [
        ToolFilterOptions(allow=("read",)),
        ToolFilterOptions(exclude=("codemode", "tool_search")),
        ToolFilterOptions(no_tools=True),
    ],
)
def test_warns_when_the_tool_filters_remove_both_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: ToolFilterOptions
) -> None:
    probe = _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path, options=options)

    notices = _Discovery(tools, "codemode", "deferred").ensure()

    assert notices == [("warning", _WARNING)]
    assert probe.runs == 0
    assert not {"codemode", "tool_search"} & set(_declared(tools))


def test_auto_enable_is_not_named_when_the_filters_remove_codemode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Probe(monkeypatch, available=True)
    tools = _capabilities(
        tmp_path, options=ToolFilterOptions(exclude=("codemode", "tool_search"))
    )

    notices = _Discovery(tools, "codemode", auto_enable_codemode=False).ensure()

    assert notices == [("warning", _WARNING)]


def test_warns_once_in_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path)
    discovery = _Discovery(tools, "codemode", auto_enable_codemode=False)

    discovery.ensure()
    discovery.exposures.add("deferred")  # activates tool_search, as Pi does
    discovery.ensure()
    assert tools.set_active_tools(["read"])
    discovery.exposures.discard("deferred")

    assert discovery.ensure() == [("warning", _WARNING_AUTO)]


class _PlainCodemode:
    """An extension's tool named ``codemode`` that reaches no other tools."""

    definition = ToolDefinition(
        name="codemode",
        description="Not the built-in.",
        input_schema={"type": "object"},
        exposure="codemode",
        schema_mode="foreign",
    )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id, output_text=""
        )


def test_another_tool_named_codemode_is_never_activated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _Probe(monkeypatch, available=True)
    extensions: Sequence[ToolPort] = (_PlainCodemode(),)
    tools = _capabilities(tmp_path, extensions)

    notices = _Discovery(tools, "codemode").ensure()

    assert notices == [("warning", _WARNING)]
    assert "codemode" not in _declared(tools)
    assert probe.runs == 0


class _HiddenToolSearch:
    """An extension's ``tool_search`` that declares a reach but is hidden."""

    definition = ToolDefinition(
        name="tool_search",
        description="Replaced and hidden.",
        input_schema={"type": "object"},
        exposure="hidden",
        schema_mode="foreign",
        reaches_tools=lambda arguments: "all",
    )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id, output_text=""
        )


def test_a_hidden_reaching_tool_does_not_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path, (_HiddenToolSearch(),))
    state = tools.state

    notices = _Discovery(tools, "deferred").ensure()

    assert notices == [("warning", _WARNING)]
    assert tools.state is state


# -- the capability methods ---------------------------------------------------


def test_register_codemode_is_refused_without_production_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _Probe(monkeypatch, available=True)
    tools = NativeToolCapabilities(
        {"read": _Tool("read")},
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
    )

    assert not tools.register_codemode()
    assert not tools.codemode_selectable()
    assert probe.runs == 0


def test_registered_codemode_survives_a_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Probe(monkeypatch, available=True)
    tools = _capabilities(tmp_path)
    assert tools.register_codemode()
    port = tools.state.registry["codemode"]

    tools.publish(tools.prepare_extensions({}))

    assert tools.state.registry["codemode"] is port
    assert "codemode" in _declared(tools)


def test_activate_tools_ignores_unknown_names_and_keeps_an_unset_selection(
    tmp_path: Path,
) -> None:
    tools = _capabilities(tmp_path)
    state = tools.state
    assert state.active_tool_names is None

    tools.activate_tools(["read", "nope"])

    assert tools.state is state


# -- EXP-11, EXP-12: the switches ---------------------------------------------


@pytest.mark.parametrize(
    ("user", "project", "enabled"),
    [
        ([], [], True),
        (["-builtin:mcp"], [], False),
        (["-builtin:mcp"], ["+builtin:mcp"], True),
        ([], ["-builtin:mcp"], False),
        (["-builtin:mcp"], ["-other", "ext.py"], False),
        (["-mcp", "builtin:mcp", 3], [], True),
        # Goal check r3 F1, Pi isEnabledByOverrides: `!` is a glob that
        # turns it off, `+` and `-` name it exactly; `-` beats `+` beats `!`
        # in the user array, whatever the order.
        (["!builtin:mcp"], [], False),
        (["!builtin:*"], [], False),
        (["!builtin:m?p"], [], False),
        (["!builtin:codemode"], [], True),
        (["-builtin:*"], [], True),
        (["+builtin:*", "!builtin:mcp"], [], False),
        (["!builtin:*", "+builtin:mcp"], [], True),
        (["+builtin:mcp", "!builtin:*"], [], True),
        (["-builtin:mcp", "+builtin:mcp"], [], False),
        (["+./builtin:mcp", "!builtin:*"], [], True),
        # Pi applyAutoloadDisabledPatterns: a matching project `!`, `+` or
        # `-` entry decides over the user array, the last one winning.
        ([], ["!builtin:*"], False),
        (["!builtin:*"], ["+builtin:mcp"], True),
        ([], ["+builtin:mcp", "!builtin:*"], False),
        ([], ["!builtin:*", "+builtin:mcp"], True),
        (["-builtin:mcp"], ["-builtin:*"], False),
        (["!builtin:mcp"], ["!builtin:codemode"], False),
    ],
)
def test_builtin_extension_enablement(
    user: list[object], project: list[object], enabled: bool
) -> None:
    assert (
        is_builtin_extension_enabled(
            "mcp", user_patterns=user, project_patterns=project
        )
        is enabled
    )


def test_settings_read_the_builtin_entry_per_scope(tmp_path: Path) -> None:
    global_path = tmp_path / "config" / "settings.json"
    project_path = tmp_path / "work" / ".pipy" / "settings.json"
    global_path.parent.mkdir(parents=True)
    project_path.parent.mkdir(parents=True)
    global_path.write_text('{"extensions": ["-builtin:mcp"]}')

    def enabled(*, trusted: bool) -> bool:
        manager = SettingsManager(
            global_path=global_path,
            project_path=project_path,
            project_trusted=trusted,
        )
        return manager.builtin_extension_enabled("mcp")

    assert not enabled(trusted=True)
    project_path.write_text('{"extensions": ["+builtin:mcp"]}')
    assert enabled(trusted=True)
    # An untrusted project's settings are not read.
    assert not enabled(trusted=False)
    # Pi's `!` exclusions, user-level and in the project.
    global_path.write_text('{"extensions": ["!builtin:mcp"]}')
    project_path.write_text('{"extensions": []}')
    assert not enabled(trusted=True)
    global_path.write_text('{"extensions": []}')
    project_path.write_text('{"extensions": ["!builtin:*"]}')
    assert not enabled(trusted=True)
    assert enabled(trusted=False)


@pytest.mark.parametrize(
    ("options", "configured", "enabled"),
    [
        (RuntimeResourceOptions(), True, True),
        (RuntimeResourceOptions(), False, False),
        (RuntimeResourceOptions(no_mcp=True), True, True),
        (RuntimeResourceOptions(no_extensions=True), True, False),
        (RuntimeResourceOptions(builtin_extensions=("codemode",)), False, True),
        (
            RuntimeResourceOptions(no_extensions=True, builtin_extensions=("mcp",)),
            True,
            False,
        ),
    ],
)
def test_run_options_decide_whether_codemode_is_loaded(
    options: RuntimeResourceOptions, configured: bool, enabled: bool
) -> None:
    # `--no-mcp` concerns MCP alone; the other flags apply to every built-in.
    assert options.builtin_extension_enabled("codemode", configured=configured) is (
        enabled
    )


@pytest.mark.parametrize(
    ("options", "configured", "enabled"),
    [
        (RuntimeResourceOptions(), True, True),
        (RuntimeResourceOptions(), False, False),
        (RuntimeResourceOptions(no_mcp=True), True, False),
        (RuntimeResourceOptions(no_extensions=True), True, False),
        (RuntimeResourceOptions(builtin_extensions=("mcp",)), False, True),
        (
            RuntimeResourceOptions(no_extensions=True, builtin_extensions=("mcp",)),
            True,
            True,
        ),
        (RuntimeResourceOptions(no_mcp=True, builtin_extensions=("mcp",)), True, False),
    ],
)
def test_run_options_decide_whether_mcp_is_on(
    options: RuntimeResourceOptions, configured: bool, enabled: bool
) -> None:
    assert options.mcp_enabled(configured=configured) is enabled


def test_cli_flags_parse_to_run_options(tmp_path: Path) -> None:
    parser = build_parser()

    args = parser.parse_args(
        ["repl", "--cwd", str(tmp_path), "--no-mcp", "-ne", "-e", "ext.py"]
        + ["-e", "builtin:mcp", "-e", "builtin:mcp", "-e", "builtin:tool-search"]
        + ["-e", "builtin:codemode"]
    )
    options = _resource_options_from_args(args)

    assert options.no_mcp and options.no_extensions
    assert options.builtin_extensions == ("mcp", "tool-search", "codemode")
    assert options.extension_paths == (tmp_path.resolve() / "ext.py",)
    plain = _resource_options_from_args(
        parser.parse_args(["repl", "--cwd", str(tmp_path)])
    )
    assert not plain.no_mcp and plain.builtin_extensions == ()


def test_an_unknown_builtin_extension_is_an_error(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        ["repl", "--cwd", str(tmp_path), "-e", "builtin:nope"]
    )

    with pytest.raises(ValueError, match="Unknown built-in extension: builtin:nope"):
        _resource_options_from_args(args)


# -- EXP-12: the config command -----------------------------------------------


def _builtin_report(capfd: pytest.CaptureFixture[str], workspace: Path) -> object:
    capfd.readouterr()
    assert main(["config", "list", "--json", "--approve", "--cwd", str(workspace)]) == 0
    return json.loads(capfd.readouterr().out)["builtin"]


def _builtins(*, mcp: bool = True, tool_search: bool = True) -> list[dict[str, object]]:
    return [
        {"name": "builtin:codemode", "enabled": True},
        {"name": "builtin:tool-search", "enabled": tool_search},
        {"name": "builtin:mcp", "enabled": mcp},
    ]


def test_config_command_lists_and_toggles_the_builtin_mcp_entry(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "cfg"
    config_home.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    workspace = tmp_path / "ws"
    (workspace / ".pipy").mkdir(parents=True)
    user_file = config_home / "settings.json"
    user_file.write_text('{"extensions": ["-other", "ext.py"]}')
    cwd = ["--cwd", str(workspace)]

    assert _builtin_report(capfd, workspace) == _builtins(mcp=True)

    assert main(["config", "disable", "builtin", "mcp", *cwd]) == 0
    assert json.loads(user_file.read_text())["extensions"] == [
        "-other",
        "ext.py",
        "-builtin:mcp",
    ]
    assert _builtin_report(capfd, workspace) == _builtins(mcp=False)

    # A project entry decides over the user-level one, and is written to the
    # project file alone.
    assert (
        main(["config", "enable", "builtin", "builtin:mcp", "-l", "--approve", *cwd])
        == 0
    )
    project = json.loads((workspace / ".pipy" / "settings.json").read_text())
    assert project["extensions"] == ["+builtin:mcp"]
    assert _builtin_report(capfd, workspace) == _builtins(mcp=True)

    assert main(["config", "list", "--approve", *cwd]) == 0
    listing = capfd.readouterr().out
    assert (
        "  built-in:\n    builtin:codemode [enabled]\n"
        "    builtin:tool-search [enabled]\n    builtin:mcp [enabled]\n"
    ) in listing


def test_config_command_replaces_every_exact_entry_of_the_builtin(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pi matches `-./builtin:mcp` exactly too, and `-` wins over `+` in the
    # user array, so enabling must drop every exact entry for it.
    config_home = tmp_path / "cfg"
    config_home.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    user_file = config_home / "settings.json"
    user_file.write_text(
        '{"extensions": ["-./builtin:mcp", "!builtin:*", "+builtin:codemode"]}'
    )
    cwd = ["--cwd", str(workspace)]
    assert _builtin_report(capfd, workspace) == _builtins(mcp=False, tool_search=False)

    assert main(["config", "enable", "builtin", "mcp", *cwd]) == 0

    assert json.loads(user_file.read_text())["extensions"] == [
        "!builtin:*",
        "+builtin:codemode",
        "+builtin:mcp",
    ]
    assert _builtin_report(capfd, workspace) == _builtins(tool_search=False)

    assert main(["config", "disable", "builtin", "mcp", *cwd]) == 0

    assert json.loads(user_file.read_text())["extensions"] == [
        "!builtin:*",
        "+builtin:codemode",
        "-builtin:mcp",
    ]
    assert _builtin_report(capfd, workspace) == _builtins(mcp=False, tool_search=False)


def test_config_command_refuses_an_unknown_builtin(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "cfg"
    config_home.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))

    assert main(["config", "disable", "builtin", "nope", "--cwd", str(tmp_path)]) == 2

    assert (
        "unknown built-in 'nope'; known: codemode, tool-search, mcp."
        in capfd.readouterr().err
    )
    assert not (config_home / "settings.json").exists()
