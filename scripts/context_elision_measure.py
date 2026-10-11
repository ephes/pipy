"""Paired synthetic live evaluation; no product policy changes.

Run with uv run python scripts/context_elision_measure.py --report /tmp/elision.json.
Uses existing openai-codex authentication, gpt-6.1-sol/low/SSE. Private product
trees stay outside git; reports contain counters and correctness booleans only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import statistics
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codemode_measure import CountingSseClient, isolate

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.request import freeze_provider_request
from pipy_harness.native.coding.request_budget import estimate_request
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexResponsesProvider,
    UrllibSseHTTPClient,
)
from pipy_harness.native.repl.loop_step import _RequestPreparationEffects
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tools.base import ToolExecutionResult
from pipy_harness.native.tools.registry import production_tool_registry
from pipy_harness.sdk import open_product_session

CEILING = 24000
RESERVE = 2048
SOFT_FRACTION = 0.6
RECENT_CYCLES = 2
MIN_CHARS = 2048
STUB = "[Evaluation: earlier successful read output omitted"
CASES = ("bulk", "reread", "history_only")
RESOURCES = RuntimeResourceOptions(
    no_extensions=True, no_skills=True, no_prompt_templates=True, no_themes=True
)


def estimated_input(request):
    return estimate_request(
        freeze_provider_request(request), image_count=0, output_reserve=RESERVE
    ).input_tokens


def elide(request):
    """Pure request projection; preserve pairing, prose, errors and recent cycles.

    Only this fixture's completed read cycles are eligible. Product history and
    summary input are untouched. Trigger on the assembled estimated input.
    """
    if estimated_input(request) < SOFT_FRACTION * (CEILING - RESERVE):
        return request
    cycles = []
    for i, message in enumerate(request.messages):
        if isinstance(message, AgentAssistantMessage) and message.tool_calls:
            results = request.messages[i + 1 : i + 1 + len(message.tool_calls)]
            pending = {call.provider_correlation_id for call in message.tool_calls}
            if (
                len(results) == len(pending)
                and all(
                    isinstance(result, AgentToolResultMessage) for result in results
                )
                and {result.provider_correlation_id for result in results} == pending
            ):
                cycles.append(tuple(range(i + 1, i + 1 + len(results))))
    eligible = {i for cycle in cycles[:-RECENT_CYCLES] for i in cycle}
    messages = tuple(
        replace(
            message,
            content=ProductContent(
                f"{STUB}: {len(message.content.value)} characters. "
                "Use the original read path from its call to recover if available.]"
            ),
        )
        if i in eligible
        and isinstance(message, AgentToolResultMessage)
        and message.tool_name == "read"
        and not message.is_error
        and len(message.content.value) > MIN_CHARS
        else message
        for i, message in enumerate(request.messages)
    )
    return replace(request, messages=messages)


class ObservedAttempt:
    def __init__(self, inner, row):
        self.inner = inner
        self.row = row

    def complete_attempt(self, allowance):
        start = time.perf_counter()
        result = self.inner.complete_attempt(allowance)
        usage = result.usage or {}
        self.row["attempts"].append(
            {
                "seconds": time.perf_counter() - start,
                "succeeded": str(result.status) == "succeeded",
                "usage": {
                    key: usage.get(key)
                    for key in (
                        "input_tokens",
                        "output_tokens",
                        "cached_tokens",
                        "reasoning_tokens",
                        "total_tokens",
                    )
                },
            }
        )
        return result


class ObservedProvider(OpenAICodexResponsesProvider):
    def __init__(self, client):
        super().__init__(
            model_id="gpt-6.1-sol",
            reasoning_effort="low",
            transport="sse",
            http_client=client,
        )
        self.rows = []
        self.phase = 0

    def prepare_completion(self, request, **kwargs):
        summary = request.system_prompt.startswith("Summarize conversation context")
        row = {
            "phase": self.phase,
            "kind": "summary" if summary else "ordinary",
            "available_tools": len(request.available_tools),
            "estimated_input_tokens": estimated_input(request),
            "elided_results": sum(
                isinstance(message, AgentToolResultMessage)
                and message.content.value.startswith(STUB)
                for message in request.messages
            ),
            "attempts": [],
        }
        self.rows.append(row)
        return ObservedAttempt(super().prepare_completion(request, **kwargs), row)


class FixtureRead:
    """Unchanged read definition; restrict execution to the one recovery fixture."""

    def __init__(self):
        self.inner = production_tool_registry()["read"]
        self.calls = 0
        self.errors = 0

    @property
    def definition(self):
        return self.inner.definition

    def invoke(self, request, context):
        self.calls += 1
        if request.arguments.get("path") != "log-0.txt":
            self.errors += 1
            return ToolExecutionResult(
                request.tool_request_id,
                "Only log-0.txt is available in this evaluation.",
                is_error=True,
                provider_correlation_id=request.provider_correlation_id,
            )
        result = self.inner.invoke(request, context)
        self.errors += result.is_error
        return result


def fixture_facts(seed):
    rng = random.Random(seed)
    code = "".join(rng.choices("abcdefghjkmnpqrstuvwxyz23456789", k=12))
    release = "".join(rng.choices("abcdefghjkmnpqrstuvwxyz", k=8))
    return code, release


def fixture(root, case, seed):
    """Seed identical synthetic completed reads, never attributed to live model."""
    root.mkdir(parents=True, mode=0o700)
    code, release = fixture_facts(seed)
    tree = NativeSessionTree.create(root, session_dir=root / "sessions")
    tree.append_message(
        AgentUserMessage(
            ProductContent(
                "Synthetic past task: inspect logs. Work in dry-run mode; "
                f"release codename is {release}. "
                "Preserve the recovery_code in the first read for a later question."
            )
        )
    )
    for i in range(8):
        name = f"log-{i}.txt"
        data = (
            f"ARCHIVED_FACT recovery_code={code}\n" if i == 0 else "Log header\n"
        ) + "".join(
            f"record {i:02d}-{j:04d} unused diagnostic abcdefghijklmnopqrstuvwxyz\n"
            for j in range(170)
        )
        if case != "history_only" or i != 0:
            (root / name).write_text(data)
        call = AgentToolCall(
            f"seed-{i}", "read", ProductContent(json.dumps({"path": name}))
        )
        tree.append_message(
            AgentAssistantMessage(ProductContent("Inspect log."), (call,))
        )
        tree.append_message(
            AgentToolResultMessage(
                f"pipy-tool-seed-{i}", "read", ProductContent(data), f"seed-{i}"
            )
        )
    tree.append_message(AgentAssistantMessage(ProductContent("Inspection complete.")))
    expected = (
        {"release": release, "mode": "dry-run"}
        if case == "bulk"
        else {"recovery_code": code}
    )
    question = (
        "Return only JSON with keys release and mode from the earlier task constraints."
        if case == "bulk"
        else "Return only JSON with key recovery_code from the first historical read. "
        "Use null if you cannot recover the exact value. Never guess. "
        + (
            "You may reread log-0.txt if needed."
            if case == "reread"
            else "The original file no longer exists; no tools are available."
        )
    )
    return tree, expected, question


def manager(root, case):
    return SettingsManager(
        global_path=root / "isolated-settings.json",
        env={},
        overrides={
            "defaultTools": ["read"],
            "theme": "pi",
            "transport": "sse",
            "quietStartup": True,
            "compaction": {
                "enabled": True,
                "contextWindow": CEILING,
                "reserveTokens": RESERVE,
            },
        },
    )


def phase_result(snapshot, expected):
    boundary = next(
        (
            i + 1
            for i in range(len(snapshot.messages) - 1, -1, -1)
            if isinstance(snapshot.messages[i], AgentUserMessage)
        ),
        len(snapshot.messages),
    )
    finals = [
        m
        for m in snapshot.messages[boundary:]
        if isinstance(m, AgentAssistantMessage)
        and not m.tool_calls
        and not m.stop_reason
    ]
    answer = None
    if finals:
        try:
            answer = json.loads(finals[-1].content.value)
        except ValueError:
            pass
    return {
        "final_present": bool(finals),
        "exact_facts": answer == expected,
        "reported_unknown": answer == {key: None for key in expected},
        "compaction_count": snapshot.compaction_count,
        "preparation_refused": snapshot.preparation_failure is not None,
        "provider_failed": snapshot.provider_failure is not None,
    }


def usage_totals(rows):
    totals = Counter()
    complete = True
    for row in rows:
        for attempt in row["attempts"]:
            for key, value in attempt["usage"].items():
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[key] += value
                else:
                    complete = False
    return {"counters": dict(totals), "all_counters_reported": complete}


def aggregate_runs(runs):
    """Reproducible descriptive totals, separating retention and repeat probes."""
    groups = []
    for case in CASES:
        for arm in ("current", "elision"):
            selected = [r for r in runs if r["case"] == case and r["arm"] == arm]
            if selected:
                groups.append(aggregate_group(selected, case, arm))
    return groups


def aggregate_group(runs, case, arm):
    complete = all(r["usage"].get("all_counters_reported", False) for r in runs)
    usage = {
        key: sum(r["usage"]["counters"][key] for r in runs)
        if complete and all(key in r["usage"]["counters"] for r in runs)
        else None
        for key in (
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "reasoning_tokens",
            "total_tokens",
        )
    }
    phases = [r["phases"] for r in runs]
    return {
        "case": case,
        "arm": arm,
        "sessions": len(runs),
        "retention_correct": sum(bool(p) and p[0]["exact_facts"] for p in phases),
        "retention_unknown": sum(bool(p) and p[0]["reported_unknown"] for p in phases),
        "repeat_correct": sum(len(p) > 1 and p[1]["exact_facts"] for p in phases),
        "repeat_unknown": sum(len(p) > 1 and p[1]["reported_unknown"] for p in phases),
        "correct_answers": sum(p["exact_facts"] for r in runs for p in r["phases"]),
        "unknown_answers": sum(
            p["reported_unknown"] for r in runs for p in r["phases"]
        ),
        "submissions": sum(len(r["phases"]) for r in runs),
        "median_wall_seconds": statistics.median(r["wall_seconds"] for r in runs),
        "summary_calls": sum(r["summary_calls"] for r in runs),
        "ordinary_calls": sum(r["ordinary_calls"] for r in runs),
        "reread_calls": sum(r["reread_calls"] for r in runs),
        "usage": usage,
        "uncached_input_tokens": usage["input_tokens"] - usage["cached_tokens"]
        if usage["input_tokens"] is not None and usage["cached_tokens"] is not None
        else None,
    }


def public_report(report):
    """Publish numeric evidence; keep local workspace pointers outside the report."""
    return {
        **{
            key: value
            for key, value in report.items()
            if key != "private_evidence_root"
        },
        "aggregates": aggregate_runs(report["runs"]),
    }


def reanalyze(report, *, evidence_root=None):
    """Rescore original private synthetic sessions; make no provider calls."""
    location = evidence_root or report.get("private_evidence_root")
    if location is None:
        raise ValueError(
            "Specify --evidence-root with the directory printed by the live run"
        )
    root = Path(location)
    for run in report["runs"]:
        rescore_run(root, run)
    return public_report(
        {
            **report,
            "analysis_driver_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "analysis": "Rescored each original submission from its own saved user boundary; aggregates computed by the current driver. Live calls, histories, timing and token counters were not rerun or modified.",
        }
    )


def rescore_run(root, run):
    pair = run["seed"] - 9100
    paths = list(
        (root / f"{pair}-{run['case']}-{run['arm']}" / "sessions").glob("*.jsonl")
    )
    if len(paths) != 1 or len(run["phases"]) != 2 or run["failure"]:
        raise ValueError(
            "reanalyze requires one complete two-submission synthetic session"
        )
    messages = (
        NativeSessionTree.open(paths[0], strict=True).build_coding_context().messages
    )
    users = [i for i, m in enumerate(messages) if isinstance(m, AgentUserMessage)]
    if len(users) < 2:
        raise ValueError("saved session has no two-submission boundary")
    code, release = fixture_facts(run["seed"])
    expected = (
        {"release": release, "mode": "dry-run"}
        if run["case"] == "bulk"
        else {"recovery_code": code}
    )
    for phase, end in zip(run["phases"], (users[-1], len(messages)), strict=True):
        snapshot = SimpleNamespace(
            messages=messages[:end],
            compaction_count=phase["compaction_count"],
            preparation_failure=True if phase["preparation_refused"] else None,
            provider_failure=True if phase["provider_failed"] else None,
        )
        score = phase_result(snapshot, expected)
        if any(score[k] != phase[k] for k in ("exact_facts", "reported_unknown")):
            raise ValueError(
                "original evidence scoring changed; inspect before publishing"
            )
        phase.update(score)
    run["scoring_verified_from_saved_session"] = True


def measure(root, arm, case, seed):
    tree, expected, question = fixture(root, case, seed)
    raw_before = tree.path.read_bytes()
    isolate(root / "state")
    client = CountingSseClient(UrllibSseHTTPClient())
    provider = ObservedProvider(client)
    read = FixtureRead()
    original = _RequestPreparationEffects._request_values

    def projected(self, *args, **kwargs):
        request = original(self, *args, **kwargs)
        return elide(request) if arm == "elision" else request

    start = time.perf_counter()
    phases = []
    failure = None
    try:
        with (
            patch.object(_RequestPreparationEffects, "_request_values", projected),
            open_product_session(
                workspace=root,
                session_path=tree.path,
                provider=provider,
                settings=manager(root, case),
                tools={"read": read} if case == "reread" else {},
                resources=RESOURCES,
                load_context_files=False,
            ) as session,
        ):
            for index, prompt in enumerate(
                (
                    question,
                    "Repeat the exact same JSON answer. Recover it if needed; never guess.",
                )
            ):
                provider.phase = index
                snapshot = session.submit(prompt)
                phases.append(phase_result(snapshot, expected))
                if snapshot.preparation_failure or snapshot.provider_failure:
                    failure = "admission_or_provider_failed"
                    break
    except Exception as exc:  # noqa: BLE001 — export type only, never auth/diagnostics
        failure = type(exc).__name__
    raw_after = tree.path.read_bytes()
    strict_reopen = True
    try:
        NativeSessionTree.open(tree.path, strict=True)
    except Exception:  # noqa: BLE001 — numeric evidence only
        strict_reopen = False
    return {
        "arm": arm,
        "case": case,
        "seed": seed,
        "wall_seconds": time.perf_counter() - start,
        "failure": failure,
        "phases": phases,
        "requests": provider.rows,
        "usage": usage_totals(provider.rows),
        "summary_calls": sum(row["kind"] == "summary" for row in provider.rows),
        "ordinary_calls": sum(row["kind"] == "ordinary" for row in provider.rows),
        "reread_calls": read.calls,
        "tool_errors": read.errors,
        "original_history_preserved": raw_after.startswith(raw_before),
        "no_elision_persisted": STUB.encode() not in raw_after,
        "strict_reopen": strict_reopen,
        "all_exact": failure is None
        and len(phases) == 2
        and all(p["exact_facts"] for p in phases),
        **client.report(),
    }


def theme_hashes():
    return [
        hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        for path in (
            Path.home() / ".pipy/settings.json",
            Path.home() / ".local/state/pipy/native-theme.json",
        )
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=4)
    parser.add_argument(
        "--analyze-existing",
        type=Path,
        help="original private report; rescore and aggregate its synthetic session trees without provider calls",
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        help="private synthetic sessions directory for reanalysis; legacy private report location is used if omitted",
    )
    args = parser.parse_args()
    if args.pairs < 1:
        parser.error("--pairs must be positive")
    if args.analyze_existing is not None:
        original_report = json.loads(args.analyze_existing.read_text())
        if (
            args.evidence_root is None
            and "private_evidence_root" not in original_report
        ):
            parser.error(
                "--evidence-root is required for a public report; use the directory printed by its live run"
            )
        report = reanalyze(original_report, evidence_root=args.evidence_root)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        args.report.chmod(0o600)
        return 0
    root = Path(tempfile.mkdtemp(prefix="pipy-context-elision-"))
    print(f"Private synthetic session evidence: {root}", flush=True)
    before = theme_hashes()
    report = {
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "provider": "openai-codex",
        "model": "gpt-6.1-sol",
        "effort": "low",
        "transport": "sse",
        "platform": platform.platform(),
        "fixture_version": 1,
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "context_ceiling": CEILING,
        "reserve": RESERVE,
        "soft_fraction": SOFT_FRACTION,
        "protected_recent_cycles": RECENT_CYCLES,
        "minimum_result_characters": MIN_CHARS,
        "pairs": args.pairs,
        "metric_definitions": {
            "usage": "Allowlisted actual provider counters across every summary and ordinary attempt; cached_tokens is a subset of input_tokens for this provider. Missing counters remain unavailable.",
            "summary_calls": "Prepared private summary operations; HTTP counts and per-operation attempts report retries separately.",
            "wall_seconds": "Session opening, two submissions and strict reopen; excludes fixture preparation.",
            "exact_facts": "Strict parsed JSON equality with deterministic synthetic ground truth; no judge or relaxed parsing.",
            "elision": "Projection before admission: successful read bodies above 2048 characters, except last two complete cycles; trigger at 60% estimated input allowance. Original canonical history still supplies summary input.",
        },
        "runs": [],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    for pair in range(args.pairs):
        order = ("current", "elision") if pair % 2 == 0 else ("elision", "current")
        for case in CASES:
            for arm in order:
                print(f"pair={pair + 1} case={case} arm={arm}", flush=True)
                result = measure(root / f"{pair}-{case}-{arm}", arm, case, 9100 + pair)
                report["runs"].append(result)
                report["global_theme_unchanged"] = theme_hashes() == before
                args.report.write_text(
                    json.dumps(public_report(report), indent=2) + "\n"
                )
                args.report.chmod(0o600)
                print(
                    f"exact={result['all_exact']} summary={result['summary_calls']} "
                    f"ordinary={result['ordinary_calls']} failure={result['failure']}",
                    flush=True,
                )
                if result["failure"]:
                    return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
