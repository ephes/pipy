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
)
from pipy_harness.native.extensions import discover_extensions
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
