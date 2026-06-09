"""Deterministic Pi-vs-pipy session-tree comparison harness.

Runs the SAME canonical session-tree workflow against the local Pi reference's
REAL ``SessionManager`` and against pipy's native product session tree, then
asserts the two implementations agree on the observable session semantics after
normalizing volatile ids/timestamps/paths:

- session identity via the persisted session **name**;
- **active branch / leaf** behavior: selecting the ALT vs MAIN leaf yields the
  corresponding root->leaf user-message chain;
- the set of root->leaf user-message **chains** (the sibling branches created by
  a ``/tree``-style branch back to an earlier user message);
- **fork** decisions: the fork records a parent session and carries the active
  (ALT) branch;
- **durable reconstruction**: reopening the written session file rebuilds the
  name and the default-leaf chain from disk.

The scenario:

    create -> user ROOT -> user MAIN -> branch back to ROOT -> user ALT
    -> name "compare-tree" -> fork the active (ALT) branch

Pi side: ``pi_session_tree_driver.mts`` drives Pi's real ``SessionManager`` via
the local Pi checkout's own ``tsx`` (offline, deterministic). pipy side: the
real ``NativeSessionTree`` product store, writing and re-reading session files.

The pipy leg is a HARD gate (it asserts the product session files on disk match
the expected normalized structure — not a helper-only check). When Pi cannot be
driven in this environment (checkout/deps/node missing), the Pi leg is reported
as skipped with the reason rather than silently passing.

Run:

    uv run python scripts/parity_checks/session_tree_pi_comparison.py --json

Set ``PI_MONO_DIR`` to the Pi checkout (default ``/Users/jochen/src/pi-mono``).
Exits 0 when every executed check passes (Pi-skip is not a failure), 1 otherwise.
No real network/AI calls.
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

from pipy_harness.native.session_tree import (
    MessageEntry,
    NativeSessionTree,
)
from pipy_harness.native.tools.messages import AssistantMessage, UserMessage

_EXPECTED = {
    "name": "compare-tree",
    "leafUserChains": [["ROOT", "ALT"], ["ROOT", "MAIN"]],
    "activeAltChain": ["ROOT", "ALT"],
    "activeMainChain": ["ROOT", "MAIN"],
    "forkParentRecorded": True,
    "forkHasAltChain": True,
    "reopenLeafChain": ["ROOT", "ALT"],
}


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
    sm = pi_dir / "packages" / "coding-agent" / "src" / "core" / "session-manager.ts"
    if not sm.exists():
        return False, f"pi session-manager missing ({sm})"
    return True, "available"


def _drive_pi(pi_dir: Path) -> dict:
    driver = Path(__file__).with_name("pi_session_tree_driver.mts")
    tsx = pi_dir / "node_modules" / ".bin" / "tsx"
    work = Path(tempfile.mkdtemp())
    session_dir = work / "pi-sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    cwd = work / "ws"
    cwd.mkdir(parents=True, exist_ok=True)
    fork_cwd = work / "ws-fork"
    fork_cwd.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PI_MONO_DIR"] = str(pi_dir)
    proc = subprocess.run(
        [str(tsx), str(driver), str(session_dir), str(cwd), str(fork_cwd)],
        cwd=str(pi_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=120.0,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"pi driver exit {proc.returncode}: {proc.stderr.decode('utf-8')[:400]}"
        )
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    return json.loads(lines[-1])


def _user_chain(tree: NativeSessionTree, leaf_id: str | None) -> list[str]:
    return [
        e.message.content
        for e in tree.get_branch(leaf_id)
        if isinstance(e, MessageEntry) and isinstance(e.message, UserMessage)
    ]


def _leaf_user_chains(tree: NativeSessionTree) -> list[list[str]]:
    leaves: list[str] = []

    def walk(node: object) -> None:
        children = getattr(node, "children", [])
        if not children:
            leaves.append(node.entry.id)  # type: ignore[attr-defined]
            return
        for child in children:
            walk(child)

    for root in tree.get_tree():
        walk(root)
    chains = [_user_chain(tree, leaf) for leaf in leaves]
    chains = [c for c in chains if c]
    chains.sort(key=lambda c: json.dumps(c))
    return chains


def _drive_pipy() -> dict:
    work = Path(tempfile.mkdtemp())
    session_dir = work / "store" / "proj"
    cwd = work / "ws"
    cwd.mkdir(parents=True, exist_ok=True)
    fork_cwd = work / "ws-fork"
    fork_cwd.mkdir(parents=True, exist_ok=True)

    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    # Mirror the Pi driver exactly: interleave assistant replies and branch back
    # to the assistant after ROOT (the parent of the MAIN user turn).
    tree.append_message(UserMessage(content="ROOT"))
    a1 = tree.append_message(AssistantMessage(content="SEEN:ROOT"))
    tree.append_message(UserMessage(content="MAIN"))
    a2 = tree.append_message(AssistantMessage(content="SEEN:ROOT,MAIN"))
    tree.set_leaf(a1.id)
    tree.append_message(UserMessage(content="ALT"))
    a3 = tree.append_message(AssistantMessage(content="SEEN:ROOT,ALT"))

    tree.set_leaf(a3.id)
    active_alt = [
        m.content
        for m in tree.build_context().messages
        if isinstance(m, UserMessage)
    ]
    tree.set_leaf(a2.id)
    active_main = [
        m.content
        for m in tree.build_context().messages
        if isinstance(m, UserMessage)
    ]

    tree.set_leaf(a3.id)
    tree.append_session_info("compare-tree")
    leaf_chains = _leaf_user_chains(tree)
    session_file = tree.path
    assert session_file is not None

    tree.set_leaf(a3.id)
    fork = NativeSessionTree.fork_from(
        session_file, fork_cwd, leaf_id=a3.id, session_dir=session_dir
    )
    assert fork.path is not None
    fork_parent = fork.get_header().parent_session is not None
    fork_chains = _leaf_user_chains(fork)

    reopened = NativeSessionTree.open(session_file)
    reopen_chain = _user_chain(reopened, reopened.get_leaf_id())

    return {
        "name": reopened.name,
        "leafUserChains": leaf_chains,
        "activeAltChain": active_alt,
        "activeMainChain": active_main,
        "forkParentRecorded": fork_parent,
        "forkHasAltChain": ["ROOT", "ALT"] in fork_chains,
        "reopenLeafChain": reopen_chain,
    }


def run_checks() -> tuple[list[Check], bool]:
    checks: list[Check] = []

    # --- pipy leg: HARD gate against the product session files ------------
    pipy = _drive_pipy()
    for key, expected in _EXPECTED.items():
        checks.append(
            Check(
                f"pipy_{key}",
                pipy.get(key) == expected,
                f"got={pipy.get(key)!r} expected={expected!r}",
            )
        )

    # --- Pi leg: compare the same workflow against the real Pi reference --
    pi_dir = _pi_mono_dir()
    available, reason = _pi_available(pi_dir)
    if not available:
        # Pi-skip is not a hard failure: mark the marker check passed so the
        # overall result still reflects only the pipy product-path leg.
        checks.append(
            Check("pi_reference_available", True, f"skipped: {reason}")
        )
        return checks, True

    pi = _drive_pi(pi_dir)
    for key in _EXPECTED:
        checks.append(
            Check(
                f"pi_vs_pipy_{key}",
                pi.get(key) == pipy.get(key),
                f"pi={pi.get(key)!r} pipy={pipy.get(key)!r}",
            )
        )
    return checks, False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    try:
        checks, skipped = run_checks()
    except RuntimeError as exc:
        # The Pi driver was invoked (Pi passed availability) but crashed. That
        # is a real mismatch (driver/API drift), NOT an environment skip —
        # surface it as a failed check so the gate cannot pass without actually
        # comparing against Pi.
        pipy = _drive_pipy()
        checks = [
            Check(
                f"pipy_{key}",
                pipy.get(key) == expected,
                f"got={pipy.get(key)!r} expected={expected!r}",
            )
            for key, expected in _EXPECTED.items()
        ]
        checks.append(Check("pi_driver_error", False, f"pi driver failed: {exc}"))
        skipped = False

    passed = all(c.passed for c in checks)
    if args.json:
        report = {
            "passed": passed,
            "skipped": skipped,
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
    sys.exit(main())
