# Top-level CLI cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pipy's command surface behave like Pi's — bare `pipy`/positional prompt launches the interactive session — and remove or realign the pipy-only surfaces in parity-plan §3.

**Architecture:** A front-controller router in `cli.py main()` injects `repl` for non-subcommand invocations (Approach A); the no-tool REPL and dead automation flags are deleted; user-facing pipy-only slash commands become deprecated aliases of their Pi equivalents. Five sequential slices, each green under `just check` and Pi-reviewed before commit.

**Tech Stack:** Python 3 stdlib (argparse), pytest, the existing `pipy_harness.native` runtime. No new runtime dependencies.

**Source spec:** `docs/superpowers/specs/2026-06-20-top-level-cli-cleanup-design.md`

**Working mode:** Sequential on `main` (repo convention is linear history). One commit per slice. Run `just check` and the Pi review loop (`python3 ~/projects/agent-stuff/claude/skills/pi-review-loop/bin/pi-review-loop --repo "$PWD" --run-dir "$(mktemp -d)/pi-review"`) before each commit; only commit a slice when Pi returns CLEAN.

---

## File Structure

Files touched across the plan:

- `src/pipy_harness/cli.py` (2593 lines) — argparse build + `main()` dispatch. Router added (slice 2); `--repl-mode` (slice 1), `--native-output`/`--archive-transcript` (slice 4) removed.
- `src/pipy_harness/native/session.py` (~2600 lines) — `NativeNoToolReplSession` and proposal/apply handlers deleted (slice 1).
- `src/pipy_harness/native/repl_input.py` — slash-command completion/description registries updated (slices 1, 3).
- `src/pipy_harness/native/tool_loop_session.py` (6397 lines) — deprecated-alias handlers + `/skill`/`/template` realignment (slice 3).
- `src/pipy_harness/adapters/native.py` (514 lines) — no-tool adapter path removed (slice 1).
- `tests/` — new CLI routing / alias / removed-flag tests; `@file`-context tests migrated off no-tool.
- `docs/` + `CHANGELOG.md` — sync (slice 5).

Pre-flight for each slice: `rg` for every reference to the symbol/flag being removed so no caller is missed.

---

## Slice 1 — Retire the no-tool REPL + proposal/apply family

Goal: one product REPL (tool-loop). Remove `--repl-mode`, `NativeNoToolReplSession`, and `/read` `/ask-file` `/propose-file` `/apply-proposal`.

### Task 1.1: Inventory every no-tool reference

**Files:** none (read-only).

- [ ] **Step 1: Enumerate references**

Run:
```bash
rg -n "repl-mode|repl_mode|no-tool|no_tool|NativeNoToolReplSession|_resolve_repl_mode|_repl_adapter_for|ask-file|ask_file|propose-file|propose_file|apply-proposal|apply_proposal|READ_ONLY_REPL_COMMAND|ASK_FILE_REPL_COMMAND|PROPOSE_FILE_REPL_COMMAND|APPLY_PROPOSAL_REPL_COMMAND|tool_observation.recorded|patch_proposal.recorded" src tests docs
```
Expected: a reference list. Record it; every hit in `src/` must be resolved by end of slice, and every `tests/` hit must be migrated or deleted.

### Task 1.2: Remove `--repl-mode`, default to tool-loop (test-first)

**Files:**
- Modify: `src/pipy_harness/cli.py` (`--repl-mode` def ~256–269; `_resolve_repl_mode()` ~1829–1891; `_repl_adapter_for` call site ~988)
- Test: `tests/test_harness_native_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_repl_mode_flag_is_removed():
    import subprocess, sys
    proc = subprocess.run(
        [sys.executable, "-m", "pipy_harness.cli", "repl", "--repl-mode", "no-tool"],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "--repl-mode" not in proc.stdout
    # argparse reports the unknown option on stderr
    assert "repl-mode" in proc.stderr.lower() or "unrecognized" in proc.stderr.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_harness_native_cli.py::test_repl_mode_flag_is_removed -v`
Expected: FAIL (flag still accepted, returncode 0 or different error).

- [ ] **Step 3: Implement**

In `cli.py`: delete the `--repl-mode` `add_argument` block (~256–269). Delete `_resolve_repl_mode()` (~1829–1891) and its call. At the former call site, route the product REPL unconditionally to the tool-loop adapter (`_tool_repl_adapter_for(...)`, ~1894–1968). Remove the now-unused `auto`/`no-tool`/`tool-loop` choice constant if present.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_harness_native_cli.py::test_repl_mode_flag_is_removed -v`
Expected: PASS.

### Task 1.3: Delete `NativeNoToolReplSession` + proposal/apply handlers

**Files:**
- Modify: `src/pipy_harness/native/session.py` (delete `NativeNoToolReplSession` ~865–1868 and helpers `_parse_repl_ask_file_command`, `_parse_repl_propose_file_command`, `_handle_repl_read_command` ~1871–1902, `_handle_repl_apply_proposal_command` ~1905–1929, and the `READ_ONLY/ASK_FILE/PROPOSE_FILE/APPLY_PROPOSAL_REPL_COMMAND` constants ~1393–1586)
- Modify: `src/pipy_harness/adapters/native.py` (remove the no-tool adapter path)
- Test: existing suite

- [ ] **Step 1: Delete the class, helpers, and constants** listed above. Keep `NativeAgentSession` (used by `pipy run`) intact — it is a different class.

- [ ] **Step 2: Remove the no-tool adapter builder** `_repl_adapter_for` (referenced `cli.py:988`) and its definition; remove the no-tool branch in `adapters/native.py`.

- [ ] **Step 3: Resolve compile/import errors**

Run: `uv run python -c "import pipy_harness.cli, pipy_harness.native.session, pipy_harness.adapters.native"`
Expected: no ImportError / NameError. Fix any dangling references surfaced by Task 1.1's list.

- [ ] **Step 4: Run the focused suites**

Run: `uv run pytest tests/test_native_session.py tests/test_pipy_native_tool_repl_adapter.py -q`
Expected: PASS (no-tool-specific tests in these files must be deleted in Task 1.5 first if they reference removed symbols — do 1.5 alongside).

### Task 1.4: Drop no-tool slash commands from the registries

**Files:**
- Modify: `src/pipy_harness/native/repl_input.py` (`DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS` ~29–50, `DEFAULT_REPL_COMMAND_DESCRIPTIONS` ~51–77, `DEFAULT_REPL_FILE_PATH_COMPLETION_COMMANDS` ~78–83)
- Test: `tests/` covering repl_input completions

- [ ] **Step 1: Write the failing test**

```python
def test_proposal_commands_absent_from_completions():
    from pipy_harness.native.repl_input import DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    for gone in ("/read", "/ask-file", "/propose-file", "/apply-proposal"):
        assert gone not in DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
```

- [ ] **Step 2: Run** `uv run pytest tests/test_native_repl_input.py::test_proposal_commands_absent_from_completions -v` (create the test file if absent). Expected: FAIL.

- [ ] **Step 3: Implement** — remove `/read`, `/ask-file`, `/propose-file`, `/apply-proposal` from all three registries.

- [ ] **Step 4: Run** the same test. Expected: PASS.

### Task 1.5: Migrate `@file`-context tests onto the tool-loop path

**Files:**
- Modify: `tests/test_native_at_file_context_cli.py` (uses `--repl-mode no-tool` ~line 32) and any no-tool-only tests found in Task 1.1.

- [ ] **Step 1:** Rewrite the `@file`-context test(s) to exercise the tool-loop product REPL (drop `--repl-mode no-tool`; use the default product path). Delete tests that only asserted no-tool/proposal behavior.

- [ ] **Step 2: Run** `uv run pytest tests/test_native_at_file_context_cli.py -q`. Expected: PASS — `@file` excerpts still load in the product session.

### Task 1.6: Gate + commit

- [ ] **Step 1:** `rg -n "no-tool|NativeNoToolReplSession|propose-file" src` → expected: no hits.
- [ ] **Step 2:** `just check` → expected: green.
- [ ] **Step 3:** Pi review loop → expected: CLEAN. Fix findings and re-review (≤3 rounds) before committing.
- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Retire the no-tool REPL and proposal/apply commands

Collapse --repl-mode to a single tool-loop product session and delete
NativeNoToolReplSession plus /read //ask-file //propose-file //apply-proposal
and their archive-side observation/patch-proposal events. @file context now
loads in the product session."
```

---

## Slice 2 — Top-level Pi-shape dispatch (front-controller router)

Goal: `pipy` and `pipy "<prompt>"` launch the interactive product session; subcommands stay reachable; reserved-word exception documented.

### Task 2.1: Routing-matrix test (test-first)

**Files:**
- Create: `tests/test_cli_top_level_routing.py`

- [ ] **Step 1: Write the failing tests** — assert the router's argv transform via a seam (Task 2.2 exposes `route_argv(argv, known_subcommands)`).

```python
import pytest
from pipy_harness.cli import route_argv

SUBS = {"auth", "run", "repl", "config", "install", "remove", "uninstall", "list", "update"}

@pytest.mark.parametrize("argv,expected", [
    ([], ["repl"]),                                   # bare pipy -> interactive
    (["do X"], ["repl", "do X"]),                     # positional prompt -> repl
    (["--model", "m"], ["repl", "--model", "m"]),     # bare repl flag -> repl
    (["-p", "x"], ["repl", "-p", "x"]),               # one-shot
    (["@file.py", "summarize"], ["repl", "@file.py", "summarize"]),
    (["repl", "--model", "m"], ["repl", "--model", "m"]),   # explicit subcommand unchanged
    (["run", "--agent", "a"], ["run", "--agent", "a"]),
    (["auth"], ["auth"]),                             # reserved word -> subcommand (exception)
    (["--help"], ["--help"]),                         # top-level only, not re-routed
    (["-h"], ["-h"]),
    (["--version"], ["--version"]),
    (["-v"], ["-v"]),
    (["--export", "s.jsonl"], ["--export", "s.jsonl"]),
])
def test_route_argv(argv, expected):
    assert route_argv(list(argv), SUBS) == expected
```

- [ ] **Step 2: Run** `uv run pytest tests/test_cli_top_level_routing.py -v`. Expected: FAIL (`route_argv` undefined).

### Task 2.2: Implement `route_argv` and wire it into `main()`

**Files:**
- Modify: `src/pipy_harness/cli.py` (add `route_argv`; call it at the top of `main()` ~752 before argparse `parse_args`)

- [ ] **Step 1: Implement** the router:

```python
_TOP_LEVEL_ONLY_FLAGS = {"-h", "--help", "-v", "--version", "--export"}

def route_argv(argv, known_subcommands):
    """Inject 'repl' for non-subcommand invocations (Pi-shape top-level).

    - empty argv -> ['repl']
    - first token is a known subcommand -> unchanged
    - first token is a top-level-only flag -> unchanged
    - otherwise -> ['repl', *argv]
    Reserved-word exception: a bare token equal to a subcommand name dispatches
    that subcommand (use `pipy repl "<word>"` / `pipy -p "<word>"` to force a prompt).
    """
    if not argv:
        return ["repl"]
    first = argv[0]
    if first in known_subcommands:
        return argv
    if first in _TOP_LEVEL_ONLY_FLAGS:
        return argv
    return ["repl", *argv]
```

- [ ] **Step 2: Wire it** — in `main(argv)`, before building/parsing: `argv = route_argv(list(argv), KNOWN_SUBCOMMANDS)` where `KNOWN_SUBCOMMANDS` is a module constant built from the subparser names. Ensure `--export` / `--help` / `--version` continue to work (they are top-level options on the root parser).

- [ ] **Step 3: Run** `uv run pytest tests/test_cli_top_level_routing.py -v`. Expected: PASS.

### Task 2.3: Accept a bare positional prompt as the interactive initial message

**Files:**
- Modify: `src/pipy_harness/cli.py` (~1017–1020 where a bare positional in interactive is rejected as ambiguous; ~2137 one-shot switch logic)
- Test: `tests/test_harness_native_cli.py`

- [ ] **Step 1: Write the failing test** — drive the interactive path headlessly with a positional prompt and a fake provider, asserting the prompt becomes the first user message (use the existing fake-provider/PTY harness pattern in this file; mirror an existing interactive test's setup).

```python
def test_positional_prompt_seeds_interactive_first_message(tmp_path, monkeypatch):
    # Arrange: fake provider + non-TTY interactive run with a positional prompt.
    # Assert: the recorded session's first user entry text == "hello there".
    ...  # mirror existing interactive fake-provider test setup in this file
```

- [ ] **Step 2: Run** the test. Expected: FAIL (currently rejected as ambiguous).

- [ ] **Step 3: Implement** — in the interactive branch, when a positional prompt is present and mode is interactive text (not `--mode rpc`), seed it as the initial user message instead of raising the ambiguity error. Leave `--print`/`-p` one-shot and `--mode json` behavior unchanged; keep `--mode rpc` rejecting a positional prompt.

- [ ] **Step 4: Run** the test + `uv run pytest tests/test_harness_native_cli.py -q`. Expected: PASS.

### Task 2.4: Help-shape + gate + commit

- [ ] **Step 1:** Update the root parser description/usage so `pipy --help` reads as a single product command with subcommands secondary (closest argparse allows). Add a test asserting `pipy --help` exits 0 and mentions interactive usage.
- [ ] **Step 2:** `just check` → green.
- [ ] **Step 3:** Pi review loop → CLEAN (≤3 rounds).
- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Route bare pipy and positional prompts to the interactive session

Add a front-controller router so 'pipy' and 'pipy <prompt>' launch the product
REPL like Pi, while auth/run/repl/etc. stay reachable as subcommands. A bare
positional prompt seeds the interactive first message; subcommand names remain a
documented reserved-word exception."
```

---

## Slice 3 — Realign user-facing slash commands (deprecated aliases)

Goal: `/clear`→`/new`, `/status`→`/session`, `/theme`→`/settings` theme selection; drop `/skill`/`/template` wrappers; keep `/help` as `/hotkeys` alias.

### Task 3.1: Deprecated-alias dispatch test (test-first)

**Files:**
- Test: `tests/test_native_tool_loop_session.py` (mirror existing command-dispatch tests in this file)

- [ ] **Step 1: Write the failing tests** — for each alias, assert that dispatching it performs the target action and emits a one-line deprecation notice exactly once. Use the file's existing tool-loop dispatch harness.

```python
def test_clear_is_deprecated_alias_for_new():
    # dispatch "/clear"; assert it runs the /new action and the rendered output
    # contains a single deprecation notice naming "/new".
    ...

def test_status_is_deprecated_alias_for_session():
    ...
```

- [ ] **Step 2: Run** the tests. Expected: FAIL (aliases absent).

### Task 3.2: Implement `/clear` and `/status` aliases

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py` (command dispatch near `/new` ~2631 and `/session` ~2609; command-name builder `_tool_loop_command_names` ~584–604; descriptions builder ~750)
- Modify: `src/pipy_harness/native/repl_input.py` (completions/descriptions)

- [ ] **Step 1: Implement** `/clear` → emit deprecation notice "(`/clear` is deprecated; use `/new`)" then call the `/new` handler; `/status` → notice then `/session` handler. Add both to the command-name + description registries marked deprecated.

- [ ] **Step 2: Run** the Task 3.1 tests. Expected: PASS.

### Task 3.3: `/theme` → `/settings` theme selection alias

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py` (`/theme` handler ~2992–3003)
- Test: `tests/test_native_tool_loop_session.py`

- [ ] **Step 1: Write the failing test** — `/theme` (no arg) routes to settings theme selection and emits the deprecation notice; `/theme <name>` still applies the theme (back-compat) with the notice.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — make bare `/theme` open the settings theme selection (reuse the `/settings` path); keep `/theme <name>` applying via `select_theme` (already imported, used ~3003); both print the deprecation notice. Ensure `--theme`/`--no-themes` load flags are untouched.

- [ ] **Step 4: Run** → PASS.

### Task 3.4: Drop `/skill`/`/template` wrappers; inject skills; templates as own commands

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py` (`_tool_loop_command_names` ~599 inserts `/skill`,`/template`; custom-command dispatch)
- Modify: `src/pipy_harness/native/repl_input.py`
- Test: `tests/test_native_tool_loop_session.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_skill_wrapper_command_removed():
    # "/skill" is no longer a registered command name.
    ...
def test_prompt_template_registers_as_its_own_command():
    # a workspace prompt template named "plan" is invokable as "/plan".
    ...
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — remove the `["/skill", "/template"]` insertion (~599) and their handlers; register each discovered prompt template as its own `/<name>` command in the command registry; confirm discovered skills are already injected into the system prompt (they are, via `WorkspaceResources`) and add a regression assertion. Remove `/skill`/`/template` from `repl_input.py` registries.

- [ ] **Step 4: Run** → PASS.

### Task 3.5: `/help` alias + gate + commit

- [ ] **Step 1:** Confirm `/help` dispatches to the `/hotkeys` handler (~2127/2142); add a test asserting `/help` output equals `/hotkeys` output. Adjust if `/help` currently has separate content.
- [ ] **Step 2:** `just check` → green.
- [ ] **Step 3:** Pi review loop → CLEAN.
- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Realign pipy-only slash commands to Pi equivalents

/clear and /status become deprecated aliases of /new and /session; /theme routes
to settings theme selection; /skill//template wrappers are dropped in favor of
auto-injected skills and prompt-templates registered as their own /<name>
commands; /help stays an alias of /hotkeys."
```

---

## Slice 4 — Retire dead automation flags

Goal: remove `--native-output json` and `--archive-transcript`.

### Task 4.1: Removed-flag tests (test-first)

**Files:**
- Test: `tests/test_harness_native_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_native_output_flag_removed_points_to_mode_json():
    import subprocess, sys
    p = subprocess.run([sys.executable, "-m", "pipy_harness.cli", "run",
                        "--agent", "pipy-native", "--slug", "s", "--native-output", "json"],
                       capture_output=True, text=True)
    assert p.returncode != 0
    assert "--mode json" in (p.stderr + p.stdout)

def test_archive_transcript_flag_removed_points_to_session_tree():
    import subprocess, sys
    p = subprocess.run([sys.executable, "-m", "pipy_harness.cli", "repl",
                        "--archive-transcript"], capture_output=True, text=True)
    assert p.returncode != 0
    out = p.stderr + p.stdout
    assert "session" in out.lower() and "--mode json" not in out
```

- [ ] **Step 2: Run** → FAIL (flags still accepted).

### Task 4.2: Remove the flags + emit guidance

**Files:**
- Modify: `src/pipy_harness/cli.py` (`--native-output` ~169–178, consumer ~800/893–894, `_native_json_output()` ~1260–1301; `--archive-transcript` ~313–321, `TranscriptSink` wiring ~1945–1949)

- [ ] **Step 1: Implement** — delete both `add_argument` blocks and their consumers and `_native_json_output()` / `TranscriptSink` wiring. Add a small argparse error/epilog or explicit guard so the removed options produce the guidance asserted above (`--native-output` → "use --mode json"; `--archive-transcript` → "the native session tree is the transcript; use --export"). `pipy run` keeps its default record finalization.

- [ ] **Step 2: Run** the Task 4.1 tests + `uv run pytest tests/test_harness_cli.py tests/test_harness_native_cli.py -q`. Expected: PASS.

### Task 4.3: Gate + commit

- [ ] **Step 1:** `rg -n "native-output|native_output|archive-transcript|archive_transcript|TranscriptSink|_native_json_output" src` → no hits.
- [ ] **Step 2:** `just check` → green.
- [ ] **Step 3:** Pi review loop → CLEAN.
- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Remove --native-output json and --archive-transcript

Automation callers use --mode json; the native session tree is the transcript
(use --export). Both removed flags now emit guidance pointing to the replacement."
```

---

## Slice 5 — Docs / help / changelog sync + de-emphasize kept extras

Goal: docs match the realigned surface; kept internal conveniences de-emphasized.

### Task 5.1: Update parity docs

**Files:**
- Modify: `docs/parity-plan.md` (§1 slash-command rows for `/clear` `/status` `/theme` `/skill` `/template` `/help`; §2 top-level shape + `--native-output`/`--archive-transcript`; §3 mark rows removed/realigned), `docs/pi-mono-gap-audit.md` (§6 cleanup), `docs/pi-parity.md` (CLI realignment), `docs/settings-config.md` (theme via `/settings`, `/skill`//template realignment).

- [ ] **Step 1:** Edit each doc to describe the shipped behavior. In §3, change the affected rows' status from "remove/realign" plans to done. De-emphasize `--read-root(s)`, `--tool-budget`, `--input-runtime`, prompt-history as internal mechanisms (not parity features), keeping the existing code.
- [ ] **Step 2:** `just docs-build` → "No issues found".

### Task 5.2: CHANGELOG + final gate + commit

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]`)

- [ ] **Step 1:** Add `[Unreleased]` entries: removed (no-tool REPL + proposal/apply commands, `--repl-mode`, `--native-output json`, `--archive-transcript`); changed (bare `pipy`/positional prompt launches interactive; `/clear`//`/status`//`/theme` deprecated aliases; `/skill`//`/template` realigned).
- [ ] **Step 2:** `just check` → green; `just docs-build` → clean.
- [ ] **Step 3:** Pi review loop → CLEAN.
- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: sync parity docs and changelog with the CLI cleanup

Record the Pi-shape top-level dispatch, the retired no-tool REPL and dead
automation flags, the realigned slash commands, and de-emphasize the kept
internal conveniences (--read-root/--tool-budget/--input-runtime/prompt-history)."
```

---

## Self-Review

**Spec coverage:**
- Top-level Pi-shape dispatch (decision 1) → Slice 2 ✓ (router, positional prompt, reserved-word exception test).
- Removal policy pragmatic mix (decision 2): no-tool REPL + proposal/apply hard-remove → Slice 1 ✓; `--native-output json`/`--archive-transcript` → Slice 4 ✓; user-facing slash aliases → Slice 3 ✓.
- Kept extras de-emphasized (decision 3) → Slice 5 Task 5.1 ✓ (no code change, matches spec).
- Reserved-word exception → Slice 2 Task 2.1 covers `["auth"] -> ["auth"]` ✓.
- Slice-4 split error guidance (native-output → `--mode json`; archive-transcript → session tree) → Task 4.1 asserts both, distinctly ✓.

**Placeholder scan:** Test bodies in Tasks 2.3, 3.1, 3.3, 3.4, 3.5 are described rather than fully coded because they must mirror each file's existing fake-provider/PTY harness, which the executor reads at that step (TDD). This is an intentional "match the established test pattern" instruction, not a vague placeholder — each names the exact assertion and seam. All new standalone code (`route_argv`, routing matrix, registry/flag-removal tests) is complete.

**Type/name consistency:** `route_argv(argv, known_subcommands)` and `_TOP_LEVEL_ONLY_FLAGS` used consistently between Task 2.1 and 2.2. `KNOWN_SUBCOMMANDS` built once in `cli.py`. Alias targets (`/new`, `/session`, `/settings`) confirmed present in `tool_loop_session.py`.

**Note for executor:** line numbers are from the 2026-06-20 code map and will drift as earlier slices edit files; re-`rg` the symbol before each edit rather than trusting the line number.
