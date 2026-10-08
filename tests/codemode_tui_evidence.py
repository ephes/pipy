"""Offline full-product TUI evidence driver; see docs/codemode.md.

Run on a tty. Prompts and Escape are consumed by CodingSession's normal input
owner. No UI monkeypatches, replacement tools, credentials or network model.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from pipy_harness.native.agent import (
    AgentToolResultMessage,
    NestedToolCallCompleted,
    NestedToolCallStarted,
    ToolCallCompleted,
)
from pipy_harness.native.codemode.selftest import availability
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager

SUMMARY = """rows = []
for path in ['alpha.txt', 'beta.txt', 'gamma.txt']:
    for line in tools.read(path=path).splitlines():
        if line.startswith('KEEP '):
            rows.append({'file': path, 'value': line[5:]})
text({'matches': rows, 'count': len(rows)})
"""


class Evidence:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.parents: list[AgentToolResultMessage] = []

    def emit(self, event) -> None:
        if isinstance(event, (NestedToolCallStarted, NestedToolCallCompleted)):
            phase = (
                "active-" + event.call.tool_name
                if isinstance(event, NestedToolCallStarted)
                else "settled-" + event.call.tool_name
            )
            (self.root / "phase.txt").write_text(phase)
        if isinstance(event, ToolCallCompleted):
            self.parents.append(event.result)
            (self.root / "parents.json").write_text(
                json.dumps(
                    [
                        {
                            "id": p.provider_correlation_id,
                            "isError": p.is_error,
                            "content": p.content.value,
                            "details": p.details,
                        }
                        for p in self.parents
                    ],
                    indent=2,
                )
            )
            (self.root / "phase.txt").write_text("parent-settled")


def isolate(root: Path) -> None:
    # HOME deliberately survives: codemode.default_paths uses Path.home(),
    # independently of config/XDG state overrides.
    for name, leaf in (
        ("PIPY_CONFIG_HOME", "config"),
        ("PIPY_NATIVE_THEME_PATH", "theme.json"),
        ("PIPY_NATIVE_DEFAULTS_PATH", "defaults.json"),
        ("PIPY_AUTH_DIR", "auth"),
        ("PIPY_PROMPT_HISTORY_PATH", "prompt-history.json"),
        ("PIPY_NATIVE_SESSIONS_ROOT", "sessions"),
        ("XDG_STATE_HOME", "state"),
    ):
        os.environ[name] = str(root / leaf)
    os.environ["PIPY_OFFLINE"] = "1"


def verify(root: Path, phase: str, seen: set[int]) -> None:
    parents = json.loads((root / "parents.json").read_text())
    tree = Path((root / "session-path.txt").read_text())
    entries = [json.loads(line) for line in tree.read_text().splitlines()]
    persisted = [
        e["message"]
        for e in entries
        if e.get("type") == "message" and e["message"]["role"] == "tool"
    ]
    assert [m["provider_correlation_id"] for m in persisted] == [
        p["id"] for p in parents
    ]
    assert [m["details"] for m in persisted] == [p["details"] for p in parents]
    assert not any(e.get("type") == "custom" for e in entries)
    if phase != "success":
        assert parents[0]["isError"] and "Script aborted" in parents[0]["content"]
        statuses = [c["status"] for c in parents[0]["details"]["nestedCalls"]["calls"]]
        assert statuses == (["cancelled"] if phase == "bash" else ["ok"])
    assert not parents[-1]["isError"] and '"count": 3' in parents[-1]["content"]
    assert [
        c["arguments"]["path"]
        for c in parents[-1]["details"]["nestedCalls"]["calls"]
        if c["name"] == "read"
    ] == ["alpha.txt", "beta.txt", "gamma.txt"]
    assert seen, "no actual worker observed"
    for pid in seen:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            continue
        raise AssertionError(f"worker group survived: {pid}")
    (root / "verified.json").write_text(
        json.dumps(
            {"phase": phase, "workersReaped": sorted(seen), "parents": len(parents)}
        )
    )


def fresh_workspace(parser: argparse.ArgumentParser, workspace: Path) -> Path:
    root = workspace.resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("use a fresh empty evidence workspace (including for replay)")
    root.mkdir(parents=True, exist_ok=True)
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("success", "bash", "compute"), default="success"
    )
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    root = fresh_workspace(parser, args.workspace)
    isolate(root)
    probe = availability()
    if not probe.available:
        parser.error(f"installed real runtime required: {probe.reason}")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("a real tty is required")
    for name, value in zip(
        ("alpha", "beta", "gamma"), ("apple", "berry", "cherry"), strict=True
    ):
        (root / f"{name}.txt").write_text(f"DROP noise\nKEEP {value}\n")
    manager = SettingsManager(
        global_path=root / "config" / "settings.json",
        project_path=root / ".pipy" / "settings.json",
        overrides={"defaultTools": ["+codemode"], "theme": "pi", "quietStartup": True},
    )
    calls: tuple[tuple[ProviderToolCall, ...], ...]
    if args.replay:
        tree = NativeSessionTree.open(args.replay)
        calls = ()
    else:
        tree = NativeSessionTree.create(root, session_dir=root / "sessions")
        first = "tools.bash(command='touch bash-active; sleep 3')\n" + SUMMARY
        if args.phase == "bash":
            first = "tools.bash(command='touch bash-active; sleep 30')\n" + SUMMARY
        elif args.phase == "compute":
            first = "tools.read(path='alpha.txt')\nwhile True: pass"
        call = ProviderToolCall("parent", "codemode", json.dumps({"code": first}))
        fresh = ProviderToolCall("fresh", "codemode", json.dumps({"code": SUMMARY}))
        calls = ((call,), ()) if args.phase == "success" else ((call,), (fresh,), ())
    (root / "session-path.txt").write_text(str(tree.path))
    (root / "runtime-check.json").write_text(
        json.dumps(
            {
                "available": probe.available,
                "backend": "wasi",
                "hash": probe.runtime_sha256,
            }
        )
    )
    evidence = Evidence(root)
    seen: set[int] = set()
    seen_lock = threading.Lock()
    original_popen = subprocess.Popen

    class RecordingPopen(subprocess.Popen):
        # Observe the standard subprocess boundary without changing argv,
        # environment, pipes, runtime, tools, UI or cancellation behavior.
        def __init__(self, argv, *args, **kwargs):
            super().__init__(argv, *args, **kwargs)
            if any("/codemode/worker.py" in str(arg) for arg in argv):
                # Startup probes and script workers can launch on different
                # threads; publish each observation and report under one guard.
                with seen_lock:
                    seen.add(self.pid)
                    (root / "worker-pids.json").write_text(json.dumps(sorted(seen)))

    setattr(subprocess, "Popen", RecordingPopen)
    try:
        CodingSession(
            provider=FakeNativeProvider(
                supports_tool_calls=True,
                final_text="T9_DONE",
                programmable_tool_calls=calls,
            ),
            settings_manager=manager,
            native_session=tree,
            agent_event_sink=evidence,
        ).run(
            workspace_root=root,
            input_stream=sys.stdin,
            output_stream=sys.stdout,
            error_stream=sys.stderr,
        )
    finally:
        setattr(subprocess, "Popen", original_popen)
    if not args.replay:
        with seen_lock:
            recorded_workers = set(seen)
        verify(root, args.phase, recorded_workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
