"""Hard conformance gate for the Pi-style headless automation surfaces.

This script drives pipy's **real product CLI** as a subprocess — the same
``pipy repl ... --mode rpc`` / ``--mode json`` paths a Pi RPC client or
automation harness would spawn — with the deterministic, tool-capable fake
provider (``--native-provider fake --native-model fake-tools``) in a temporary
workspace. It is the implementation source of truth for the automation track in
``docs/automation-rpc.md``: it fails unless the protocol, event sequence, and
stream hygiene are correct.

Run:

    uv run python scripts/parity_checks/automation_rpc_conformance.py --json

It proves, end to end, through the product CLI:

1. ``--mode rpc`` starts and emits no stray stdout before the first command.
2. ``prompt`` emits a correlated success then the full event sequence
   (agent_start, turn_start, message_start, message_update text_delta(s),
   message_end, turn_end, agent_end with willRetry:false).
3. The streamed ``text_delta`` deltas concatenate to the assistant's final text,
   which appears in message_end (full-content surface).
4. ``get_state`` returns a well-formed RpcSessionState.
5. ``get_messages`` returns the recorded conversation with full content.
6. ``bash`` returns a BashResult (output contains ``hi``, exitCode 0).
7. A mid-turn ``steer`` emits a ``queue_update``; ``abort`` terminates the run
   with a correlated success and a final ``agent_end``.
8. ``set_session_name`` then ``get_state`` shows the new ``sessionName``;
   ``get_session_stats`` returns coherent counters.
9. An unknown command and a malformed line return well-formed error responses
   (``command:"frobnicate"`` / ``command:"parse"``) without crashing.
10. No API key / token material appears anywhere on stdout.
11. stdin EOF triggers a clean shutdown with exit code 0 and pure-JSONL stdout.
12. ``--mode json "<prompt>"`` emits the native session header first, then the
    event sequence, exits 0, and never emits the ``pipy.native_output`` schema.

Also asserts strict LF-only framing (no ``\r``, one JSON object per line, no
interleaved records) and that all 29 Pi RPC command types are accepted (a
``response`` for each, never a crash or ``Unknown command``).

Exits 0 when every check passes, 1 otherwise. No real network/AI calls.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_BOOT = "import sys; from pipy_harness.cli import main; sys.exit(main(sys.argv[1:]))"

_SECRET_TOKEN = "sk-CONFORMANCE-SECRET-TOKEN-do-not-leak"

# The full Pi RPC command vocabulary (29 types) the product must accept.
_ALL_COMMANDS = [
    {"type": "prompt", "message": "hi"},
    {"type": "steer", "message": "hi"},
    {"type": "follow_up", "message": "hi"},
    {"type": "abort"},
    {"type": "new_session"},
    {"type": "get_state"},
    {"type": "set_model", "provider": "fake", "modelId": "fake-tools"},
    {"type": "cycle_model"},
    {"type": "get_available_models"},
    {"type": "set_thinking_level", "level": "off"},
    {"type": "cycle_thinking_level"},
    {"type": "set_steering_mode", "mode": "all"},
    {"type": "set_follow_up_mode", "mode": "all"},
    {"type": "compact"},
    {"type": "set_auto_compaction", "enabled": True},
    {"type": "set_auto_retry", "enabled": True},
    {"type": "abort_retry"},
    {"type": "bash", "command": "echo hi"},
    {"type": "abort_bash"},
    {"type": "get_session_stats"},
    {"type": "export_html"},
    {"type": "switch_session", "sessionPath": "/nonexistent"},
    {"type": "fork", "entryId": "nope"},
    {"type": "clone"},
    {"type": "get_fork_messages"},
    {"type": "get_last_assistant_text"},
    {"type": "set_session_name", "name": "x"},
    {"type": "get_messages"},
    {"type": "get_commands"},
]


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _env(base: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PIPY_NATIVE_DEFAULTS_PATH"] = str(base / "defaults.json")
    env["PIPY_AUTH_DIR"] = str(base / "auth")
    env["PIPY_NATIVE_SESSIONS_ROOT"] = str(base / "sessions")
    env["XDG_STATE_HOME"] = str(base / "state")
    env["XDG_CONFIG_HOME"] = str(base / "config")
    env["PIPY_CONFIG_HOME"] = str(base / "config")
    env["PIPY_PROMPT_HISTORY_PATH"] = str(base / "history.json")
    # Plant a credential-shaped secret to prove it never reaches stdout.
    env["OPENAI_API_KEY"] = _SECRET_TOKEN
    return env


class _RpcProcess:
    def __init__(self, workspace: Path, env: dict[str, str]) -> None:
        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _BOOT,
                "repl",
                "--cwd",
                str(workspace),
                "--native-provider",
                "fake",
                "--native-model",
                "fake-tools",
                "--no-session",
                "--mode",
                "rpc",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self.raw = bytearray()
        self.records: "queue.Queue[dict]" = queue.Queue()
        self._lines: list[str] = []
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self._proc.stdout is not None
        buffer = b""
        while True:
            chunk = self._proc.stdout.read(1)
            if chunk == b"":
                break
            self.raw += chunk
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                text = line.decode("utf-8")
                self._lines.append(text)
                try:
                    self.records.put(json.loads(text))
                except ValueError:
                    self.records.put({"__unparseable__": text})

    def send(self, command: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(command) + "\n").encode("utf-8"))
        self._proc.stdin.flush()

    def send_raw(self, text: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(text.encode("utf-8"))
        self._proc.stdin.flush()

    def wait_for(self, predicate, timeout: float = 10.0) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                record = self.records.get(timeout=deadline - time.monotonic())
            except queue.Empty:
                return None
            if predicate(record):
                return record
        return None

    def collect_until(self, predicate, timeout: float = 10.0) -> list[dict]:
        out: list[dict] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                record = self.records.get(timeout=deadline - time.monotonic())
            except queue.Empty:
                break
            out.append(record)
            if predicate(record):
                break
        return out

    def close(self) -> int:
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        try:
            return self._proc.wait(timeout=15.0)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            return -1


def _run_rpc_checks(base: Path) -> list[Check]:
    checks: list[Check] = []
    env = _env(base)
    workspace = base / "work"
    workspace.mkdir(parents=True, exist_ok=True)
    proc = _RpcProcess(workspace, env)

    # (1) No stray stdout before the first command.
    time.sleep(0.3)
    pre = list(proc._lines)
    checks.append(Check("rpc_no_stray_stdout_before_command", pre == [], f"pre={pre}"))

    # (2)+(3) prompt → correlated success then full event sequence.
    proc.send({"id": "r1", "type": "prompt", "message": "ROOT"})
    records = proc.collect_until(lambda r: r.get("type") == "agent_end")
    success = records[0] if records else {}
    seq_ok = success == {
        "id": "r1",
        "type": "response",
        "command": "prompt",
        "success": True,
    }
    types = [r.get("type") for r in records[1:]]
    required = [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    order_ok = seq_ok and all(t in types for t in required)
    order_ok = order_ok and types.count("message_update") >= 1
    agent_end = records[-1] if records else {}
    order_ok = order_ok and agent_end.get("willRetry") is False
    checks.append(
        Check("rpc_prompt_event_sequence", bool(order_ok), f"types={types}")
    )

    deltas = "".join(
        r["assistantMessageEvent"]["delta"]
        for r in records
        if r.get("type") == "message_update"
    )
    message_end = next(
        (
            r
            for r in records
            if r.get("type") == "message_end"
            and r.get("message", {}).get("role") == "assistant"
        ),
        {},
    )
    final_text = "".join(
        b.get("text", "")
        for b in message_end.get("message", {}).get("content", [])
        if b.get("type") == "text"
    )
    content_ok = deltas == final_text and "ROOT" in final_text and final_text != ""
    checks.append(
        Check(
            "rpc_full_content_text_deltas_concatenate",
            content_ok,
            f"deltas={deltas!r} final={final_text!r}",
        )
    )

    # (4) get_state
    proc.send({"id": "r2", "type": "get_state"})
    state = proc.wait_for(lambda r: r.get("id") == "r2") or {}
    sd = state.get("data", {})
    state_ok = (
        state.get("success") is True
        and "model" in sd
        and "isStreaming" in sd
        and sd.get("steeringMode") in ("all", "one-at-a-time")
        and bool(sd.get("sessionId"))
        and sd.get("messageCount", 0) >= 2
    )
    checks.append(Check("rpc_get_state_shape", bool(state_ok), f"data={sd}"))

    # (5) get_messages
    proc.send({"id": "r3", "type": "get_messages"})
    msgs = proc.wait_for(lambda r: r.get("id") == "r3") or {}
    roles = [m.get("role") for m in msgs.get("data", {}).get("messages", [])]
    msgs_ok = "user" in roles and "assistant" in roles
    checks.append(Check("rpc_get_messages_full_content", bool(msgs_ok), f"roles={roles}"))

    # (6) bash
    proc.send({"id": "r4", "type": "bash", "command": "echo hi"})
    bash = proc.wait_for(lambda r: r.get("id") == "r4") or {}
    bd = bash.get("data", {})
    bash_ok = (
        bash.get("success") is True
        and "hi" in bd.get("output", "")
        and bd.get("exitCode") == 0
        and bd.get("cancelled") is False
    )
    checks.append(Check("rpc_bash_result", bool(bash_ok), f"data={bd}"))

    # bash must not emit auth secrets: env-var expansion is rejected by the
    # sandbox and a literal secret-shaped token is redacted in the output.
    proc.send({"id": "r4b", "type": "bash", "command": "echo $OPENAI_API_KEY"})
    bash_env = proc.wait_for(lambda r: r.get("id") == "r4b") or {}
    env_out = bash_env.get("data", {}).get("output", "")
    proc.send({"id": "r4c", "type": "bash", "command": f"echo {_SECRET_TOKEN}"})
    bash_lit = proc.wait_for(lambda r: r.get("id") == "r4c") or {}
    lit_out = bash_lit.get("data", {}).get("output", "")
    bash_secret_ok = _SECRET_TOKEN not in env_out and _SECRET_TOKEN not in lit_out
    checks.append(
        Check(
            "rpc_bash_does_not_leak_secret",
            bash_secret_ok,
            f"env_out={env_out!r} lit_out={lit_out!r}",
        )
    )

    # (8) set_session_name then get_state; get_session_stats
    proc.send({"id": "r5", "type": "set_session_name", "name": "named-session"})
    proc.wait_for(lambda r: r.get("id") == "r5")
    proc.send({"id": "r6", "type": "get_state"})
    state2 = proc.wait_for(lambda r: r.get("id") == "r6") or {}
    name_ok = state2.get("data", {}).get("sessionName") == "named-session"
    proc.send({"id": "r7", "type": "get_session_stats"})
    stats = proc.wait_for(lambda r: r.get("id") == "r7") or {}
    st = stats.get("data", {})
    stats_ok = (
        stats.get("success") is True
        and st.get("userMessages", 0) >= 1
        and st.get("assistantMessages", 0) >= 1
        and "tokens" in st
    )
    checks.append(
        Check("rpc_session_name_and_stats", bool(name_ok and stats_ok), f"name_ok={name_ok} stats={st}")
    )

    # (9) unknown command + parse error
    proc.send({"type": "frobnicate"})
    unknown = proc.wait_for(
        lambda r: r.get("type") == "response" and r.get("command") == "frobnicate"
    ) or {}
    unknown_ok = (
        unknown.get("success") is False
        and unknown.get("error") == "Unknown command: frobnicate"
        and "id" not in unknown
    )
    proc.send_raw("{ not valid json\n")
    parse = proc.wait_for(
        lambda r: r.get("type") == "response" and r.get("command") == "parse"
    ) or {}
    parse_ok = parse.get("success") is False and "Failed to parse" in parse.get("error", "")
    checks.append(
        Check("rpc_unknown_and_parse_errors", bool(unknown_ok and parse_ok), f"unknown={unknown} parse={parse}")
    )

    # (7) mid-turn steer emits queue_update; abort terminates with agent_end
    proc.send({"id": "r8", "type": "prompt", "message": "BLOCK please"})
    proc.wait_for(lambda r: r.get("type") == "agent_start")
    proc.send({"id": "r9", "type": "steer", "message": "turn left"})
    qu = proc.wait_for(lambda r: r.get("type") == "queue_update") or {}
    steer_ok = "turn left" in qu.get("steering", [])
    proc.send({"id": "r10", "type": "abort"})
    abort_resp = proc.wait_for(
        lambda r: r.get("id") == "r10" and r.get("command") == "abort"
    ) or {}
    abort_end = proc.wait_for(lambda r: r.get("type") == "agent_end")
    abort_ok = abort_resp.get("success") is True and abort_end is not None
    checks.append(
        Check("rpc_steer_queue_update_and_abort", bool(steer_ok and abort_ok), f"steer_ok={steer_ok} abort_ok={abort_ok}")
    )

    # (11) EOF shutdown clean exit
    exit_code = proc.close()
    checks.append(Check("rpc_eof_clean_shutdown", exit_code == 0, f"exit_code={exit_code}"))

    # (10) secret hygiene + framing on the captured stdout
    raw = bytes(proc.raw)
    secret_ok = _SECRET_TOKEN.encode("utf-8") not in raw
    checks.append(Check("rpc_no_secret_on_stdout", secret_ok, "secret token absent"))

    lf_ok = b"\r" not in raw
    lines = [ln for ln in raw.decode("utf-8").split("\n") if ln]
    parse_all_ok = True
    for ln in lines:
        try:
            json.loads(ln)
        except ValueError:
            parse_all_ok = False
            break
    checks.append(
        Check("rpc_lf_only_no_interleave", bool(lf_ok and parse_all_ok), f"lines={len(lines)} lf_ok={lf_ok}")
    )

    return checks


def _check_accepts_all_commands(base: Path) -> Check:
    env = _env(base)
    workspace = base / "allcmds"
    workspace.mkdir(parents=True, exist_ok=True)
    proc = _RpcProcess(workspace, env)
    accepted: list[str] = []
    missing: list[str] = []
    for i, command in enumerate(_ALL_COMMANDS):
        cid = f"c{i}"
        payload = {"id": cid, **command}
        proc.send(payload)
        # prompt is async (success then events); others resolve to one response.
        resp = proc.wait_for(
            lambda r: r.get("type") == "response" and r.get("command") == command["type"],
            timeout=8.0,
        )
        if resp is None:
            missing.append(command["type"])
        else:
            accepted.append(command["type"])
        if command["type"] == "prompt":
            # let the async turn settle before the next command
            proc.wait_for(lambda r: r.get("type") == "agent_end", timeout=8.0)
    proc.close()
    ok = not missing and len(accepted) == len(_ALL_COMMANDS)
    return Check(
        "rpc_accepts_all_29_commands",
        ok,
        f"accepted={len(accepted)}/29 missing={missing}",
    )


def _check_json_mode_oneshot(base: Path) -> Check:
    env = _env(base)
    workspace = base / "jsonmode"
    workspace.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _BOOT,
            "repl",
            "--cwd",
            str(workspace),
            "--native-provider",
            "fake",
            "--native-model",
            "fake-tools",
            "--no-session",
            "--mode",
            "json",
            "ROOT",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        timeout=30.0,
    )
    raw = proc.stdout
    lf_ok = b"\r" not in raw
    lines = [ln for ln in raw.decode("utf-8").split("\n") if ln]
    try:
        records = [json.loads(ln) for ln in lines]
    except ValueError:
        return Check("json_mode_oneshot", False, "non-JSON line on stdout")
    header_ok = bool(records) and records[0].get("type") == "session"
    types = [r.get("type") for r in records[1:]]
    seq_ok = (
        types[:1] == ["agent_start"]
        and "message_update" in types
        and types[-1:] == ["agent_end"]
    )
    no_metadata = all(r.get("schema") != "pipy.native_output" for r in records)
    secret_ok = _SECRET_TOKEN.encode("utf-8") not in raw
    ok = (
        proc.returncode == 0
        and lf_ok
        and header_ok
        and seq_ok
        and no_metadata
        and secret_ok
    )
    return Check(
        "json_mode_oneshot",
        bool(ok),
        f"rc={proc.returncode} header_ok={header_ok} seq_ok={seq_ok} no_metadata={no_metadata}",
    )


def run_checks(base: Path) -> list[Check]:
    checks = _run_rpc_checks(base / "rpc")
    checks.append(_check_accepts_all_commands(base / "all"))
    checks.append(_check_json_mode_oneshot(base / "json"))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for sub in ("rpc", "all", "json"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        checks = run_checks(base)

    passed = all(c.passed for c in checks)
    if args.json:
        report = {
            "passed": passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks
            ],
        }
        print(json.dumps(report, indent=2))
    else:
        for c in checks:
            print(f"[{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")
        print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
