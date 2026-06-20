# Pi-parity follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Pi's skill-advertisement system-prompt block (model loads via the read tool), move theme selection into `/settings`, and remove the pipy-only `/theme`/`/clear`/`/status`/`/help` commands outright (no deprecations).

**Architecture:** Slice A makes `compose_skills_system_block` Pi-shaped (name+description+location) and injects it into the tool-loop system prompt when `read` is available, adding skill dirs to the read-only `reference_roots`. Slice B adds a theme row+picker to the `/settings` dialog and deletes `/theme`. Slice C hard-removes `/clear`/`/status`/`/help` and records the no-deprecation policy in `AGENTS.md`.

**Tech Stack:** Python 3 stdlib; existing `pipy_harness.native` runtime; pytest. No new deps.

**Source spec:** `docs/superpowers/specs/2026-06-20-pi-parity-followups-design.md`

**Working mode:** Sequential on `main`. One commit per slice. Run `just check` and the Pi review loop (`python3 ~/projects/agent-stuff/claude/skills/pi-review-loop/bin/pi-review-loop --repo "$PWD" --run-dir "$(mktemp -d)/pi-review"`) before each commit; commit a slice only when Pi returns CLEAN. Slice C also runs `just docs-build`.

---

## File Structure

- `src/pipy_harness/native/skills.py` — `SkillFile` (add absolute-path access), `compose_skills_system_block` (Pi format). Slice A.
- `src/pipy_harness/adapters/native.py` — inject the skill block into the composed system prompt (gated on `read` tool) and add skill dirs to `reference_roots`. Slice A.
- `src/pipy_harness/native/tool_loop_session.py` — settings dialog theme row+action (Slice B); remove `/theme`,`/clear`,`/status`,`/help` handlers (Slices B,C).
- `src/pipy_harness/native/repl_input.py` — registries: drop `/theme`,`/clear`,`/status`,`/help`. Slices B,C.
- `AGENTS.md` — no-deprecation policy note. Slice C.
- `docs/` + `CHANGELOG.md` — sync. Slice C.

Pre-flight each slice: `rg` the symbol/command before editing (line numbers drift).

---

## Slice A — Wire the skill advertisement (#11)

### Task A1: `SkillFile` exposes an absolute path

**Files:**
- Modify: `src/pipy_harness/native/skills.py` (`SkillFile` dataclass ~57-72; discovery `discover_workspace_skills`)
- Test: `tests/test_native_skills.py`

- [ ] **Step 1: Inspect** `SkillFile` and `discover_resource_files`/`discover_workspace_skills` to see what path data is available. The discovery reads real files, so an absolute `Path` is known at load time.

- [ ] **Step 2: Write the failing test**

```python
def test_skillfile_exposes_absolute_path(tmp_path):
    from pipy_harness.native.skills import discover_workspace_skills
    skill_dir = tmp_path / ".pipy" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "lint.md").write_text("---\nname: lint\ndescription: Lint the code\n---\nbody\n")
    skills, _ = discover_workspace_skills(workspace=tmp_path, config_home=tmp_path / "cfg")
    assert skills
    assert skills[0].absolute_path == (skill_dir / "lint.md").resolve()
```

- [ ] **Step 3: Run** `uv run pytest tests/test_native_skills.py::test_skillfile_exposes_absolute_path -v`. Expected: FAIL (no `absolute_path`).

- [ ] **Step 4: Implement** — add an `absolute_path: Path` field to `SkillFile`, populated from the discovered file's resolved path. Ensure `safe_skill_metadata` is NOT changed (it must keep emitting only path_label/sha256/byte_length/truncated — verify the absolute path never enters it).

- [ ] **Step 5: Run** the test + `uv run pytest tests/test_native_skills.py -q`. Expected: PASS.

### Task A2: `compose_skills_system_block` → Pi format with `<location>`

**Files:**
- Modify: `src/pipy_harness/native/skills.py` (`compose_skills_system_block` ~151-176, the `SKILLS_SYSTEM_BLOCK_*` templates)
- Test: `tests/test_native_skills.py`

- [ ] **Step 1: Write the failing test**

```python
def test_skills_block_pi_format_includes_location_and_escapes(tmp_path):
    from pipy_harness.native.skills import SkillFile, compose_skills_system_block
    from pathlib import Path
    s = SkillFile(name="x<y", description="a & b", path_label=".pipy/skills/x.md",
                  sha256="0"*64, byte_length=1, truncated=False,
                  absolute_path=Path("/abs/.pipy/skills/x.md"))
    block = compose_skills_system_block([s])
    assert "Use the read tool to load a skill" in block
    assert "<available_skills>" in block and "</available_skills>" in block
    assert "<name>x&lt;y</name>" in block
    assert "<description>a &amp; b</description>" in block
    assert "<location>/abs/.pipy/skills/x.md</location>" in block
    assert compose_skills_system_block([]) == ""
```

- [ ] **Step 2: Run** the test. Expected: FAIL.

- [ ] **Step 3: Implement** — rewrite `compose_skills_system_block` to emit Pi's shape: a header ("The following skills provide specialized instructions for specific tasks.", "Use the read tool to load a skill's file when the task matches its description.", and a note to resolve a skill's relative paths against its directory), then `<available_skills>` with per-skill `<name>`/`<description>`/`<location>` (absolute_path), XML-escaped (`&`,`<`,`>`,`"`,`'`). Empty → "".

- [ ] **Step 4: Run** the test + suite. Expected: PASS.

### Task A3: Inject the block (read-gated) + add skill dirs to reference_roots

**Files:**
- Modify: `src/pipy_harness/adapters/native.py` (`run()` ~255-300, after `compose_system_prompt`; the `reference_roots`/session construction ~294+)
- Test: `tests/test_pipy_native_tool_repl_adapter.py` (or a focused adapter/system-prompt test)

- [ ] **Step 1: Inspect** how the adapter obtains discovered skills. If the adapter does not yet receive `SkillFile`s, thread them in from the same discovery used for `/skill` (`WorkspaceResources`/`dispatch_resource_command` path) — find where skills are discovered for the session and make the discovered list available to `run()` before the system-prompt build.

- [ ] **Step 2: Write the failing tests**

```python
def test_system_prompt_includes_skill_block_when_read_available(...):
    # Build the tool-loop adapter with a discovered skill and a tool set that
    # includes `read`; assert the composed system prompt contains
    # "<available_skills>" and the skill's <location>.
    ...
def test_system_prompt_omits_skill_block_when_read_excluded(...):
    # Same, but the active tool set has no `read` tool; assert no
    # "<available_skills>" in the composed prompt.
    ...
def test_skill_dirs_added_to_reference_roots(...):
    # The session built by the adapter has the discovered skill's parent dir in
    # its reference_roots, so the read tool can load the skill body.
    ...
```

- [ ] **Step 3: Run** the tests. Expected: FAIL.

- [ ] **Step 4: Implement**
  - In `run()`, after `composed_system_prompt = compose_system_prompt(base_prompt, discovery)`, append the skill block **only if the `read` tool is present** in the active tool registry/set: `composed_system_prompt += compose_skills_system_block(skills)` (guard for empty).
  - Compute the unique set of discovered skill parent directories and add them to the `reference_roots` passed to `NativeToolReplSession` (union with any existing reference roots; dedupe; keep absolute, resolved).
  - Keep the workspace-context emit and archive metadata unchanged.

- [ ] **Step 5: Run** the tests + `uv run pytest tests/test_pipy_native_tool_repl_adapter.py tests/test_native_skills.py -q`. Expected: PASS.

### Task A4: Read-access integration + archive-boundary check, gate, commit

**Files:**
- Test: a focused integration test (mirror existing read-tool tests) proving the model can read a skill body outside cwd via reference_roots; archive-safe metadata unchanged.

- [ ] **Step 1: Write the test** — construct a session whose skill lives in a global dir outside the workspace; confirm the read tool can read that absolute path (because the dir is in reference_roots) and that a non-skill path outside cwd is still refused. Assert `safe_skill_metadata` for the skills still contains only path_label/sha256/byte_length/truncated.

- [ ] **Step 2: Run** the test. Expected: PASS (or implement minor wiring until it passes).

- [ ] **Step 3:** `just check` → green.

- [ ] **Step 4:** Pi review loop → CLEAN (≤3 rounds).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Wire the Pi-style skill advertisement into the system prompt

Advertise discovered skills (name, description, absolute location) in the
tool-loop system prompt when the read tool is available, matching Pi's
formatSkillsForPrompt, and add skill directories to the read-only reference
roots so the model can load skill bodies (incl. global skills outside cwd) via
the read tool. /skill is kept. The archive-safe skill metadata is unchanged."
```

---

## Slice B — Theme selection in `/settings`, remove `/theme` (#12)

### Task B1: Theme row + picker in the settings dialog

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py` (`_settings_dialog_rows` ~4730; `_drive_settings_dialog` loop ~4582-4660 where `action == "model"` opens `run_model_selector`)
- Test: `tests/test_native_tool_loop_session.py` / `test_native_tool_loop_tui.py`

- [ ] **Step 1: Inspect** the `action == "model"` branch (opens `run_model_selector`, applies selection) and the theme primitives (`select_theme`, the available-theme list used by the old `/theme` handler).

- [ ] **Step 2: Write the failing test** — drive `_drive_settings_dialog` (or its row builder + action handler) with a fake terminal_ui that selects the "theme" row then a theme; assert the chosen theme is applied (and persisted via settings, the source of truth). Mirror the existing model-selector dialog test.

- [ ] **Step 3: Run** → FAIL (no theme row/action).

- [ ] **Step 4: Implement** — add a "Theme" `SettingsRow`; add an `action == "theme"` branch that builds theme options and calls a theme selector (reuse `run_model_selector`-style flow or a `run_theme_selector`), then applies via `select_theme` and persists through settings. Keep the loop's re-open behavior.

- [ ] **Step 5: Run** → PASS.

### Task B2: Remove the `/theme` command

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py` (`/theme` handler ~2992-3003; command-name/description builders), `src/pipy_harness/native/repl_input.py` (registries)
- Test: `tests/test_native_tool_loop_session.py`, `tests/test_native_repl_input.py`

- [ ] **Step 1: Write the failing test**

```python
def test_theme_command_removed():
    from pipy_harness.native.repl_input import DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    assert "/theme" not in DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    # and dispatching "/theme" in the tool loop is an unknown command (mirror the
    # session's unknown-command test harness).
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — delete the `/theme` handler branch and its command-name/description entries; remove `/theme` from `repl_input.py` registries and any menu/help text. Leave `--theme`/`--no-themes`/`PIPY_THEME` untouched. Delete/adjust the old `/theme` command tests (theme switching is now tested via the settings dialog in B1).

- [ ] **Step 4: Run** the tests + suite. Expected: PASS.

### Task B3: Gate + commit

- [ ] **Step 1:** `rg -n '"/theme"|command_text == "/theme"' src` → no command handler remains (only `--theme` flag refs).
- [ ] **Step 2:** `just check` → green.
- [ ] **Step 3:** Pi review loop → CLEAN.
- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Move theme selection into /settings and remove /theme

Add a theme row and picker to the /settings dialog (matching Pi, which has no
/theme command); theme still persists via settings. Remove the pipy-only /theme
command. The --theme/--no-themes load flags are unchanged."
```

---

## Slice C — Pi-faithful command set + no-deprecation policy

### Task C1: Remove `/clear`, `/status`, `/help` (test-first)

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py` (`/help` handler ~2127; `/clear`,`/status` deprecated-alias branches added in cleanup Slice 3), `src/pipy_harness/native/repl_input.py` (registries), any reserved-name/menu lists
- Test: `tests/test_native_tool_loop_session.py`, `tests/test_native_repl_input.py`

- [ ] **Step 1: Write the failing test**

```python
def test_pipy_only_commands_removed():
    from pipy_harness.native.repl_input import DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    for gone in ("/clear", "/status", "/help"):
        assert gone not in DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    # and each dispatches as an unknown command in the tool loop (mirror harness).
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — delete the `/clear`→`/new`, `/status`→`/session`, and `/help`→`/hotkeys` alias branches (added in cleanup Slice 3) and the `/help` handler; remove all three from the `repl_input.py` registries, reserved-name sets, and any slash-menu/help text. `/new`, `/session`, `/hotkeys` remain canonical and unchanged. Update/remove the Slice-3 alias tests that asserted the deprecation behavior.

- [ ] **Step 4: Run** the tests + suite. Expected: PASS.

### Task C2: Record the no-deprecation policy in AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Implement** — add a short section, e.g.:

```markdown
## No-deprecation policy

pipy has no users yet and stays private until Pi parity is reached. Do not add
deprecation shims (aliases, deprecation notices) for pipy-only surfaces being
realigned to Pi — remove them outright and match Pi directly.
```

- [ ] **Step 2:** Confirm `AGENTS.md` still renders (it's plain Markdown).

### Task C3: Docs sync + gate + commit

**Files:**
- Modify: `docs/parity-plan.md` (§1: `/clear`,`/status`,`/theme`,`/help` now **removed**, not deprecated aliases; `/skill` advertisement now wired; §3 rows), `docs/pi-parity.md`, `docs/settings-config.md` (theme selectable in `/settings`; `/skill` advertisement), `CHANGELOG.md` `[Unreleased]`.

- [ ] **Step 1:** Edit each doc to state: `/clear`/`/status`/`/theme`/`/help` removed outright (no aliases); skills advertised in the system prompt + loaded via read tool; theme selection in `/settings`. Add CHANGELOG entries (Removed: `/theme`,`/clear`,`/status`,`/help`; Added: system-prompt skill advertisement, theme in `/settings`). Note the no-deprecation policy.
- [ ] **Step 2:** `just docs-build` → "No issues found".
- [ ] **Step 3:** `just check` → green.
- [ ] **Step 4:** Pi review loop → CLEAN.
- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Remove pipy-only /clear, /status, /help; record no-deprecation policy

Hard-remove the pipy-only /clear, /status, and /help commands (Pi has none;
equivalents are /new, /session, /hotkeys) with no deprecation shims, per the
no-users/private-until-parity policy now recorded in AGENTS.md. Sync parity docs
and CHANGELOG; pipy's slash set now matches Pi's plus /skill."
```

---

## Self-Review

**Spec coverage:**
- Skill advertisement Pi-format + location (#11) → A2 ✓; injection read-gated + reference_roots → A3 ✓; read-access + archive boundary → A4 ✓; `/skill` kept (no task removes it) ✓.
- Theme in `/settings` + remove `/theme` (#12) → B1, B2 ✓.
- Remove `/clear`/`/status`/`/help` → C1 ✓; AGENTS.md policy → C2 ✓; docs/CHANGELOG → C3 ✓.
- Out-of-scope items (`/skill:name` syntax, `disableModelInvocation`, kept-internal flags) → no tasks, correct.

**Placeholder scan:** A3/B1 test bodies are described (must mirror each file's existing dialog/adapter fake-provider harness, read at that step) rather than fully coded — an intentional "match the established pattern" instruction naming the exact assertion, not a vague placeholder. All standalone new code (A1/A2/B2/C1 tests + the skill-block format) is complete.

**Type/name consistency:** `SkillFile.absolute_path` defined in A1 and used in A2/A3; `compose_skills_system_block(skills)` signature unchanged (still takes `Sequence[SkillFile]`); `action == "theme"` branch + `_settings_dialog_rows` consistent in B1.

**Note for executor:** line numbers are from the 2026-06-20 post-cleanup tree and drift as slices edit files; re-`rg` the symbol before each edit.
