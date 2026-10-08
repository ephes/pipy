"""CM1 T10: one paired live measurement, openai-codex/gpt-6.1-sol only.

Run: uv run --extra codemode python scripts/codemode_measure.py --report /tmp/cm1.json
No fallback, dollar cap, credential inspection, or global settings changes.
--prepare-only creates owned fixtures for the coordinator's interactive check.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentRunCompleted,
    MessageCompleted,
    NestedToolCallCompleted,
    NestedToolCallStarted,
    ProviderFailed,
    ToolCallCompleted,
    ToolCallStarted,
    TurnStarted,
    UsageUpdated,
)
from pipy_harness.native.codemode import run_script
from pipy_harness.native.codemode.runtime import RUNTIME_PIN
from pipy_harness.native.codemode.selftest import availability
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexResponsesProvider,
    UrllibSseHTTPClient,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.system_prompt_sections import SystemPromptTemplate

FILES = ("alpha.txt", "beta.txt", "gamma.txt", "delta.txt")
EXPECTED = {
    "matches": [
        {"file": name, "value": f"value-{i}-{j}"}
        for i, name in enumerate(FILES)
        for j in (73, 319)
    ],
    "count": 8,
}
GOAL = (
    "Read every line of alpha.txt, beta.txt, gamma.txt, delta.txt. "
    "Select lines beginning exactly KEEP ; return only JSON with keys matches "
    "(array of {file,value}, value is the text after KEEP ) and count. "
    "Order by the listed file order, then original line order. Do not infer facts "
    "without reading files. Read only these four relative paths; make no writes. "
)


def prompt(mode):
    return GOAL + (
        "Use direct builtin read calls only."
        if mode == "direct"
        else "Use one Python codemode call, tools.read for all four files, "
        "filter in Python and text() only the compact structured answer."
    )


def json_bytes(value):
    # Exactly UrllibSseHTTPClient's default JSON encoding, not compact encoding.
    return len(json.dumps(value).encode("utf-8"))


class CountingSseClient:
    """Forward unchanged; retain only byte lengths, including retry attempts."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.body_bytes = []
        self.schema_bytes = []
        self.input_bytes = []

    def post_sse(self, url, *, headers, body, timeout_seconds, cancel_token=None):
        self.body_bytes.append(json_bytes(body))
        self.schema_bytes.append(json_bytes(body.get("tools", [])))
        self.input_bytes.append(json_bytes(body.get("input", [])))
        return self.delegate.post_sse(
            url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
            cancel_token=cancel_token,
        )

    def report(self):
        return {
            key: {
                "count": len(values),
                "total": sum(values),
                "min": min(values, default=0),
                "max": max(values, default=0),
                "per_attempt": values.copy(),
            }
            for key, values in (
                ("http_body_bytes", self.body_bytes),
                ("declared_schema_bytes", self.schema_bytes),
                ("serialized_input_bytes", self.input_bytes),
            )
        }


class Metrics:
    def __init__(self):
        self.parents = Counter()
        self.children = Counter()
        self.result_bytes = []
        self.parent_seconds = []
        self.turns = 0
        self.errors = 0
        self.nested_errors = 0
        self.final = None
        self.usage = None
        self.outcome = None
        self.compact_bytes = None
        self.compact_facts = False

    def emit(self, event):
        if isinstance(event, TurnStarted):
            self.turns += 1
        elif isinstance(event, ToolCallStarted):
            self.parents[self.call_type(event.call.tool_name)] += 1
        elif isinstance(event, NestedToolCallStarted):
            self.children[self.call_type(event.call.tool_name)] += 1
        elif isinstance(event, NestedToolCallCompleted):
            self.nested_errors += event.result.is_error or event.status != "settled"
        elif isinstance(event, ToolCallCompleted):
            self.errors += event.result.is_error
            self.result_bytes.append(len(event.result.content.value.encode("utf-8")))
            self.parent_seconds.append(event.duration_seconds)
            self.parent_completed(event.result)
        elif isinstance(event, MessageCompleted):
            self.message_completed(event.message)
        elif isinstance(event, UsageUpdated):
            self.usage = asdict(event.cumulative_usage)
        elif isinstance(event, AgentRunCompleted):
            self.outcome = str(event.result.outcome)
        elif isinstance(event, ProviderFailed):
            self.errors += 1

    @staticmethod
    def call_type(name):
        # Unknown model-generated names are not safe report content.
        return name if name in {"read", "codemode"} else "other"

    def parent_completed(self, result):
        if result.tool_name == "codemode":
            body = result.content.value.partition("Output:\n")[2]
            self.compact_bytes = len(body.encode("utf-8"))
            try:
                self.compact_facts = json.loads(body) == EXPECTED
            except ValueError:
                self.compact_facts = False

    def message_completed(self, message):
        if isinstance(message, AgentAssistantMessage) and not message.tool_calls:
            try:
                self.final = json.loads(message.content.value)
            except (ValueError, TypeError):
                self.final = None

    def report(self, mode):
        types_ok = (
            self.parents == {"read": 4} and not self.children
            if mode == "direct"
            else self.parents == {"codemode": 1} and self.children == {"read": 4}
        )
        quality = self.final == EXPECTED and (mode == "direct" or self.compact_facts)
        passed = (
            quality
            and types_ok
            and not self.errors
            and not self.nested_errors
            and self.outcome == "succeeded"
        )
        return dict(
            provider_turn_count=self.turns,
            parent_calls=dict(self.parents),
            nested_calls=dict(self.children),
            parent_result_bytes=self.result_bytes,
            compact_output_bytes=self.compact_bytes,
            compact_exact_facts=self.compact_facts if mode == "codemode" else None,
            parent_duration_seconds=self.parent_seconds,
            provider_usage=self.usage,
            exact_facts=quality,
            call_types_pass=bool(types_ok),
            errors=self.errors,
            nested_errors=self.nested_errors,
            outcome=self.outcome,
            passed=bool(passed),
        )


def prepare(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(root.iterdir()):
        raise ValueError("workspace must be fresh and empty")
    total = 0
    for i, name in enumerate(FILES):
        data = (
            "\n".join(
                f"KEEP value-{i}-{j}"
                if j in (73, 319)
                else f"DROP irrelevant record {i:02d} {j:04d} abcdefghijklmnopqrstuvwxyz"
                for j in range(400)
            )
            + "\n"
        )
        (root / name).write_text(data, encoding="utf-8")
        (root / name).chmod(0o600)
        total += len(data.encode("utf-8"))
    # Normal product tool_call hook confines both direct and nested reads.
    ext = root / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "fixture_gate.py").write_text(
        "from pipy_harness.extensions import ToolBlock\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        f"        if event.tool_name == 'read' and event.input.get('path') not in {FILES!r}:\n"
        "            return ToolBlock(reason='Read outside controlled fixtures denied')\n"
    )
    return total


def isolate(root):
    # Preserve HOME/PIPY_AUTH_DIR: normal auth manager owns existing credentials.
    for key, leaf in (
        ("PIPY_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
        ("PIPY_NATIVE_THEME_PATH", "theme.json"),
        ("PIPY_NATIVE_DEFAULTS_PATH", "defaults.json"),
        ("PIPY_PROMPT_HISTORY_PATH", "history.json"),
        ("PIPY_NATIVE_SESSIONS_ROOT", "sessions"),
        ("PIPY_SESSION_DIR", "archive"),
    ):
        os.environ[key] = str(root / leaf)


def measure(root, mode, provider, client):
    metrics = Metrics()
    start = time.perf_counter()
    manager = SettingsManager.for_workspace(
        root,
        overrides={
            "defaultTools": ["read", "+codemode"] if mode == "codemode" else ["read"],
            "theme": "pi",
            "transport": "sse",
            "quietStartup": True,
        },
    )
    session = CodingSession(
        provider=provider,
        settings_manager=manager,
        agent_event_sink=metrics,
        native_session=NativeSessionTree.create(root, session_dir=root / "sessions"),
        system_prompt_sections=SystemPromptTemplate(cwd=str(root)),
        resource_options=RuntimeResourceOptions(
            no_skills=True, no_prompt_templates=True, no_themes=True
        ),
    )
    run_error = False
    try:
        session.run(
            workspace_root=root,
            input_stream=io.StringIO(prompt(mode) + "\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )
    except Exception as exc:  # noqa: BLE001 — privacy boundary
        del exc
        run_error = True
        metrics.errors += 1
    return dict(
        execution_failed=run_error,
        label=mode,
        wall_seconds=time.perf_counter() - start,
        **metrics.report(mode),
        **client.report(),
    )


def execute(root, run_label="paired-1"):
    report = dict(
        run_label=run_label,
        fixture_version=1,
        order=["direct", "codemode"],
        model="gpt-6.1-sol",
        effort="low",
        provider="openai-codex",
        transport="sse",
        platform=platform.platform(),
        machine=platform.machine(),
        python=platform.python_version(),
        runtime_pin=asdict(RUNTIME_PIN),
        configuration={
            "tool_budget": 50,
            "theme": "pi",
            "persistent_product_tree": True,
        },
        runs=[],
        passed=False,
        metric_definitions={
            "wall_seconds": "Session construction through run return; includes selected availability/selftest, model/tool turns, fresh WASI workers and persistence; excludes fixture creation and separate preflight/minimal worker probe.",
            "parent_duration_seconds": "Canonical ToolCallCompleted duration; codemode includes fresh worker startup, nested tools and settlement.",
            "http_body_bytes": "len(json.dumps(body).encode(utf-8)) for every post_sse attempt including retries; excludes headers and response bytes.",
            "declared_schema_bytes": "Same encoding of body.tools (descriptions and schemas); per HTTP attempt.",
            "serialized_input_bytes": "Same encoding of body.input; per HTTP attempt; not token count.",
            "provider_turn_count": "Canonical TurnStarted count; HTTP attempts reported separately.",
            "parent_result_bytes": "UTF-8 model-facing parent content including codemode header; nested results never enter provider history.",
            "compact_output_bytes": "UTF-8 codemode Output body only; must parse as the exact expected JSON; null for direct tools.",
            "fresh_minimal_worker_seconds": "One standalone text(1) real run including fresh process, engine/cache load, guest startup, execution and cleanup; excludes availability probe; no tool work.",
            "provider_usage": "Cumulative canonical provider usage when available; null means unavailable; no dollar cap.",
        },
    )
    try:
        report["wasmtime"] = version("wasmtime")
        start = time.perf_counter()
        probe = availability()
        report["preflight_seconds"] = time.perf_counter() - start
        if not probe.available:
            report["failure"] = "runtime_unavailable"
            return report
        start = time.perf_counter()
        outcome = run_script("text(1)", call_tool=lambda *_: None, tool_names=())
        report["fresh_minimal_worker_seconds"] = time.perf_counter() - start
        if outcome.error is not None:
            report["failure"] = "minimal_worker_failed"
            return report
        workspace = root / "codemode"
        report["fixture_input_bytes"] = prepare(workspace)
        for mode in ("direct", "codemode"):
            isolate(root / (mode + "-state"))
            client = CountingSseClient(UrllibSseHTTPClient())
            provider = OpenAICodexResponsesProvider(
                model_id="gpt-6.1-sol",
                reasoning_effort="low",
                transport="sse",
                http_client=client,
            )
            result = measure(workspace, mode, provider, client)
            report["runs"].append(result)
            if result["errors"] or result["outcome"] != "succeeded":
                report["failure"] = "provider_or_task_failed"
                return report
        report["passed"] = all(r["passed"] for r in report["runs"])
    except Exception as exc:  # noqa: BLE001 — privacy boundary, never export diagnostics
        del exc  # Discard diagnostic content at the report boundary.
        # Never serialize exceptions, auth objects, provider bodies or headers.
        report["failure"] = "measurement_failed"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--run-label", default="paired-1")
    parser.add_argument(
        "--workspace",
        type=Path,
        help="fresh owned temporary directory; retained for interactive acceptance",
    )
    parser.add_argument(
        "--prepare-only", action="store_true", help="fixtures only; no provider calls"
    )
    args = parser.parse_args()
    if not args.prepare_only and args.report is None:
        parser.error("--report required for live measurement")
    root = args.workspace or Path(tempfile.mkdtemp(prefix="pipy-cm1-measure-"))
    root = root.resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("workspace must be outside the repository")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(root.iterdir()):
        parser.error("workspace must be fresh and empty")
    if args.prepare_only:
        prepare(root / "codemode")
        return 0
    report = execute(root, args.run_label)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    args.report.chmod(0o600)
    print("CM1 report written; pass=" + str(report["passed"]))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
