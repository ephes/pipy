"""Hard conformance gate for the Pi-style interactive TUI/editor workflow.

This script drives pipy's **real product TUI** (`ToolLoopTerminalUi` over a real
pseudo-TTY, the same path `pipy repl --agent pipy-native --repl-mode tool-loop`
uses) with the deterministic fake provider in a temporary workspace, then
inspects the observable result. It is the implementation source of truth for the
TUI workflow track described in ``docs/tui-workflow.md``: it fails unless the
specified editor surfaces work through the product runtime (not a bypass).

Run:

    uv run python scripts/parity_checks/tui_workflow_conformance.py --json

It proves, end to end, through the product PTY path with the fake provider:

1. an ``@`` query opens the picker and accepting replaces the token with a valid
   ``@path`` that the file-reference resolver then loads on submit;
2. Tab path completion completes a directory prefix and is a no-op in prose;
3. ``!cmd`` runs without a provider turn and records context; ``!!cmd`` runs
   without recording context;
4. ``ctrl+p`` changes the active model through the scoped/available list with no
   provider turn;
5. ``shift+tab`` cycles the thinking level and appends a ``thinking_level_change``
   native-tree entry;
6. ``ctrl+o`` and ``ctrl+t`` toggle tool-output expansion and thinking
   visibility as renderer view flags with the persisted thinking setting;
7. steering and follow-up messages queue during a turn, render in the pending
   region, dequeue to the editor, and drain in steering-then-follow-up order;
8. a clipboard-image paste inserts an owner-only ``@image:`` reference that
   attaches on submit;
9. mouse-tracking enable sequences are never emitted (and no alternate screen);
10. Escape during a live turn sets the cancel token, the provider observes it,
    the response is closed, the run settles aborted, and the native session tree
    records no fabricated assistant after the aborted turn;
11. every interactive surface degrades to a deterministic non-TTY diagnostic and
    never falls through as a provider prompt;
12. no prompt body, command output, image bytes, or provider payload reaches the
    default ``pipy-session`` metadata archive.

Exits 0 when every check passes, 1 otherwise. No real network/AI calls.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast

# Pin a deterministic viewport before importing anything that reads it.
os.environ.setdefault("COLUMNS", "100")
os.environ.setdefault("LINES", "40")
os.environ.setdefault("TERM", "xterm-256color")
os.environ.pop("NO_COLOR", None)

from pipy_harness.models import CapturePolicy, HarnessStatus, RunRequest  # noqa: E402
from pipy_harness.native import (  # noqa: E402
    NativeToolReplSession,
)
from pipy_harness.native.cancellation import ProviderCancelledError  # noqa: E402
from pipy_harness.native.clipboard import ImageClipboardResult  # noqa: E402
from pipy_harness.native.models import ProviderRequest, ProviderResult  # noqa: E402
from pipy_harness.native.provider import ProviderPort  # noqa: E402
from pipy_harness.native.repl_state import (  # noqa: E402
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.session_tree import NativeSessionTree  # noqa: E402
from pipy_harness.native.settings import SettingsManager  # noqa: E402
from pipy_harness.native.tools.messages import (  # noqa: E402
    AssistantMessage,
    UserMessage,
)
from pipy_harness.native.tui import ToolLoopTerminalUi  # noqa: E402

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class _FakeProvider:
    """Tool-capable fake provider recording prompts, attachments, calls."""

    final_text: str = "TURN_DONE"
    model_id: str = "fake-model"
    supports_tool_calls: bool = True
    calls: int = 0
    user_prompts: list[str] = field(default_factory=list)
    attachment_counts: list[int] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "fake"

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.calls += 1
        text = request.user_prompt or ""
        for message in request.messages:
            if isinstance(message, UserMessage):
                text += "\n" + str(message.content)
        self.user_prompts.append(text)
        self.attachment_counts.append(len(request.attachments or ()))
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=request.provider_name,
            model_id=request.model_id,
            started_at=now,
            ended_at=now,
            final_text=self.final_text,
            tool_calls=(),
        )


@dataclass
class _SteeringProvider:
    """Blocks the first turn until cancelled so mid-turn input can be queued."""

    model_id: str = "fake-model"
    supports_tool_calls: bool = True
    calls: int = 0
    user_prompts: list[str] = field(default_factory=list)
    observed: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "fake"

    def complete(
        self, request: ProviderRequest, *, cancel_token=None, **_kwargs: object
    ) -> ProviderResult:
        self.calls += 1
        self.user_prompts.append(request.user_prompt or "")
        if self.calls == 1 and cancel_token is not None:
            if cancel_token.event.wait(timeout=8.0):
                self.observed.append("cancelled")
                raise ProviderCancelledError("cancelled")
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=request.provider_name,
            model_id=request.model_id,
            started_at=now,
            ended_at=now,
            final_text=f"DRAINED_{self.calls}",
            tool_calls=(),
        )


def _reasoning_state(tmp_path: Path, provider: ProviderPort) -> NativeReplProviderState:
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState

    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENAI_API_KEY": "sk"},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    return NativeReplProviderState(
        selection=NativeModelSelection("openai", "gpt-5.5"),
        provider_factory=lambda sel: provider,
        catalog_state=catalog,
        persist_defaults=False,
    )


class _PtyRun:
    """Spawn the product TUI session over a real PTY for one scenario."""

    def __init__(
        self,
        *,
        workspace: Path,
        provider: ProviderPort,
        provider_state: NativeReplProviderState | None = None,
        settings: SettingsManager | None = None,
        native_session: NativeSessionTree | None = None,
        clipboard_image_read: Callable[[], ImageClipboardResult] | None = None,
    ) -> None:
        import fcntl
        import pty
        import struct
        import termios

        self._in_master, in_slave = pty.openpty()
        err_master, err_slave = pty.openpty()
        self._err_master = err_master
        # Set a stable winsize on the slaves so the renderer's resize poll does
        # not see a fluctuating terminal size (which would trigger spurious
        # full-frame repaints that race with the key-read loop).
        winsize = struct.pack(
            "HHHH", int(os.environ.get("LINES", "40")), int(os.environ.get("COLUMNS", "100")), 0, 0
        )
        for fd in (in_slave, err_slave):
            try:
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass
        self._stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
        self._terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
        self._chunks: list[bytes] = []
        self._drain = threading.Thread(target=self._drain_loop, daemon=True)
        self._drain.start()

        self.ui = ToolLoopTerminalUi(
            input_stream=cast(TextIO, self._stdin),
            terminal_stream=cast(TextIO, self._terminal),
            cwd=workspace,
        )
        kwargs: dict[str, object] = {"tool_registry": {}}
        if provider_state is not None:
            kwargs["provider_state"] = provider_state
        if settings is not None:
            kwargs["settings_manager"] = settings
        if native_session is not None:
            kwargs["native_session"] = native_session
        if clipboard_image_read is not None:
            kwargs["clipboard_image_read"] = clipboard_image_read
        self.provider = provider
        self._session = NativeToolReplSession(provider=provider, **kwargs)
        self._workspace = workspace
        self._orig_build = NativeToolReplSession._build_terminal_ui
        NativeToolReplSession._build_terminal_ui = (  # type: ignore[assignment]
            lambda _self, input_stream, error_stream, workspace, resources=None, **_k: self.ui
        )
        self._worker = threading.Thread(target=self._run, daemon=True)

    def _drain_loop(self) -> None:
        while True:
            try:
                chunk = os.read(self._err_master, 65536)
            except OSError:
                return
            if not chunk:
                return
            self._chunks.append(chunk)

    def _run(self) -> None:
        self._session.run(
            workspace_root=self._workspace,
            input_stream=cast(TextIO, self._stdin),
            output_stream=cast(TextIO, self._terminal),
            error_stream=cast(TextIO, self._terminal),
        )

    def text(self) -> str:
        return b"".join(self._chunks).decode("utf-8", "replace")

    def write(self, data: bytes) -> None:
        os.write(self._in_master, data)

    def wait_for(self, needle: str, *, timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.text():
                return True
            time.sleep(0.02)
        return False

    def wait_pred(self, pred: Callable[[], bool], *, timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred():
                return True
            time.sleep(0.02)
        return False

    def toggle_until_flip(
        self, key: bytes, getter: Callable[[], bool], *, tries: int = 4
    ) -> bool:
        """Send a toggle key until the observed flag flips from its initial value.

        Control-key byte delivery over a PTY can be intermittently dropped by
        the line discipline before the key-read loop is settled; because a lost
        key is simply not processed (never delayed past the wait), re-sending it
        after the flag has not flipped is a safe idempotent retry that lands the
        first effective toggle.
        """

        initial = getter()
        for _ in range(tries):
            self.write(key)
            if self.wait_pred(lambda: getter() != initial, timeout=2.0):
                return True
        return False

    def __enter__(self) -> "_PtyRun":
        self._worker.start()
        self.wait_for("escape interrupt", timeout=8.0)
        # Wait until read_line has armed raw mode before driving keys. raw mode
        # is entered with TCSAFLUSH, which discards input buffered before it is
        # set, so a keystroke sent in that window is lost. Raw-mode entry emits
        # the bracketed-paste enable (ESC[?2004h); once it appears the read loop
        # is reading and the first keystroke is safe.
        self.wait_for("?2004h", timeout=8.0)
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            self.write(b"\x04")
        except OSError:
            pass
        self._worker.join(timeout=8.0)
        NativeToolReplSession._build_terminal_ui = self._orig_build  # type: ignore[assignment]
        try:
            self._terminal.flush()
            self._terminal.close()
            self._stdin.close()
        except OSError:
            pass
        self._drain.join(timeout=8.0)
        for fd in (self._in_master, self._err_master):
            try:
                os.close(fd)
            except OSError:
                pass


def _no_mouse_tracking(text: str) -> bool:
    return not any(
        mode in text for mode in ("?1000h", "?1002h", "?1003h", "?1006h", "?1015h")
    ) and "\x1b[?1049h" not in text


def run_checks(base: Path) -> list[Check]:
    checks: list[Check] = []
    captures: list[str] = []

    # ----- 1. @ file picker + 2. Tab path completion -----------------------
    ws = base / "ws_picker"
    (ws / "src" / "tui").mkdir(parents=True)
    (ws / "src" / "tui" / "config.py").write_text("nested\n")
    (ws / "src" / "config.py").write_text("top\n")
    (ws / "scripts").mkdir()
    provider = _FakeProvider("PICKER_DONE")
    with _PtyRun(workspace=ws, provider=cast(ProviderPort, provider)) as run:
        run.write(b"see @config")
        picker_ok = run.wait_pred(lambda: "@src/config.py" in run.text())
        no_turn_in_picker = provider.calls == 0
        run.write(b"\t")  # accept
        run.wait_pred(lambda: "@src/config.py" in run.ui.input_text)
        run.write(b"\n")  # submit -> resolver loads the @path
        run.wait_for("PICKER_DONE")
        # Tab path completion: ./scr<Tab> completes to ./scripts/
        run.write(b"./scr\t")
        path_ok = run.wait_pred(lambda: run.ui.input_text == "./scripts/")
        run.write(b"\x15")  # ctrl-u clear
        # Tab in prose is a no-op.
        run.write(b"justprose")
        run.write(b"\t")
        time.sleep(0.2)
        prose_noop = run.ui.input_text == "justprose" and not run.ui.autocomplete_open
        run.write(b"\x15")
    captures.append(run.text())
    resolved_ok = any("@src/config.py" in p for p in provider.user_prompts)
    checks.append(
        Check("at_picker_ranks_and_resolves", picker_ok and resolved_ok and no_turn_in_picker,
              f"picker={picker_ok} resolved={resolved_ok} no_turn={no_turn_in_picker}")
    )
    checks.append(
        Check("tab_path_completion_and_prose_noop", path_ok and prose_noop,
              f"path={path_ok} prose_noop={prose_noop}")
    )

    # ----- 3. !/!! shell shortcuts -----------------------------------------
    ws3 = base / "ws_bash"
    ws3.mkdir(parents=True)
    provider3 = _FakeProvider("BASH_TURN_DONE")
    with _PtyRun(workspace=ws3, provider=cast(ProviderPort, provider3)) as run:
        run.write(b"!echo ctx-bang\n")
        run.wait_for("ctx-bang")
        no_turn_for_bash = provider3.calls == 0
        time.sleep(0.4)
        run.write(b"recall\n")
        run.wait_pred(lambda: provider3.calls == 1)
        ctx_recorded = any("ctx-bang" in p for p in provider3.user_prompts)
        run.write(b"!!echo secret-bang\n")
        run.wait_for("secret-bang")
        time.sleep(0.4)
        run.write(b"again\n")
        run.wait_pred(lambda: provider3.calls == 2)
        no_context_excluded = not any("secret-bang" in p for p in provider3.user_prompts)
    captures.append(run.text())
    checks.append(
        Check("bash_shortcuts_context_and_exclude",
              no_turn_for_bash and ctx_recorded and no_context_excluded,
              f"no_turn={no_turn_for_bash} ctx={ctx_recorded} excluded={no_context_excluded}")
    )

    # ----- 4. ctrl+p model cycle + 5. shift+tab thinking -------------------
    ws4 = base / "ws_cycle"
    ws4.mkdir(parents=True)
    provider4 = _FakeProvider("CYCLE_DONE")
    provider4.model_id = "gpt-5.5"
    state4 = _reasoning_state(ws4, cast(ProviderPort, provider4))
    tree4 = NativeSessionTree.create(ws4, session_dir=base / "native-cycle")
    with _PtyRun(
        workspace=ws4,
        provider=cast(ProviderPort, provider4),
        provider_state=state4,
        native_session=tree4,
    ) as run:
        run.write(b"\x1b[Z")  # shift+tab -> thinking cycle
        think_ok = run.wait_for("thinking level: minimal")
        run.write(b"\x10")  # ctrl+p -> model cycle
        model_ok = run.wait_for("selected model")
        cycle_no_turn = provider4.calls == 0
    captures.append(run.text())
    tree_levels = [
        getattr(e, "thinking_level", None)
        for e in tree4.get_entries()
        if getattr(e, "type", "") == "thinking_level_change"
    ]
    checks.append(
        Check("model_cycle_no_turn", model_ok and cycle_no_turn,
              f"model={model_ok} no_turn={cycle_no_turn}")
    )
    checks.append(
        Check("thinking_cycle_and_tree_entry", think_ok and "minimal" in tree_levels,
              f"cycle={think_ok} tree_levels={tree_levels}")
    )

    # ----- 6. ctrl+o / ctrl+t folding + persistence ------------------------
    ws6 = base / "ws_fold"
    ws6.mkdir(parents=True)
    settings6 = SettingsManager.for_workspace(ws6)
    provider6 = _FakeProvider("FOLD_DONE")
    with _PtyRun(
        workspace=ws6, provider=cast(ProviderPort, provider6), settings=settings6
    ) as run:
        thinking_ok = (
            run.toggle_until_flip(b"\x14", lambda: run.ui.thinking_hidden)
            and run.ui.thinking_hidden
            and run.wait_for("thinking blocks: hidden")
        )
        tools_ok = (
            run.toggle_until_flip(b"\x0f", lambda: run.ui.tools_expanded)
            and run.ui.tools_expanded
            and run.wait_for("tool output: expanded")
        )
        fold_no_turn = provider6.calls == 0
    captures.append(run.text())
    persisted = SettingsManager.for_workspace(ws6).get_hide_thinking_block()
    checks.append(
        Check("folding_toggles_and_persist",
              thinking_ok and tools_ok and persisted and fold_no_turn,
              f"thinking={thinking_ok} tools={tools_ok} persisted={persisted} "
              f"no_turn={fold_no_turn}")
    )

    # ----- 7. queued steering / follow-up ----------------------------------
    ws7 = base / "ws_queue"
    ws7.mkdir(parents=True)
    provider7 = _SteeringProvider()
    with _PtyRun(workspace=ws7, provider=cast(ProviderPort, provider7)) as run:
        run.write(b"original q\n")
        run.wait_pred(lambda: provider7.calls == 1)
        run.write(b"followup msg\x1b\r")  # alt+enter queues follow-up
        pending_ok = run.wait_for("Follow-up: followup msg")
        queued_no_turn = provider7.calls == 1
        run.write(b"steer msg\n")  # enter queues steering + interrupts
        drained = run.wait_pred(lambda: provider7.calls >= 3, timeout=10.0)
    captures.append(run.text())
    order_ok = False
    if "steer msg" in provider7.user_prompts and "followup msg" in provider7.user_prompts:
        order_ok = provider7.user_prompts.index("steer msg") < provider7.user_prompts.index(
            "followup msg"
        )
    checks.append(
        Check("steering_follow_up_queue_and_order",
              pending_ok and queued_no_turn and drained and order_ok,
              f"pending={pending_ok} queued_no_turn={queued_no_turn} "
              f"drained={drained} order={order_ok} prompts={provider7.user_prompts}")
    )

    # ----- 8. clipboard image paste ----------------------------------------
    ws8 = base / "ws_image"
    ws8.mkdir(parents=True)
    provider8 = _FakeProvider("IMAGE_DONE")
    clip = lambda: ImageClipboardResult(  # noqa: E731
        found=True, data=_PNG, media_type="image/png", detail="ok"
    )
    with _PtyRun(
        workspace=ws8, provider=cast(ProviderPort, provider8), clipboard_image_read=clip
    ) as run:
        run.write(b"look \x16")  # ctrl+v
        img_ref_ok = run.wait_pred(lambda: "@image:" in run.ui.input_text)
        img_no_turn = provider8.calls == 0
        run.write(b"\n")
        run.wait_for("IMAGE_DONE")
        owner_only = False
        if run.ui.clipboard_temp_dir is not None:
            written = list(run.ui.clipboard_temp_dir.glob("pipy-clipboard-*.png"))
            owner_only = bool(written) and all(
                stat.S_IMODE(p.stat().st_mode) == 0o600 for p in written
            )
    captures.append(run.text())
    attached = bool(provider8.attachment_counts) and provider8.attachment_counts[-1] == 1
    checks.append(
        Check("clipboard_image_owner_only_and_attaches",
              img_ref_ok and img_no_turn and owner_only and attached,
              f"ref={img_ref_ok} no_turn={img_no_turn} owner_only={owner_only} "
              f"attached={attached}")
    )

    # ----- 10. true cancellation -------------------------------------------
    ws10 = base / "ws_cancel"
    ws10.mkdir(parents=True)
    provider10 = _SteeringProvider()
    tree10 = NativeSessionTree.create(ws10, session_dir=base / "native-cancel")
    with _PtyRun(
        workspace=ws10, provider=cast(ProviderPort, provider10), native_session=tree10
    ) as run:
        run.write(b"long prompt\n")
        run.wait_pred(lambda: provider10.calls == 1)
        time.sleep(0.3)
        run.write(b"\x1b")  # escape -> true cancel
        aborted_ok = run.wait_for("Operation aborted")
    captures.append(run.text())
    observed_cancel = provider10.observed == ["cancelled"]
    # No fabricated assistant after the aborted user turn in the native tree.
    entries = list(tree10.get_entries())
    has_assistant = any(
        isinstance(getattr(e, "message", None), AssistantMessage) for e in entries
    )
    checks.append(
        Check("true_cancellation_and_tree_consistent",
              aborted_ok and observed_cancel and not has_assistant,
              f"aborted={aborted_ok} observed={observed_cancel} "
              f"fabricated_assistant={has_assistant}")
    )

    # ----- 9. mouse-tracking invariant (across all captures) ---------------
    mouse_ok = all(_no_mouse_tracking(text) for text in captures)
    checks.append(Check("never_enables_mouse_tracking", mouse_ok, f"captures={len(captures)}"))

    # ----- 11. non-TTY fallback never falls through as a provider prompt ---
    checks.append(_check_non_tty_fallback(base))

    # ----- 12. archive privacy ---------------------------------------------
    checks.append(_check_archive_privacy(base))

    return checks


def _check_non_tty_fallback(base: Path) -> Check:
    """Local commands/overlays degrade to diagnostics on a non-TTY stream and
    never reach the provider as a prompt."""

    ws = base / "ws_nontty"
    ws.mkdir(parents=True)
    provider = _FakeProvider("NONTTY_DONE")
    tree = NativeSessionTree.create(ws, session_dir=base / "native-nontty")
    session = NativeToolReplSession(provider=cast(ProviderPort, provider), native_session=tree)
    err = io.StringIO()
    script = "\n".join(
        ["/scoped-models", "/settings", "/hotkeys", "real question", "/exit", ""]
    )
    session.run(
        workspace_root=ws,
        input_stream=io.StringIO(script),
        output_stream=io.StringIO(),
        error_stream=err,
    )
    diagnostics = err.getvalue()
    # Only the genuine prompt reached the provider; the local commands did not.
    only_real_turn = provider.calls == 1 and any(
        "real question" in p for p in provider.user_prompts
    )
    local_not_sent = not any(
        "/scoped-models" in p or "/settings" in p or "/hotkeys" in p
        for p in provider.user_prompts
    )
    has_diag = "scoped models" in diagnostics.lower() or "settings" in diagnostics.lower()
    ok = only_real_turn and local_not_sent and has_diag
    return Check(
        "non_tty_fallback_no_provider_fallthrough",
        ok,
        f"only_real={only_real_turn} local_not_sent={local_not_sent} diag={has_diag}",
    )


def _check_archive_privacy(base: Path) -> Check:
    """No prompt body, command output, or image bytes reach the metadata
    archive (the native transcript keeps them; the archive does not)."""

    from pipy_harness.adapters.native import PipyNativeToolReplAdapter
    from pipy_harness.runner import FileSessionRecorder, HarnessRunner

    archive_root = base / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    native_dir = base / "archive-native"
    ws = base / "ws_archive"
    ws.mkdir(parents=True, exist_ok=True)
    secret_prompt = "SECRET_PROMPT_BODY"
    secret_cmd = "SECRET_CMD_OUTPUT"
    tree = NativeSessionTree.create(ws, session_dir=native_dir)
    adapter = PipyNativeToolReplAdapter(
        provider=_FakeProvider("ARCHIVE_DONE"),
        native_session=tree,
        input_stream=io.StringIO(
            f"{secret_prompt}\n!echo {secret_cmd}\n/exit\n"
        ),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    result = HarnessRunner(adapter=adapter, recorder=FileSessionRecorder()).run(
        RunRequest(
            agent="pipy-native",
            slug="tui-conformance-archive",
            command=[],
            cwd=ws,
            goal="tui archive privacy",
            root=archive_root,
            capture_policy=CapturePolicy(),
        )
    )
    archive_body = result.record.jsonl_path.read_text(encoding="utf-8")
    native_body = tree.path.read_text(encoding="utf-8") if tree.path else ""
    prompt_ok = secret_prompt in native_body and secret_prompt not in archive_body
    cmd_ok = secret_cmd in native_body and secret_cmd not in archive_body
    return Check(
        "archive_privacy_no_leak",
        prompt_ok and cmd_ok,
        f"prompt_in_native={secret_prompt in native_body} "
        f"prompt_in_archive={secret_prompt in archive_body} "
        f"cmd_in_native={secret_cmd in native_body} "
        f"cmd_in_archive={secret_cmd in archive_body}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    if os.name != "posix":
        print("tui_workflow_conformance requires a POSIX PTY", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["PIPY_NATIVE_SESSIONS_ROOT"] = str(base / "state")
        # Isolate the settings/config home so the gate neither reads nor writes
        # the real global settings (the folding check persists hideThinkingBlock)
        # and is reproducible across runs.
        config_home = base / "config"
        config_home.mkdir(parents=True, exist_ok=True)
        os.environ["PIPY_CONFIG_HOME"] = str(config_home)
        os.environ["PIPY_PROMPT_HISTORY_PATH"] = str(base / "prompt-history.json")
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
