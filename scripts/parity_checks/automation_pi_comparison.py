"""Deterministic Pi-vs-pipy session-event comparison harness.

Runs the SAME headless workflow in the local Pi reference and in pipy, using
deterministic offline providers on both sides, normalizes volatile fields (ids,
timestamps, cwd temp paths, token counts) and the streaming-delta granularity,
and asserts the two implementations agree on the observable session semantics:

- the normalized session-event order and type discriminators (agent/turn/
  message lifecycle, role-tagged), treating the assistant streaming-delta run
  as one group (pipy emits the `text_delta` subset it produces; Pi additionally
  frames `text_start`/`text_end` — a documented, allowed divergence);
- the assistant's final text and the concatenation of its streamed text deltas;
- `agent_end` semantics (`willRetry` and the run's message roles);
- durable session-tree reconstruction: pipy's native session tree (the product
  source of truth) rebuilds the same user+assistant conversation the event
  stream describes.

Pi side: `pi_faux_event_driver.mts` drives Pi's real `AgentSession` with the
faux `streamFn` via the local Pi checkout's own `tsx` (so it is offline and
deterministic). pipy side: the real `pipy repl --mode json` CLI with the
deterministic tool-capable fake provider (`--native-model fake-tools`).

Run:

    uv run python scripts/parity_checks/automation_pi_comparison.py --json

Set `PI_MONO_DIR` to the Pi checkout (default `/Users/jochen/src/pi-mono`). When
Pi cannot be driven in this environment (checkout/deps/node missing), the Pi leg
is reported as skipped with the reason rather than silently passing; the
deterministic pipy conformance gate (`automation_rpc_conformance.py`) remains
the hard gate per `docs/automation-rpc.md`.

Exits 0 when every executed check passes (and Pi-skip is not a failure), 1
otherwise. No real network/AI calls.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_BOOT = "import sys; from pipy_harness.cli import main; sys.exit(main(sys.argv[1:]))"

_PROMPT = "ROOT"
_REPLY = "SEEN:ROOT"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _pi_mono_dir() -> Path:
    return Path(os.environ.get("PI_MONO_DIR", "/Users/jochen/src/pi-mono"))


def _pi_available(pi_dir: Path) -> tuple[bool, str]:
    if not pi_dir.is_dir():
        return False, f"pi-mono not found at {pi_dir}"
    tsx = pi_dir / "node_modules" / ".bin" / "tsx"
    if not tsx.exists():
        return False, f"pi-mono deps not installed (no {tsx})"
    harness = pi_dir / "packages" / "coding-agent" / "test" / "test-harness.ts"
    if not harness.exists():
        return False, f"pi test harness missing ({harness})"
    return True, "available"


def _drive_pi(pi_dir: Path) -> list[dict]:
    driver = Path(__file__).with_name("pi_faux_event_driver.mts")
    tsx = pi_dir / "node_modules" / ".bin" / "tsx"
    env = dict(os.environ)
    env["PI_MONO_DIR"] = str(pi_dir)
    proc = subprocess.run(
        [str(tsx), str(driver), _PROMPT, _REPLY],
        cwd=str(pi_dir),  # anchor module resolution at the Pi checkout
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=120.0,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"pi driver exit {proc.returncode}: {proc.stderr.decode('utf-8')[:400]}"
        )
    return [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines() if line]


def _drive_pipy(sessions_root: Path | None) -> tuple[list[dict], Path]:
    work = Path(tempfile.mkdtemp())
    env = dict(os.environ)
    env["PIPY_NATIVE_DEFAULTS_PATH"] = str(work / "defaults.json")
    env["PIPY_AUTH_DIR"] = str(work / "auth")
    env["XDG_STATE_HOME"] = str(work / "state")
    env["XDG_CONFIG_HOME"] = str(work / "config")
    env["PIPY_CONFIG_HOME"] = str(work / "config")
    env["PIPY_PROMPT_HISTORY_PATH"] = str(work / "history.json")
    argv = [
        "repl",
        "--cwd",
        str(work / "ws"),
        "--native-provider",
        "fake",
        "--native-model",
        "fake-tools",
        "--mode",
        "json",
    ]
    (work / "ws").mkdir(parents=True, exist_ok=True)
    if sessions_root is not None:
        env["PIPY_NATIVE_SESSIONS_ROOT"] = str(sessions_root)
    else:
        argv.append("--no-session")
    argv.append(_PROMPT)
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT, *argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        timeout=60.0,
    )
    records = [
        json.loads(line)
        for line in proc.stdout.decode("utf-8").splitlines()
        if line
    ]
    # Drop the leading native session header; keep the event stream.
    events = [r for r in records if r.get("type") != "session"]
    return events, work, proc.returncode


def _normalized_sequence(events: list[dict]) -> list[str]:
    seq: list[str] = []
    for event in events:
        etype = event.get("type")
        if etype == "message_update":
            if seq and seq[-1] == "message_update*":
                continue
            seq.append("message_update*")
            continue
        if etype in ("message_start", "message_end"):
            role = event.get("message", {}).get("role")
            seq.append(f"{etype}:{role}")
        else:
            seq.append(str(etype))
    return seq


def _assistant_final_text(events: list[dict]) -> str:
    for event in events:
        if event.get("type") == "message_end" and event.get("message", {}).get(
            "role"
        ) == "assistant":
            return "".join(
                b.get("text", "")
                for b in event["message"].get("content", [])
                if b.get("type") == "text"
            )
    return ""


def _delta_concatenation(events: list[dict]) -> str:
    return "".join(
        event["assistantMessageEvent"].get("delta", "")
        for event in events
        if event.get("type") == "message_update"
        and event.get("assistantMessageEvent", {}).get("type") == "text_delta"
    )


def _agent_end(events: list[dict]) -> dict:
    for event in events:
        if event.get("type") == "agent_end":
            return event
    return {}


def _run_comparison(pi_events: list[dict], pipy_events: list[dict]) -> list[Check]:
    checks: list[Check] = []

    pi_seq = _normalized_sequence(pi_events)
    pipy_seq = _normalized_sequence(pipy_events)
    checks.append(
        Check(
            "event_order_and_discriminators_match",
            pi_seq == pipy_seq,
            f"pi={pi_seq} pipy={pipy_seq}",
        )
    )

    pi_text = _assistant_final_text(pi_events)
    pipy_text = _assistant_final_text(pipy_events)
    checks.append(
        Check(
            "assistant_final_text_matches",
            pi_text == pipy_text and pi_text != "",
            f"pi={pi_text!r} pipy={pipy_text!r}",
        )
    )

    pi_deltas = _delta_concatenation(pi_events)
    pipy_deltas = _delta_concatenation(pipy_events)
    checks.append(
        Check(
            "streamed_deltas_concatenate_to_final_text",
            pi_deltas == pi_text and pipy_deltas == pipy_text and pipy_deltas != "",
            f"pi_deltas={pi_deltas!r} pipy_deltas={pipy_deltas!r}",
        )
    )

    pi_end = _agent_end(pi_events)
    pipy_end = _agent_end(pipy_events)
    pi_roles = [m.get("role") for m in pi_end.get("messages", [])]
    pipy_roles = [m.get("role") for m in pipy_end.get("messages", [])]
    checks.append(
        Check(
            "agent_end_semantics_match",
            pi_end.get("willRetry") == pipy_end.get("willRetry") is False
            and pi_roles == pipy_roles
            and pi_roles == ["user", "assistant"],
            f"pi(willRetry={pi_end.get('willRetry')},roles={pi_roles}) "
            f"pipy(willRetry={pipy_end.get('willRetry')},roles={pipy_roles})",
        )
    )
    return checks


def _check_durable_tree_reconstruction(events: list[dict], sessions_root: Path) -> Check:
    """pipy's native session tree rebuilds the same conversation as the events."""

    from pipy_harness.native.session_tree import NativeSessionTree

    session_files = sorted(sessions_root.glob("**/*.jsonl"))
    if not session_files:
        return Check("durable_session_tree_reconstruction", False, "no session file written")
    tree = NativeSessionTree.open(session_files[-1])
    messages = list(tree.build_context().messages)
    roles = [type(m).__name__ for m in messages]
    assistant_texts = [
        m.content for m in messages if type(m).__name__ == "AssistantMessage"
    ]
    end = _agent_end(events)
    event_roles = [m.get("role") for m in end.get("messages", [])]
    ok = (
        roles == ["UserMessage", "AssistantMessage"]
        and event_roles == ["user", "assistant"]
        and assistant_texts == [_REPLY]
    )
    return Check(
        "durable_session_tree_reconstruction",
        ok,
        f"tree_roles={roles} assistant_texts={assistant_texts} event_roles={event_roles}",
    )


def run_checks() -> tuple[list[Check], bool]:
    pi_dir = _pi_mono_dir()
    available, reason = _pi_available(pi_dir)
    if not available:
        return (
            [Check("pi_reference_available", False, f"skipped: {reason}")],
            True,  # skipped, not a hard failure
        )

    pi_events = _drive_pi(pi_dir)
    pipy_events, _, pipy_rc = _drive_pipy(sessions_root=None)
    checks = _run_comparison(pi_events, pipy_events)

    # Durable session-tree reconstruction on the pipy side (product source of
    # truth) with a persistent session.
    sessions_root = Path(tempfile.mkdtemp()) / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    pipy_persistent_events, _, pipy_persistent_rc = _drive_pipy(
        sessions_root=sessions_root
    )
    checks.append(
        _check_durable_tree_reconstruction(pipy_persistent_events, sessions_root)
    )
    # A non-zero pipy exit is a hard failure even if the emitted events looked
    # matching — otherwise a crash after emission would be a false green.
    checks.append(
        Check(
            "pipy_exit_zero",
            pipy_rc == 0 and pipy_persistent_rc == 0,
            f"oneshot_rc={pipy_rc} persistent_rc={pipy_persistent_rc}",
        )
    )
    return checks, False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    checks, skipped = run_checks()
    all_passed = all(c.passed for c in checks)
    # A skipped Pi leg is NOT a pass: report passed=false so it cannot be read as
    # a green comparison. It is non-gating (exit 0) so non-Pi environments are not
    # blocked; a real Pi-vs-pipy mismatch is a hard failure (exit 1).
    passed = all_passed and not skipped
    if args.json:
        report = {
            "passed": passed,
            "skipped": skipped,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks
            ],
        }
        print(json.dumps(report, indent=2))
    else:
        for c in checks:
            print(f"[{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")
        if skipped:
            print("SKIPPED (Pi reference unavailable) — comparison did not run")
        else:
            print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if (all_passed or skipped) else 1


if __name__ == "__main__":
    raise SystemExit(main())
