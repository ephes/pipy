"""Hard conformance gate for Pi-style native product session trees.

This script drives pipy's real tool-loop product runtime with the deterministic
fake provider in a temporary workspace and state root, then inspects the native
product session artifacts. It is the implementation source of truth for the
session-tree track described in ``docs/session-tree.md``: it fails unless the
full Pi-style workflow works through the product runtime (not a bypass).

Run:

    uv run python scripts/parity_checks/session_tree_conformance.py --json

It proves, end to end:

1. a native raw session tree file is created under the native product store;
2. the file contains raw conversation entries needed for product resume;
3. root + sibling branches are created through ``/tree``;
4. provider-visible context follows only the active branch;
5. ``/session`` reports safe native-session status;
6. ``/name`` persists a session name;
7. ``/new`` starts a fresh native product session;
8. ``/resume`` opens a previous native session and supports rename/delete/named;
9. startup equivalents for ``-c``/``-r``/``--no-session``/``--session``/``--fork``;
10. ``/fork`` creates a new native session from an earlier user message;
11. ``/clone`` duplicates the current active branch into a new native session;
12. ``/compact`` appends a durable compaction entry honored on rebuild;
13. branch-summary entries are created and used when switching branches;
14. reloading from the native file reconstructs tree/branch/labels/name/context;
15. the ``pipy-session`` archive still works as metadata only (privacy), and is
    not used as the product session source.

Canonical scenario (docs/session-tree.md):

    /name conformance-tree
    ROOT  -> SEEN:ROOT
    MAIN  -> SEEN:ROOT,MAIN
    /tree select MAIN user message; edit MAIN -> ALT; submit -> SEEN:ROOT,ALT

Assertions: native sibling paths ROOT->SEEN:ROOT->MAIN->SEEN:ROOT,MAIN and
ROOT->SEEN:ROOT->ALT->SEEN:ROOT,ALT; the ALT request contains ROOT/ALT not
MAIN; continuing the MAIN branch contains ROOT/MAIN not ALT.

Exits 0 when every check passes, 1 otherwise. No real network/AI calls.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import CapturePolicy, HarnessStatus, RunRequest
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.session_tree import (
    LabelEntry,
    MessageEntry,
    NativeSessionTree,
)
from pipy_harness.native.session_tree_commands import (
    list_native_sessions,
    resolve_startup_session,
)
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    UserMessage,
)
from pipy_harness.native.tool_loop_session import NativeToolReplSession


class _SeenProvider:
    """Deterministic provider echoing the active-branch user messages it sees."""

    name = "fake"
    supports_tool_calls = True
    model_id = "fake-native-bootstrap"

    def __init__(self) -> None:
        self.requests: list[tuple[str, ...]] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        users = tuple(
            m.content for m in request.messages if isinstance(m, UserMessage)
        )
        self.requests.append(users)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="SEEN:" + ",".join(users),
            tool_calls=(),
        )


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _drive(
    tree: NativeSessionTree, cwd: Path, script: str, provider: _SeenProvider | None = None
) -> _SeenProvider:
    provider = provider or _SeenProvider()
    session = NativeToolReplSession(provider=provider, native_session=tree)
    session.run(
        workspace_root=cwd,
        input_stream=io.StringIO(script),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return provider


def _drive_capture(
    tree: NativeSessionTree, cwd: Path, script: str
) -> tuple[_SeenProvider, str]:
    provider = _SeenProvider()
    session = NativeToolReplSession(provider=provider, native_session=tree)
    err = io.StringIO()
    session.run(
        workspace_root=cwd,
        input_stream=io.StringIO(script),
        output_stream=io.StringIO(),
        error_stream=err,
    )
    return provider, err.getvalue()


def _branch_user_contents(tree: NativeSessionTree, leaf_id: str) -> list[str]:
    return [
        e.message.content
        for e in tree.get_branch(leaf_id)
        if isinstance(e, MessageEntry) and isinstance(e.message, UserMessage)
    ]


def _assistant_leaf(tree: NativeSessionTree, text: str) -> str | None:
    for e in tree.get_entries():
        if (
            isinstance(e, MessageEntry)
            and isinstance(e.message, AssistantMessage)
            and e.message.content == text
        ):
            return e.id
    return None


def run_checks(state_root: Path, session_dir_root: Path) -> list[Check]:
    checks: list[Check] = []

    # ----- Canonical scenario through the real runtime -----------------
    cwd = session_dir_root / "ws"
    cwd.mkdir(parents=True, exist_ok=True)
    sessions_dir = state_root / "native-sessions" / "canonical"
    tree = NativeSessionTree.create(cwd, session_dir=sessions_dir)
    provider, err = _drive_capture(
        tree,
        cwd,
        "\n".join(
            [
                "/name conformance-tree",
                "ROOT",
                "MAIN",
                "/tree select 3",
                "ALT",
                "/tree select 4",
                "CONT",
                "/session",
                "/exit",
                "",
            ]
        ),
    )

    # 1. native raw session tree file created
    checks.append(
        Check(
            "native_file_created",
            tree.path is not None and tree.path.exists(),
            f"path={tree.path}",
        )
    )

    body = tree.path.read_text(encoding="utf-8") if tree.path else ""
    # 2. raw conversation entries present
    raw_ok = all(s in body for s in ("ROOT", "MAIN", "ALT", "SEEN:ROOT"))
    checks.append(Check("raw_entries_present", raw_ok, "raw user/assistant text in file"))

    # 3. native sibling branches exist
    reopened = NativeSessionTree.open(tree.path)
    main_leaf = _assistant_leaf(reopened, "SEEN:ROOT,MAIN")
    alt_leaf = _assistant_leaf(reopened, "SEEN:ROOT,ALT")
    main_path = _branch_user_contents(reopened, main_leaf) if main_leaf else []
    alt_path = _branch_user_contents(reopened, alt_leaf) if alt_leaf else []
    sibling_ok = (
        main_leaf is not None
        and alt_leaf is not None
        and main_path == ["ROOT", "MAIN"]
        and alt_path == ["ROOT", "ALT"]
    )
    checks.append(
        Check(
            "native_sibling_branches",
            sibling_ok,
            f"main={main_path} alt={alt_path}",
        )
    )

    # 4. branch-only provider context
    alt_reqs = [u for u in provider.requests if "ALT" in u]
    cont_reqs = [u for u in provider.requests if "CONT" in u]
    branch_ctx_ok = (
        bool(alt_reqs)
        and all("ROOT" in u and "MAIN" not in u for u in alt_reqs)
        and bool(cont_reqs)
        and all("ROOT" in u and "MAIN" in u and "ALT" not in u for u in cont_reqs)
    )
    checks.append(
        Check(
            "branch_only_context",
            branch_ctx_ok,
            f"alt={alt_reqs} cont={cont_reqs}",
        )
    )

    # 5. /session reports safe status
    checks.append(
        Check(
            "session_status",
            "conformance-tree" in err and tree.session_id[:8] in err,
            "/session status line present",
        )
    )

    # 6. /name persisted
    checks.append(
        Check("name_persisted", reopened.name == "conformance-tree", f"name={reopened.name}")
    )

    # ----- /new starts a fresh native session --------------------------
    new_cwd = session_dir_root / "ws_new"
    new_cwd.mkdir(parents=True, exist_ok=True)
    new_dir = state_root / "native-sessions" / "newcmd"
    new_tree = NativeSessionTree.create(new_cwd, session_dir=new_dir)
    first_id = new_tree.session_id
    _drive(new_tree, new_cwd, "\n".join(["hello", "/new", "world", "/exit", ""]))
    files_after_new = list(new_dir.glob("*.jsonl"))
    checks.append(
        Check(
            "new_session",
            len(files_after_new) == 2,
            f"files={len(files_after_new)} first={first_id[:8]}",
        )
    )

    # 7. /fork from an earlier user message -> new file with parentSession
    fork_cwd = session_dir_root / "ws_fork"
    fork_cwd.mkdir(parents=True, exist_ok=True)
    fork_dir = state_root / "native-sessions" / "forkcmd"
    fork_tree = NativeSessionTree.create(fork_cwd, session_dir=fork_dir)
    _drive(fork_tree, fork_cwd, "\n".join(["one", "two", "/fork 1", "/exit", ""]))
    fork_files = [f for f in fork_dir.glob("*.jsonl") if f != fork_tree.path]
    fork_ok = bool(fork_files) and "parentSession" in fork_files[0].read_text(
        encoding="utf-8"
    )
    checks.append(Check("fork_command", fork_ok, f"forked={[f.name for f in fork_files]}"))

    # 8. /clone duplicates the active branch -> new file
    clone_cwd = session_dir_root / "ws_clone"
    clone_cwd.mkdir(parents=True, exist_ok=True)
    clone_dir = state_root / "native-sessions" / "clonecmd"
    clone_tree = NativeSessionTree.create(clone_cwd, session_dir=clone_dir)
    _drive(clone_tree, clone_cwd, "\n".join(["aa", "bb", "/clone", "/exit", ""]))
    clone_files = [f for f in clone_dir.glob("*.jsonl") if f != clone_tree.path]
    clone_ok = bool(clone_files) and all(
        s in clone_files[0].read_text(encoding="utf-8") for s in ("aa", "bb")
    )
    checks.append(Check("clone_command", clone_ok, f"cloned={[f.name for f in clone_files]}"))

    # 9. durable compaction entry honored on rebuild
    comp_cwd = session_dir_root / "ws_comp"
    comp_cwd.mkdir(parents=True, exist_ok=True)
    comp_dir = state_root / "native-sessions" / "compcmd"
    comp_tree = NativeSessionTree.create(comp_cwd, session_dir=comp_dir)
    _drive(
        comp_tree,
        comp_cwd,
        "\n".join(["a", "b", "c", "d", "/compact", "e", "/exit", ""]),
    )
    comp_reopened = NativeSessionTree.open(comp_tree.path)
    has_compaction = any(
        e.type == "compaction" for e in comp_reopened.get_entries()
    )
    rebuilt = comp_reopened.build_context().messages
    summary_in_ctx = any(
        isinstance(m, UserMessage) and "compacted" in m.content.lower()
        for m in rebuilt
    )
    checks.append(
        Check(
            "durable_compaction",
            has_compaction and summary_in_ctx,
            f"compaction_entry={has_compaction} summary_in_ctx={summary_in_ctx}",
        )
    )

    # 9b. fork of a compacted branch preserves the retained boundary
    cfork_cwd = session_dir_root / "ws_cfork"
    cfork_cwd.mkdir(parents=True, exist_ok=True)
    cfork_dir = state_root / "native-sessions" / "cforkcmd"
    cfork_tree = NativeSessionTree.create(cfork_cwd, session_dir=cfork_dir)
    _drive(
        cfork_tree,
        cfork_cwd,
        "\n".join(["a", "b", "c", "d", "/compact", "e", "/clone", "/exit", ""]),
    )
    cclone_files = [f for f in cfork_dir.glob("*.jsonl") if f != cfork_tree.path]
    cfork_ok = False
    if cclone_files:
        cloned = NativeSessionTree.open(cclone_files[0])
        cloned_users = [
            m.content
            for m in cloned.build_context().messages
            if isinstance(m, UserMessage)
        ]
        # The most recent retained user turns (kept across compaction) survive
        # the clone; the oldest dropped turn does not reappear.
        cfork_ok = "d" in cloned_users and "e" in cloned_users and "a" not in cloned_users
    checks.append(
        Check(
            "compacted_fork_preserves_kept",
            cfork_ok,
            f"cloned_files={len(cclone_files)}",
        )
    )

    # 10. branch summary created + used in context
    bs_cwd = session_dir_root / "ws_bs"
    bs_cwd.mkdir(parents=True, exist_ok=True)
    bs_dir = state_root / "native-sessions" / "bscmd"
    bs_tree = NativeSessionTree.create(bs_cwd, session_dir=bs_dir)
    _drive(
        bs_tree,
        bs_cwd,
        "\n".join(["ROOT", "MAIN", "/tree select 1 summarize", "ALT", "/exit", ""]),
    )
    bs_reopened = NativeSessionTree.open(bs_tree.path)
    has_summary = any(
        e.type == "branch_summary" for e in bs_reopened.get_entries()
    )
    summary_used = any(
        isinstance(m, UserMessage) and "abandoned" in m.content.lower()
        for m in bs_reopened.build_context().messages
    )
    checks.append(
        Check(
            "branch_summary",
            has_summary and summary_used,
            f"summary_entry={has_summary} used={summary_used}",
        )
    )

    # 11. /resume open + rename + delete + named-only listing
    resume_cwd = session_dir_root / "ws_resume"
    resume_cwd.mkdir(parents=True, exist_ok=True)
    resume_dir = state_root / "native-sessions" / "resumecmd"
    first = NativeSessionTree.create(resume_cwd, session_dir=resume_dir)
    _drive(first, resume_cwd, "\n".join(["/name alpha", "x", "/exit", ""]))
    second = NativeSessionTree.create(resume_cwd, session_dir=resume_dir)
    third = NativeSessionTree.create(resume_cwd, session_dir=resume_dir)
    third_id = third.session_id
    # From `second`: list, named-only, open `first` by name prefix, rename
    # `third`, then delete `third` with confirmation.
    _prov, resume_err = _drive_capture(
        second,
        resume_cwd,
        "\n".join(
            [
                "y",
                "/resume",
                "/resume named",
                "/resume rename " + third_id[:6] + " renamed-three",
                "/resume delete " + third_id[:6] + " --yes",
                "/exit",
                "",
            ]
        ),
    )
    listed_after = list_native_sessions(resume_dir)
    third_gone = all(s.session_id != third_id for s in listed_after)
    resume_ok = (
        "alpha" in resume_err  # named session listed by /resume
        and "named native sessions" in resume_err  # named-only filter
        and "renamed" in resume_err  # rename ran
        and third_gone  # delete --yes removed the native file
    )
    checks.append(
        Check(
            "resume_picker_controls",
            resume_ok,
            f"named_listed={'alpha' in resume_err} third_deleted={third_gone}",
        )
    )

    # 12. startup flags via the same resolver the CLI uses
    flag_cwd = session_dir_root / "ws_flags"
    flag_cwd.mkdir(parents=True, exist_ok=True)
    flag_state = state_root / "flagstate"
    fresh = resolve_startup_session(flag_cwd, mode="new", state_root=flag_state)
    fresh.append_message(UserMessage(content="HELLO"))
    fid = fresh.session_id
    cont = resolve_startup_session(flag_cwd, mode="continue", state_root=flag_state)
    opened = resolve_startup_session(
        flag_cwd, mode="session", target=fid[:6], state_root=flag_state
    )
    forked = resolve_startup_session(
        flag_cwd, mode="fork", target=fid[:6], state_root=flag_state
    )
    none = resolve_startup_session(flag_cwd, mode="none", state_root=flag_state)
    flags_ok = (
        cont is not None
        and cont.session_id == fid
        and opened is not None
        and opened.session_id == fid
        and forked is not None
        and forked.session_id != fid
        and none is None
    )
    checks.append(
        Check(
            "startup_flags",
            flags_ok,
            f"continue={cont.session_id[:8] if cont else None} "
            f"fork_new={forked.session_id[:8] if forked else None} "
            f"no_session={none}",
        )
    )

    # 13. reload reconstructs tree/labels/name/leaf/context
    reload_cwd = session_dir_root / "ws_reload"
    reload_cwd.mkdir(parents=True, exist_ok=True)
    reload_dir = state_root / "native-sessions" / "reloadcmd"
    rtree = NativeSessionTree.create(reload_cwd, session_dir=reload_dir)
    _drive(
        rtree,
        reload_cwd,
        "\n".join(["/name reloaded", "p", "q", "/tree label 1 pin", "/exit", ""]),
    )
    rr = NativeSessionTree.open(rtree.path)
    labels = [e for e in rr.get_entries() if isinstance(e, LabelEntry) and e.label]
    reload_ok = (
        rr.name == "reloaded"
        and bool(labels)
        and any(isinstance(m, UserMessage) for m in rr.build_context().messages)
    )
    checks.append(
        Check("reload_reconstruction", reload_ok, f"name={rr.name} labels={len(labels)}")
    )

    # 14. metadata archive privacy: secret prompt body never reaches the archive
    archive_ok, archive_detail = _check_archive_privacy(session_dir_root)
    checks.append(Check("archive_privacy", archive_ok, archive_detail))

    return checks


def _check_archive_privacy(base: Path) -> tuple[bool, str]:
    """Run the product runtime through HarnessRunner and verify the
    ``pipy-session`` metadata archive contains no raw prompt body."""

    from pipy_harness.adapters.native import PipyNativeToolReplAdapter
    from pipy_harness.runner import FileSessionRecorder, HarnessRunner

    archive_root = base / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    native_dir = base / "archive-native"
    cwd = base / "ws_archive"
    cwd.mkdir(parents=True, exist_ok=True)
    secret = "SECRET_PROMPT_BODY_DO_NOT_ARCHIVE"
    tree = NativeSessionTree.create(cwd, session_dir=native_dir)
    adapter = PipyNativeToolReplAdapter(
        provider=_SeenProvider(),
        native_session=tree,
        input_stream=io.StringIO(f"{secret}\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    result = HarnessRunner(adapter=adapter, recorder=FileSessionRecorder()).run(
        RunRequest(
            agent="pipy-native",
            slug="conformance-archive",
            command=[],
            cwd=cwd,
            goal="conformance archive privacy",
            root=archive_root,
            capture_policy=CapturePolicy(),
        )
    )
    archive_body = result.record.jsonl_path.read_text(encoding="utf-8")
    native_body = tree.path.read_text(encoding="utf-8") if tree.path else ""
    # The secret is present in the native product transcript but absent from the
    # metadata archive record.
    ok = secret in native_body and secret not in archive_body
    return ok, f"secret_in_native={secret in native_body} secret_in_archive={secret in archive_body}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state_root = base / "state"
        work = base / "work"
        # Isolate the native-session and metadata-archive stores.
        os.environ["PIPY_NATIVE_SESSIONS_ROOT"] = str(state_root)
        checks = run_checks(state_root, work)

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
    sys.exit(main())
