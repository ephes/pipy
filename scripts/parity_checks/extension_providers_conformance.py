"""Hard conformance gate for extension provider registration (slice 11).

Activates extensions with the real `activate_extensions` and asserts the
slice-11 invariants from `docs/extension-api.md`:

1. `api.register_provider(ExtensionProvider(...))` is collected with its
   name / default model / models;
2. the provider `factory` builds a working `ProviderPort`
   (name / model_id / complete);
3. `api.unregister_provider(name)` is recorded;
4. a duplicate provider name across extensions disables the later one;
5. an invalid provider (empty name / non-callable factory) disables the
   extension;
6. a factory that raises is bounded (yields None, no crash).

The catalog / `/model` selection wiring is the provider-catalog track's
follow-on; this gate proves the registration mechanism + ProviderPort
composition.

Exits 0 when every check passes, 1 otherwise. No network.

Run:

    uv run python scripts/parity_checks/extension_providers_conformance.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.extension_runtime import (
    activate_extensions,
    build_extension_provider_port,
    extension_providers,
    extension_unregistered_providers,
)
from pipy_harness.native.extensions import discover_extensions


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


_FAKE_PROVIDER = (
    "from pipy_harness.extensions import ExtensionProvider\n"
    "from pipy_harness.models import HarnessStatus\n"
    "from pipy_harness.native.models import ProviderResult\n"
    "from datetime import datetime, timezone\n"
    "class _Port:\n"
    "    def __init__(self, ctx): self._ctx = ctx\n"
    "    @property\n"
    "    def name(self): return self._ctx.provider_name\n"
    "    @property\n"
    "    def model_id(self): return self._ctx.default_model or 'm'\n"
    "    @property\n"
    "    def supports_tool_calls(self): return False\n"
    "    def complete(self, request, **k):\n"
    "        now = datetime(2026, 6, 15, tzinfo=timezone.utc)\n"
    "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
    "            provider_name=self.name, model_id=self.model_id,\n"
    "            started_at=now, ended_at=now, final_text='ext-ok', tool_calls=())\n"
    "def activate(api):\n"
    "    api.register_provider(ExtensionProvider(name='myprov',\n"
    "        default_model='myprov/big', models=('myprov/big',),\n"
    "        factory=lambda ctx: _Port(ctx)))\n"
)


def _write(workspace: Path, name: str, body: str) -> None:
    ext = workspace / ".pipy" / "extensions"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / f"{name}.py").write_text(body, encoding="utf-8")


def _activate(workspace: Path, base: Path) -> list:
    return activate_extensions(
        discover_extensions(
            workspace,
            config_home_env={"PIPY_CONFIG_HOME": str(base / "nocfg")},
            home_dir=workspace,
        )
    )


def run_checks(base: Path) -> list[Check]:
    checks: list[Check] = []

    reg = base / "reg"
    reg.mkdir()
    _write(reg, "aaa", _FAKE_PROVIDER)
    _write(reg, "bbb", _FAKE_PROVIDER)  # duplicate provider name "myprov"
    _write(reg, "unreg", "def activate(api):\n    api.unregister_provider('openai-codex')\n")
    _write(
        reg,
        "badprov",
        "from pipy_harness.extensions import ExtensionProvider\n"
        "def activate(api):\n"
        "    api.register_provider(ExtensionProvider(name='',\n"
        "        default_model=None, models=(), factory=lambda ctx: None))\n",
    )
    _write(
        reg,
        "crashprov",
        "from pipy_harness.extensions import ExtensionProvider\n"
        "def _boom(ctx):\n    raise RuntimeError('x')\n"
        "def activate(api):\n"
        "    api.register_provider(ExtensionProvider(name='crashy',\n"
        "        default_model='crashy/m', models=('crashy/m',), factory=_boom))\n",
    )
    activated = _activate(reg, base)
    providers = extension_providers(activated)

    my = [p for p in providers if p.provider.name == "myprov"]
    checks.append(
        Check(
            "register_and_collect",
            len(my) == 1
            and my[0].provider.default_model == "myprov/big"
            and my[0].provider.models == ("myprov/big",),
            "register_provider is collected with its metadata",
        )
    )
    port = build_extension_provider_port(my[0]) if my else None
    checks.append(
        Check(
            "factory_builds_port",
            port is not None
            and port.name == "myprov"
            and port.model_id == "myprov/big"
            and port.complete(object()).status is HarnessStatus.SUCCEEDED,
            "factory builds a working ProviderPort",
        )
    )
    checks.append(
        Check(
            "unregister_recorded",
            "openai-codex" in extension_unregistered_providers(activated),
            "unregister_provider is recorded",
        )
    )
    aaa = next((a for a in activated if a.name == "aaa"), None)
    bbb = next((a for a in activated if a.name == "bbb"), None)
    checks.append(
        Check(
            "duplicate_disabled",
            aaa is not None
            and aaa.status == "activated"
            and bbb is not None
            and bbb.status == "disabled",
            "duplicate provider name disables the later extension",
        )
    )
    badprov = next((a for a in activated if a.name == "badprov"), None)
    checks.append(
        Check(
            "invalid_disabled",
            badprov is not None and badprov.status == "disabled",
            "invalid provider disables the extension",
        )
    )
    crashy = next(
        (p for p in providers if p.provider.name == "crashy"), None
    )
    checks.append(
        Check(
            "factory_failure_bounded",
            crashy is not None and build_extension_provider_port(crashy) is None,
            "a factory that raises is bounded (None, no crash)",
        )
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["PIPY_CONFIG_HOME"] = str(base / "empty-global")
        checks = run_checks(base)

    passed = all(c.passed for c in checks)
    if args.json:
        report = {
            "passed": passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in checks
            ],
        }
        print(json.dumps(report, indent=2))
    else:
        for c in checks:
            status = "PASS" if c.passed else "FAIL"
            print(f"[{status}] {c.name}: {c.detail}")
        print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
