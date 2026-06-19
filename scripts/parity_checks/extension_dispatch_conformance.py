"""Hard conformance gate for extension command dispatch (slice 3).

Drives the real `NativeToolReplSession.run` with the deterministic fake
provider in a temporary workspace, and asserts the slice-3 invariants
from `docs/extension-api.md`:

1. an activated extension `/<command>` runs through the product REPL
   dispatch and its `ctx.ui.notify` output reaches live UI output;
2. the extension command triggers NO provider turn (only a genuine
   prompt reaches the provider);
3. a handler exception is bounded into a safe diagnostic and the loop
   survives;
4. an extension cannot shadow a built-in command name (disabled at
   activation, so the built-in still resolves);
5. a custom session-entry renderer plus `ctx.append_entry(...)` persists and
   renders without a provider turn.

Exits 0 when every check passes, 1 otherwise. No network.

Run:

    uv run python scripts/parity_checks/extension_dispatch_conformance.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderResult
from pipy_harness.native.extension_runtime import (
    activate_extensions,
    extension_command_map,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.session_tree import CustomEntry, NativeSessionTree
from pipy_harness.native.tool_loop_session import NativeToolReplSession


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class _Provider:
    requests: list[ProviderRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return "fake-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="OK",
            usage=None,
            metadata=None,
            tool_calls=(),
        )


def run_checks(workspace: Path) -> list[Check]:
    ext = workspace / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "sayhi.py").write_text(
        "def activate(api):\n"
        "    def hi(ctx, args):\n"
        "        ctx.ui.notify('RAN:' + args)\n"
        "    api.register_command('sayhi', 'say hi', hi)\n",
        encoding="utf-8",
    )
    (ext / "crash.py").write_text(
        "def activate(api):\n"
        "    def boom(ctx, args):\n"
        "        raise RuntimeError('/secret/leak-xyz')\n"
        "    api.register_command('crash', 'b', boom)\n",
        encoding="utf-8",
    )
    (ext / "shadow.py").write_text(
        "def activate(api):\n"
        "    api.register_command('help', 'x', lambda ctx, args: None)\n",
        encoding="utf-8",
    )
    (ext / "card.py").write_text(
        "def activate(api):\n"
        "    api.register_message_renderer('card', lambda data: ['CARD:' + data['title']])\n"
        "    def card(ctx, args):\n"
        "        entry_id = ctx.append_entry('card', {'title': args or 'untitled'})\n"
        "        ctx.ui.notify('ENTRY:' + str(entry_id))\n"
        "    api.register_command('card', 'add card', card)\n",
        encoding="utf-8",
    )

    provider = _Provider()
    native_session = NativeSessionTree.create(workspace, persist=False)
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        native_session=native_session,
    )
    error_stream = StringIO()
    result = session.run(
        workspace_root=workspace,
        input_stream=StringIO("/sayhi world\n/card hello\n/crash\nplain prompt\n"),
        output_stream=StringIO(),
        error_stream=error_stream,
    )
    err = error_stream.getvalue()

    checks: list[Check] = []
    checks.append(
        Check("dispatch_runs", "RAN:world" in err, "extension command ran with args")
    )
    checks.append(
        Check(
            "no_provider_turn",
            len(provider.requests) == 1
            and "plain prompt" in provider.requests[0].user_prompt,
            "extension commands issue no provider turn",
        )
    )
    custom_entries = [
        entry for entry in native_session.entries if isinstance(entry, CustomEntry)
    ]
    checks.append(
        Check(
            "custom_entry_rendered",
            "CARD:hello" in err
            and "ENTRY:" in err
            and len(custom_entries) == 1
            and custom_entries[0].custom_type == "card"
            and custom_entries[0].data == {"title": "hello"},
            "custom entry persisted and rendered without a provider turn",
        )
    )
    checks.append(
        Check(
            "handler_error_bounded",
            "/crash failed" in err
            and "/secret/leak-xyz" not in err
            and "leak-xyz" not in err,
            "handler exception bounded, no raw message leak",
        )
    )
    checks.append(
        Check("loop_survived", result.user_turn_count == 1, "loop survived bad handler")
    )

    descriptors = discover_extensions(
        workspace, config_home_env={"PIPY_CONFIG_HOME": str(workspace / "nocfg")}, home_dir=workspace
    )
    command_map = extension_command_map(
        activate_extensions(descriptors, reserved_command_names=("help",))
    )
    checks.append(
        Check(
            "no_builtin_shadow",
            "help" not in command_map and "sayhi" in command_map,
            "extension cannot shadow a built-in command",
        )
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "work"
        workspace.mkdir()
        # Isolate global config so only the workspace extensions load.
        os.environ["PIPY_CONFIG_HOME"] = str(Path(tmp) / "empty-global")
        checks = run_checks(workspace)

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
