"""Slice 11 tests for extension provider registration.

An extension registers a model provider via
`api.register_provider(ExtensionProvider(...))` (name, default model,
models, factory) that composes with the provider catalog; the factory
builds a `ProviderPort`. `api.unregister_provider(name)` records a removal
(restoring any built-in the provider overrode). Registrations are staged
and committed only on successful activation.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pipy_harness.models import HarnessStatus
from pipy_harness.native.extension_runtime import (
    activate_extensions,
    build_extension_provider_port,
    extension_providers,
    extension_unregistered_providers,
    try_build_extension_provider_port,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.catalog_state import ProviderCatalogState, format_list_models
from pipy_harness.native.extension_provider_catalog import (
    extension_reserved_command_names,
    extension_reserved_tool_names,
    load_extension_provider_contributions,
)
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    NativeReplProviderState,
    default_selection_for,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.provider import ProviderPort


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _write(workspace: Path, name: str, body: str) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(body, encoding="utf-8")


def _activate(workspace: Path) -> list:
    return activate_extensions(
        discover_extensions(workspace, config_home_env={}, home_dir=workspace)
    )


_FAKE_PROVIDER = (
    "from pipy_harness.extensions import ExtensionProvider\n"
    "from pipy_harness.models import HarnessStatus\n"
    "from pipy_harness.native.models import ProviderResult\n"
    "from datetime import datetime, timezone\n"
    "class _Port:\n"
    "    def __init__(self, ctx):\n"
    "        self._ctx = ctx\n"
    "    @property\n"
    "    def name(self): return self._ctx.provider_name\n"
    "    @property\n"
    "    def model_id(self): return self._ctx.default_model or 'm'\n"
    "    @property\n"
    "    def supports_tool_calls(self): return False\n"
    "    def complete(self, request, **kwargs):\n"
    "        now = datetime(2026, 6, 15, tzinfo=timezone.utc)\n"
    "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
    "            provider_name=self.name, model_id=self.model_id,\n"
    "            started_at=now, ended_at=now, final_text='ext-ok', tool_calls=())\n"
    "def activate(api):\n"
    "    api.register_provider(ExtensionProvider(name='myprov',\n"
    "        default_model='myprov/big', models=('myprov/big','myprov/small'),\n"
    "        factory=lambda ctx: _Port(ctx)))\n"
)

_SELECTABLE_PROVIDER = (
    "from pipy_harness.extensions import ExtensionProvider\n"
    "from pipy_harness.models import HarnessStatus\n"
    "from pipy_harness.native.models import ProviderResult\n"
    "from datetime import datetime, timezone\n"
    "class _Port:\n"
    "    def __init__(self, ctx): self._ctx = ctx\n"
    "    @property\n"
    "    def name(self): return self._ctx.provider_name\n"
    "    @property\n"
    "    def model_id(self): return self._ctx.model_id or self._ctx.default_model\n"
    "    @property\n"
    "    def supports_tool_calls(self): return True\n"
    "    def complete(self, request, **kwargs):\n"
    "        now = datetime(2026, 6, 15, tzinfo=timezone.utc)\n"
    "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
    "            provider_name=self.name, model_id=self.model_id,\n"
    "            started_at=now, ended_at=now, final_text='ext-' + self.model_id,\n"
    "            tool_calls=())\n"
    "def activate(api):\n"
    "    api.register_provider(ExtensionProvider(name='extprov',\n"
    "        default_model='big', models=('small','big'), factory=lambda ctx: _Port(ctx)))\n"
)


def test_register_provider_is_collected(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "provext", _FAKE_PROVIDER)

    providers = extension_providers(_activate(workspace))

    assert [p.provider.name for p in providers] == ["myprov"]
    assert providers[0].provider.default_model == "myprov/big"
    assert providers[0].provider.models == ("myprov/big", "myprov/small")


def test_factory_builds_a_working_provider_port(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "provext", _FAKE_PROVIDER)
    registered = extension_providers(_activate(workspace))[0]

    built = build_extension_provider_port(registered)

    assert built is not None
    port = cast(ProviderPort, built)
    assert port.name == "myprov"
    assert port.model_id == "myprov/big"
    result = port.complete(cast("object", None))  # type: ignore[arg-type]
    assert result.status is HarnessStatus.SUCCEEDED
    assert result.final_text == "ext-ok"


def test_unregister_provider_is_recorded(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "unreg",
        "def activate(api):\n    api.unregister_provider('openai-codex')\n",
    )

    activated = _activate(workspace)

    assert "openai-codex" in extension_unregistered_providers(activated)


def test_invalid_provider_disables_extension(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "badprov",
        "from pipy_harness.extensions import ExtensionProvider\n"
        "def activate(api):\n"
        "    api.register_provider(ExtensionProvider(name='',\n"
        "        default_model=None, models=(), factory=lambda ctx: None))\n",
    )

    activated = next(a for a in _activate(workspace) if a.name == "badprov")
    assert activated.status == "disabled"
    assert not extension_providers([activated])


def test_duplicate_provider_disables_second(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "aaa", _FAKE_PROVIDER)
    _write(workspace, "bbb", _FAKE_PROVIDER)

    activated = _activate(workspace)
    aaa = next(a for a in activated if a.name == "aaa")
    bbb = next(a for a in activated if a.name == "bbb")

    assert aaa.status == "activated"
    assert bbb.status == "disabled"
    assert [p.provider.name for p in extension_providers(activated)] == ["myprov"]


def test_factory_failure_is_bounded(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "crashprov",
        "from pipy_harness.extensions import ExtensionProvider\n"
        "def _boom(ctx):\n"
        "    raise RuntimeError('factory failed')\n"
        "def activate(api):\n"
        "    api.register_provider(ExtensionProvider(name='crashy',\n"
        "        default_model='crashy/m', models=('crashy/m',), factory=_boom))\n",
    )
    registered = extension_providers(_activate(workspace))[0]

    # A factory that raises yields None rather than crashing the caller.
    assert build_extension_provider_port(registered) is None
    build_result = try_build_extension_provider_port(registered)
    assert build_result.port is None
    assert build_result.diagnostic == "RuntimeError"


def test_provider_registration_normalizes_names_and_models(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "spacedprov",
        "from pipy_harness.extensions import ExtensionProvider\n"
        "def activate(api):\n"
        "    api.register_provider(ExtensionProvider(name=' spaced ',\n"
        "        default_model=' m ', models=(' m ', ' n '), factory=lambda ctx: None))\n",
    )

    providers = extension_providers(_activate(workspace))
    assert len(providers) == 1
    provider = providers[0].provider
    assert provider.name == "spaced"
    assert provider.default_model == "m"
    assert provider.models == ("m", "n")

    state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    state.set_extension_provider_contributions(providers, ())
    assert state.find("spaced", "m") is not None
    assert state.extension_provider_for("spaced") is providers[0]


def test_extension_provider_appears_in_catalog_options_and_list_output(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "selectable", _SELECTABLE_PROVIDER)
    providers = extension_providers(_activate(workspace))
    state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    state.set_extension_provider_contributions(providers, ())
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda _selection: (_ for _ in ()).throw(
            AssertionError("legacy factory must not build extension providers")
        ),
        catalog_state=state,
        persist_defaults=False,
    )

    refs = [option.selection.reference for option in repl_state.model_options()]
    assert "extprov/big" in refs
    assert "extprov/small" in refs
    output = format_list_models(state.get_available(), search="extprov", load_error=None)
    assert "extprov" in output
    assert "big" in output
    assert str(workspace) not in output


def test_selecting_extension_provider_constructs_selected_provider_port(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "selectable", _SELECTABLE_PROVIDER)
    state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    state.set_extension_provider_contributions(extension_providers(_activate(workspace)), ())
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda _selection: (_ for _ in ()).throw(
            AssertionError("legacy factory must not build extension providers")
        ),
        catalog_state=state,
        persist_defaults=False,
    )

    ok, message = repl_state.select_model("extprov/small")
    assert ok, message
    assert repl_state.selection == NativeModelSelection("extprov", "small")
    port = repl_state.current_provider()
    assert port.name == "extprov"
    assert port.model_id == "small"
    result = port.complete(
        ProviderRequest(
            system_prompt="",
            user_prompt="hi",
            provider_name="extprov",
            model_id="small",
            cwd=workspace,
        )
    )
    assert result.status is HarnessStatus.SUCCEEDED
    assert result.final_text == "ext-small"


def test_startup_selection_resolves_extension_provider_default(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "selectable", _SELECTABLE_PROVIDER)
    state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    state.set_extension_provider_contributions(extension_providers(_activate(workspace)), ())

    selection = default_selection_for(
        native_provider="extprov", native_model=None, rows=state.get_all()
    )

    assert selection == NativeModelSelection("extprov", "big")


def test_extension_provider_reload_recomputes_removed_entries(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "selectable", _SELECTABLE_PROVIDER)
    providers, unregistered = load_extension_provider_contributions(workspace)
    state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    state.set_extension_provider_contributions(providers, unregistered)
    assert state.find("extprov", "big") is not None

    (workspace / ".pipy" / "extensions" / "selectable.py").unlink()
    providers2, unregistered2 = load_extension_provider_contributions(workspace)
    state.refresh()
    state.set_extension_provider_contributions(providers2, unregistered2)

    assert state.find("extprov", "big") is None


def test_removed_active_extension_provider_resets_to_available_catalog_model(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "selectable", _SELECTABLE_PROVIDER)
    providers, unregistered = load_extension_provider_contributions(workspace)
    state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    state.set_extension_provider_contributions(providers, unregistered)
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("extprov", "small"),
        provider_factory=lambda _selection: (_ for _ in ()).throw(
            AssertionError("legacy factory must not build extension providers")
        ),
        catalog_state=state,
        persist_defaults=False,
    )
    assert repl_state.current_selection_supported() is True

    state.set_extension_provider_contributions((), ())

    assert repl_state.current_selection_supported() is False
    fallback = repl_state.reset_to_first_available_model()
    assert fallback is not None
    assert repl_state.current_selection_supported() is True
    assert repl_state.selection.provider_name != "extprov"


def test_reserved_command_collision_hides_provider_contribution(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "collides",
        "from pipy_harness.extensions import ExtensionProvider\n"
        "def activate(api):\n"
        "    api.register_command('model', 'bad shadow', lambda ctx, args: None)\n"
        "    api.register_provider(ExtensionProvider(name='shadowprov',\n"
        "        default_model='m', models=('m',), factory=lambda ctx: None))\n",
    )

    providers, unregistered = load_extension_provider_contributions(
        workspace,
        reserved_command_names=extension_reserved_command_names(),
    )

    assert providers == ()
    assert unregistered == ()


def test_reserved_tool_collision_hides_provider_contribution(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "tool_collides",
        "from pipy_harness.extensions import ExtensionProvider, ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(name='bash', description='bad shadow',\n"
        "        input_schema={'type': 'object', 'properties': {}},\n"
        "        handler=lambda ctx, data: ToolResult(content='nope')))\n"
        "    api.register_provider(ExtensionProvider(name='toolshadow',\n"
        "        default_model='m', models=('m',), factory=lambda ctx: None))\n",
    )

    providers, unregistered = load_extension_provider_contributions(
        workspace,
        reserved_tool_names=extension_reserved_tool_names(),
    )

    assert providers == ()
    assert unregistered == ()


def test_failing_extension_provider_factory_fails_closed_from_catalog(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "crashprov",
        "from pipy_harness.extensions import ExtensionProvider\n"
        "def _boom(ctx):\n"
        "    raise RuntimeError('factory failed')\n"
        "def activate(api):\n"
        "    api.register_provider(ExtensionProvider(name='crashy',\n"
        "        default_model='m', models=('m',), factory=_boom))\n",
    )
    state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    state.set_extension_provider_contributions(extension_providers(_activate(workspace)), ())
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("crashy", "m"),
        provider_factory=lambda _selection: (_ for _ in ()).throw(
            AssertionError("legacy factory must not build extension providers")
        ),
        catalog_state=state,
        persist_defaults=False,
    )

    assert [o.selection.reference for o in repl_state.model_options()].count("crashy/m") == 1
    port = repl_state.current_provider()
    assert port.name == "crashy"
    assert port.supports_tool_calls is False
    result = port.complete(
        ProviderRequest(
            system_prompt="",
            user_prompt="hi",
            provider_name="crashy",
            model_id="m",
            cwd=workspace,
        )
    )
    assert result.status is HarnessStatus.FAILED
    assert result.error_type == "ExtensionProviderFactoryError"
    assert result.error_message == "extension provider factory failed: RuntimeError"
    assert "RuntimeError('factory failed')" not in result.error_message
    assert str(workspace) not in result.error_message


def test_unregister_provider_hides_extension_overlay_without_corrupting_builtin(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "override",
        "from pipy_harness.extensions import ExtensionProvider\n"
        "def activate(api):\n"
        "    api.register_provider(ExtensionProvider(name='openai',\n"
        "        default_model='ext', models=('ext',), factory=lambda ctx: None))\n"
        "    api.unregister_provider('openai')\n",
    )
    activated = _activate(workspace)
    state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    state.set_extension_provider_contributions(
        extension_providers(activated),
        extension_unregistered_providers(activated),
    )

    assert state.find("openai", "ext") is None
    assert state.find("openai", "gpt-5.5") is not None
