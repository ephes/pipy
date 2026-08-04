# God-File Decomposition Plan

Status: active. Wave 0 is test-only rule hardening and lands first; every later
slice lands individually with `just check` green and the old path deleted in the
same commit.

Date: 2026-08-03.

This plan closes the one axis where pipy measurably loses to `pi-mono`: shape.
pipy already wins on enforcement (one `type: ignore` across 87k source lines, a
C901 ratchet burned from 71 pinned files to 13, zero functions above logical
nesting depth 5, real-PTY tests with deadline semantics). But
`native/tui.py` is 7,210 lines holding a 4,746-line / 345-method /
43-field `ToolLoopTerminalUi`, and `native/tool_loop_session.py` is 6,171.
pi-mono's counterpart packages are *larger* in total (`packages/tui` 14,311
lines, `packages/agent` 10,436) with **no file above ~2,400 lines**. That is the
bar.

It supersedes the shape-related deferrals in the
[2026-07-30 comparative review remediation plan](2026-07-30-comparative-review-remediation-plan.md),
which explicitly postponed whole-file decomposition until measured ownership was
available "after A1". A1 landed; this is that measurement.

**Provenance.** Three independent architects each produced a complete target
layout from a different angle (state ownership, lifecycle phase, mirror pi-mono)
over five cohesion analyses that computed `self.*` field usage per method by AST
rather than inferring from names. A three-lens judge panel (implementability,
elegance, durability) scored them; state ownership won 2-1. The panel rejected
concrete errors in the winning proposal, recorded below.

**Scope ruling.** This repository is private, has no users, and has no CI. This
plan therefore proposes no CI job, no release process, and no external-oracle
testing. Those remain out of scope regardless of their merit.

Built from **Proposal 1 (state ownership)** — winner 2–1 (implementability, durability). Grafted: P3's pi-mirrored *names* and `repl/wiring.py` composition-root-as-value; P2's verified `_LiveExtensionUiDriver` split and unified attach (demoted to a late track). Dropped from P1: deleting `editor_state.py` / `overlay_state.py` / `extension_chrome_state.py` (that breaks `test_repository_forbidden_import_prefixes_are_known` via the stale entries at boundary-test lines 995–997 **and** relaxes `frame_renderer`'s rule, since `native.ui` is not in its forbidden list); the singular `native.extension` package (use the existing plural `native.extensions`); and the `overlays` package rule forbidding `terminal_driver` while its own `stack.py` needed raw mode.

**Governing rule:** the three dependency-neutral *state* modules stay and keep their maximal rules. Only **effects** move. Each widget owns its field subset as private fields on the effect module; `overlay_state.py` shrinks to the stack (~150L), `editor_state.py` to buffer/history/undo/paste + steering queue, `extension_chrome_state.py` splits internally into three records (`ExtensionChromeState`, `FooterBranchLedger`, `TerminalInputListenerLedger`) in one file — no rule edit.

## 1. Target layout

| Path | Owns | ~L | From |
|---|---|---|---|
| `native/ui/component.py` | `FrameLine`, `Region`/`KeyTarget`/`RepaintPort` Protocols, `OverlayHandle` | 110 | tui |
| `native/ui/screen.py` | `PaintLock` (constructed here, injected), paint core, inline-scrollback fields, raw key read, resize, `external_io_suspension`, the one `drive(owner)` loop | 420 | tui |
| `native/ui/autocomplete.py` | slash-menu + autocomplete state, providers, path completion, popup frames | 320 | tui |
| `native/ui/pending_messages.py` | steering/follow-up queue verbs | 90 | tui |
| `native/ui/clipboard_images.py` | clipboard paste + drag refs | 110 | tui |
| `native/ui/key_specs.py` | key-spec resolution helpers | 60 | tui |
| `native/ui/extension_chrome.py` | chrome region build/dispose/render, status/header/footer/title/widget verbs | 480 | tui |
| `native/ui/terminal_input_listeners.py` | listener ledger effects | 150 | tui |
| `native/ui/extension_generation.py` | **named teardown owner**: ordered participant sweep (`retire_generation`/`reconcile_generation`) | 180 | tui `clear_/reconcile_extension_chrome` |
| `native/ui/chrome_handoff.py` | chrome ownership transaction (18 methods, 0 `_terminal_ui` reads) | 430 | tui `_LiveExtensionUiDriver` |
| `native/ui/extension_chrome_driver.py` | 22-verb steady-state driver facade + `_GenerationExtensionUiDriver` | 250 | tui |
| `native/ui/components/input_editor.py` | buffer/history/undo effects, input frame, shared `apply_editing_key` | 280 | tui |
| `native/ui/components/transcript.py` | history blocks, live buffers, thinking visibility, commit/stream verbs | 540 | tui |
| `native/ui/components/tool_loop_renderer.py` | `_TuiToolLoopRenderer` (`AgentEventRenderer` impl) | 300 | tui |
| `native/ui/components/custom_entry_renderer.py` | `_CustomEntryRenderer` + run state (`_CustomEntryDiagnosticHost` deleted → `Callable[[str],None]`) | 430 | tui |
| `native/ui/components/model_selector.py` | model pool + render + keys + **rows** | 205 | tui + session |
| `native/ui/components/scoped_models_selector.py` | scoped pool + render + keys + opener | 185 | tui + session |
| `native/ui/components/tree_selector.py` | tree pool + render + keys + `_handle_tree_command` | 265 | tui + session |
| `native/ui/components/session_picker.py` | 14 `session_*` fields, 3 modes, rows, startup picker | 500 | tui + session |
| `native/ui/components/settings_dialog.py` | settings pool + render + keys + rows + drive loop + theme/trust/fold/thinking actions | 840 | tui + session |
| `native/ui/components/trust_selector.py` | trust prompts + `_handle_trust_command` | 235 | tui + session |
| `native/ui/components/custom_overlay.py` | custom overlay fields + `_CustomOverlayHandle` | 250 | tui |
| `native/ui/components/custom_editor.py` | 7 `_custom_editor_*` fields, wiring, keys, frame | 420 | tui |
| `native/ui/components/extension_prompts.py` | 4 modal components + runners + external editor | 400 | tui |
| `native/ui/components/footer.py` | footer region + git-branch poller effects | 180 | tui |
| `native/repl/diagnostics.py` | free `emit_diagnostic` / `copy_last_answer` / `last_assistant_answer` | 50 | session |
| `native/repl/loop_scope.py` | `_RunControlState`, `_ReplLoopScope`, snapshots | 190 | session |
| `native/repl/provider_selection.py` | **`provider_state` sole owner** + mutation protocol + `mutation_io_lock` | 560 | session |
| `native/repl/extension_operations.py` / `execution_projections.py` / `session_adapters.py` | per-op dispatch / turn projections / 16 adapters | 240/130/285 | session |
| `native/repl/local_shell.py` / `session_transfer.py` | `!`/`!!` shell / export-import-share | 200/210 | session |
| `native/repl/session_commands.py` / `provider_config_commands.py` / `command_router.py` | command families + fan-out | 330/240/70 | session |
| `native/repl/extension_bringup.py` / `reload.py` / `extension_attach.py` | startup attach / `/reload` driver / unified `attach_generation(predecessor=None)` | 310/300/430 | session |
| `native/repl/loop_step.py` | `step_once` as regions A–E behind `_TurnScope` | 620 | session |
| `native/repl/wiring.py` | ~8 phase functions each **returning a frozen record**; `SessionWiring` | 620 | session `run()` |
| `native/chrome.py` (grows) | absorbs footer composition; deletes 2 injected callables | 1176 | session |
| `native/extensions/*` (12 modules) | contracts, message_routing, custom_payloads, session_views, dispatch, tool_port, contribution_names, collectors, flag_tokens, provider_normalization, command_context, activation | 1570 max | extension_runtime |
| `native/tui.py` (residual) | 8 own fields + one handle per owner, `read_line`, `wait_for_active_turn_interrupt`, `start`, `is_supported` | **500** | — |
| `native/tool_loop_session.py` (residual) | dataclass surface, `__post_init__`, `provider_port`, `run()` = 2 lines | **420** | — |

**Projected largest file this plan produces:** `native/extensions/activation.py` ~1,570 (pi's counterpart `runner.ts` is 1,236) — comfortably under the 2,400 bar. Then `chrome.py` 1,176, `extensions/packages.py` 1,105, `ui/components/settings_dialog.py` 840. Repo-wide, `cli.py` (2,851) and `session.py` (2,488) become the largest files; both are out of scope.

## 2. The hard part — state that resists partitioning

1. **`_paint_lock` is reentrant by necessity.** `paint() → _paint_locked → _frame_snapshot → _standard_frame_inputs → _extension_*_lines` re-acquires it while running trusted extension factories; 19 readers, 15 in the chrome cluster. *Handling:* `ui/screen.py` constructs the single `RLock` behind a `PaintLock` newtype **with no default constructor** and injects it into extension_chrome, footer, custom_editor, transcript. "Forgot to inject" becomes a mypy error, not a hang. A plain `Lock` is unrepresentable.
2. **`session_state_lock` — one RLock, documented only in an inline comment.** Shared by keybindings, settings, coding_state, tool_capabilities, generation_ref, the delivery gate, queue/reference mutexes, so a worker's `set_active_tools` serializes against a `/reload`. *Handling:* `SessionStateLock` newtype, no default, threaded as an explicit typed parameter from `repl/wiring.py`. `provider_selection.py` pins the order against its private `mutation_io_lock` in its module docstring.
3. **The extension-generation teardown has no owner.** `clear_extension_chrome` (54L) atomically resets chrome + custom editor + autocomplete providers + thinking label across two critical sections with an unlocked disposal window running trusted code. *Handling:* `ui/extension_generation.py` — one field (`generation`) plus an **ordered module-level participant tuple**, each exposing `retire_generation()`/`reconcile_generation()`, with a test asserting the order. It lands *before* the chrome and custom-editor slices, which cannot separate until it exists.
4. **`ctl` is a two-party protocol.** `line`/`pending_prefill` (loop_step writes each iteration; `/model` and `/scoped-models` push a prefill for the next), `session_tree` (rebound wholesale by `/new /resume /fork /clone /import`, and the setter re-binds the mutation lock — a cached tree becomes a retired tree), `extension_in_agent_turn` (two writers, one reader in tui's custom-message router; a stuck-true flag routes extension messages as steering forever). *Handling:* `repl/loop_scope.py` holds `_RunControlState` and **every consumer takes the same instance** — never a copy. `pending_prefill` gets `push()`/`consume()`; `session_tree` only a lock-rebinding setter; `extension_in_agent_turn` only `enter_turn()`/`settle()`/`in_turn`.
5. **`provider_state` has two owners today** — declared on the session, written exclusively by `_ProviderMutationEffects` through `self.session.provider_state`. *Handling:* the field and all 16 methods move into `repl/provider_selection.py`; consumers read `current_thinking_level()` instead of the three-variant union.
6. **`self` is part of the extension contract.** `factory(self, theme, keybindings)` and `_CustomOverlayHandle(self)` pass the whole `ToolLoopTerminalUi`. Verified: accepted in every factory signature, read in none. *Decision, settled now:* keep passing the residual facade (it still holds the key loops, driver and owner handles); pass narrowed ports only to `_CustomOverlayHandle`.

## 2a. Ordering amendment (2026-08-03, after slice 1 landed)

**This section governs where it conflicts with section 3.** The design phase
measured field cohesion *inside* each god class but never measured outbound
dependencies *from* a candidate extraction back to it. Section 3's wave order
was written without that number, and it is close to inverted.

Measured with an AST closure walk (`closure(x)` = every top-level name in the
file that must move together with `x`):

| File | Top-level defs | Extractable without touching the god class | Blocked |
| --- | --- | --- | --- |
| `native/tui.py` | 59 | **54** | 4 |
| `native/tool_loop_session.py` | 44 | **41** | 2 |

Only four things in `tui.py` are genuinely blocked: `_TuiToolLoopRenderer`,
`run_project_trust_selector`, `run_startup_project_trust_selector`, and
`run_startup_session_picker`. Everything else is free today.

Two corrections follow.

**Section 3's slice 2 does not exist as written.** `FrameLine` already lives in
`native/frame_renderer.py`, and `Region`, `KeyTarget`, `RepaintPort` and
`OverlayHandle` appear nowhere in the tree. That slice is not a relocation; it is
an instruction to invent four Protocols with no implementors. The honest version
is to *derive* one port from the 20 members `_TuiToolLoopRenderer` actually uses
on `ToolLoopTerminalUi` (including the private `_driver`), and to do it only when
a blocked extraction needs it — not speculatively, and not before any free work.

**Section 3's slice 3 cannot move verbatim.** `_TuiToolLoopRenderer` reaches
those same 20 members, so relocating it under `native.ui` would violate the
back-edge rule slice 1 just added. The plan's own Wave 0 blocks the plan's own
next slice.

### Correction to this section, same day

The first pass of this measurement was itself wrong, and the error is worth
recording because it is easy to repeat. The dependency walk collected
`ast.Name` and `ast.Attribute` nodes, which **misses a string annotation**. Every
class here takes the god class as `ui: "ToolLoopTerminalUi"` — a quoted forward
reference, so an `ast.Constant`. The walk reported those classes as free when
they are not. Corrected numbers, from a conservative word-boundary scan of each
definition's own source text: `tui.py` is **42 free / 12 blocked**, not 54 / 4.

Closure size was also the wrong metric. What decides whether an extraction is
cheap is its **port surface**: how many members of the god class it actually
touches. A 120-line class that touches nothing is free; a 70-line class that
reaches four private fields is not.

| Extraction | Port surface | Closure | Verdict |
| --- | --- | --- | --- |
| `ui/key_specs.py` | 0 | 20L | **done** |
| `Extension{Select,Confirm,Input}Component` + `clip_plain` | 0 | ~110L | **done** |
| `_ExtensionEditorComponent` | **0** | 120L | free — take next |
| `_BuiltinAutocompleteProvider` | **1** (`cwd`) | 62L | free once it takes `cwd` |
| `_CustomOverlayHandle` | 5 | 70L | needs a port |
| `_CustomEntryRenderer` + companions | 7 | 395L | needs a port |
| `_LiveExtensionUiDriver` / `_GenerationExtensionUiDriver` | 22 | 667L | needs the large port |

So the original section 3 was right that a port has to come early — it was only
wrong about which port, and about inventing its contents. Derive each port from
the measured member list, smallest first, and let the three port-needing
extractions follow their own port rather than a speculative one.

The ordering method that survives: measure port surface, take zero-port
extractions first, then derive each remaining port from real usage.

Section 3's *target layout* is unaffected — the module list, the shared-state
analysis in section 2, and the success criteria in section 5 all held up under
checking. Only the ordering and the "near-free" labels were wrong.

## 3. Ordered slices

Take 3–5 per session. **[T]** touches `tui.py`, **[S]** touches `tool_loop_session.py` — these serialize; never run two in parallel.

> **Superseded in part.** The wave ordering below predates the dependency
> measurement in section 2a; where the two disagree, section 2a governs. The
> target layout, per-slice boundary-rule edits, and C901 handling below remain
> current.

**Wave 0 — rules first (free, no source churn)**

1. **Boundary hardening.** Edit only `tests/test_architecture_import_boundaries.py`: (a) add `"pipy_harness.native.ui",` immediately above each `"pipy_harness.native.tui",` in the six inner rules at lines **993** (`frame_renderer`), **1021** (`overlay_state`), **1055** (`session_tree_commands`), **1066** (`tool_renderers`), **1076** (`extensions`), **1108** (`extension_ui`); (b) add `"pipy_harness.native.tui",` to the `source_package="pipy_harness.native.ui"` rule's `forbidden_imports` at lines 982–989. Verified green today: none of those six modules imports `native.ui`, and neither `ui/rendering.py` nor `ui/state.py` imports `tui`. Without (a), relocating tui.py into `native.ui.*` is a silent six-rule weakening; (b) forbids the back-edge the whole program exists to break. No C901 impact.

**Wave 1 — near-free tui owners (zero C901 findings)**

2. **[T]** `ui/component.py` — Protocols + `FrameLine`. Pure declarations. 3. **[T]** `ui/components/tool_loop_renderer.py` — verbatim; already implements `ui/rendering.AgentEventRenderer`. 4. **[T]** `ui/components/transcript.py`. 5. **[T]** `ui/screen.py` + `PaintLock` newtype + the one `drive(owner)` loop. 6. **[T]** `ui/autocomplete.py` + `ui/components/input_editor.py`. 7. **[T]** `ui/pending_messages.py` + `ui/clipboard_images.py` + `ui/key_specs.py`.

Each is a single owner's fields + effects; `dataclass(slots=True)` + mypy strict turns every missed property rebind into a check failure. Delete that owner's property projections **in the same slice**. No pyproject edit; tui.py keeps its existing pin covering fewer functions each time.

**Wave 2 — repl tier (free moves)**

8. **[S]** `repl/diagnostics.py`. Creates the package; add `"pipy_harness.native.repl"` beside every `"pipy_harness.native.tool_loop_session"` (16 sites: 115, 164, 222, 759, 792, 815, 986, 1000, 1023, 1045, 1054, 1065, 1079, 1089, 1107, 2166) in the same commit, plus a new rule `source_package="pipy_harness.native.repl", forbidden_imports=("pipy_session",)` (verified: zero `pipy_session` refs today; do **not** forbid `pipy_harness.capture` or `native.automation`, which are genuinely imported). Deletes `tui._CustomEntryDiagnosticHost`. Blast radius: 4 src + 7 test files.
9. **[S]** `repl/loop_scope.py` (delete the dead `extension_notify` field). 10. **[S]** `chrome.py` absorbs footer composition; deletes two injected-callable fields. 11. **[S]** `repl/local_shell.py` + `session_transfer.py`. 12. **[S]** `repl/extension_operations.py` + `execution_projections.py` + `session_adapters.py` (none has a `session` or `ctl` field). 13. **[S]** `repl/provider_selection.py`.

**Wave 3 — widgets (dual-file [T][S], widest slices)**

14. model_selector + scoped_models_selector. 15. tree_selector. 16. session_picker — burn `_handle_session_picker_key` (20) **in tui.py first**, splitting on `session_mode`; rename/delete handlers already exist. 17. settings_dialog — burn `run_settings_dialog` (11) and `_drive_settings_dialog` (16) first; the latter is already a `_local_action` dispatch. 18. trust_selector + startup prompts (`cli.py` repoints). 19. **[T]** `ui/extension_generation.py` (teardown owner) — must precede 20–21. 20. **[T]** custom_overlay + extension_prompts (burn 17, 11, 14). 21. **[T]** custom_editor (burn `_handle_custom_editor_key` 28, split by key class). 22. **[T]** extension_chrome + footer + terminal_input_listeners (burn 11). 23. **[T]** chrome_handoff + extension_chrome_driver (burn 14, 11) — the P2 split; the apply-port `ExtensionChromeSink(self._deliver_chrome_event)` already exists in `__init__`.

Findings 11/17/20 largely dissolve once `screen.drive(owner)` replaces six hand-rolled raw-key loops — the burn *is* the move.

**Wave 4 — repl apex**

24. **[S]** command families + router. 25. **[S]** `repl/extension_bringup.py` + `reload.py` (burn `_reload_extension_generation` 16 along activate → prepare → publish → retire). 26. **[S]** `repl/loop_step.py` (burn `step_once` 30 into regions A–E behind `_TurnScope`). 27. **[S]** `repl/wiring.py` — run() as ~8 value-returning phase functions; `run()` becomes two lines. `run`'s C901 40 dissolves per-phase (4–8 band); **no pin for wiring.py** — if a phase measures >10 it splits again. **Delete `"src/pipy_harness/native/tool_loop_session.py"` from per-file-ignores.** Lower the `run < 800` ast-line bound in `tests/test_architecture_agent_loop_boundaries.py:643` in every slice that shrinks run(); final value ~80.
28. **[S]** `repl/extension_attach.py` — unify the startup and `/reload` attach into `attach_generation(predecessor=None)`. **Behavior-affecting**, not a move; schedule last in this wave with the full extension-reload scenario suite.

**Wave 5 — extensions/** 29–33. Convert `native/extensions.py` → `native/extensions/packages.py`; then leaf cuts (`flag_tokens`, `provider_normalization`, `session_views`, `dispatch`, `tool_port`, `contribution_names`, `collectors`, `custom_payloads`, `command_context`, `message_routing`, `contracts`), residual `activation.py` last. Destination is the **plural** `native.extensions`: already in `_PLANNED_IMPORT_PREFIXES` (line 1124), already a rule source (1073) with a *stricter* list than `extension_runtime`'s, already paired everywhere — zero forbidden-list additions and three edges tightened. The commit that deletes `extension_runtime.py` must remove all 21 stale `"pipy_harness.native.extension_runtime"` forbidden entries and the `extension_runtime` member of the four-module host tuple. Re-read `tests/test_native_extension_activation_sealing.py`: it monkeypatches module-object attributes (`_r1_*`) and embeds the literal import string in generated source — invisible to grep and mypy.

**Wave 6 — the tui pin.** 34. **[T]** Collapse the ~60 duplicated editing-key lines in `read_line` (39) and `wait_for_active_turn_interrupt` (35) into `input_editor.apply_editing_key`. Succeed → delete tui.py's pin. Fall short → tui.py keeps its **existing** pin now covering 2 functions instead of 11; no pyproject edit either way. **Zero new pins, ever.**

## 4. Explicitly out of scope

- `cli.py` (2,851) and `session.py` (2,488) — the two largest files after this plan. Separate track; absorbing them here would double the program.
- Renaming for taste, arbitrary sub-packaging quotas, "N top-level modules" targets.
- Growing `ui/__init__.py`: it is a small curated barrel for the reducer/adapter pair and must not gain the new modules. No `ui/utils.py`, ever — that is pi's own accretion sink.
- Copying pi's `TuiBase` strategy hierarchy (pipy has one paint strategy), retained mutable component tree with `invalidate()` (pipy's immutable `FrameSnapshot` under a reentrant RLock is strictly better), module-level mutable singletons, or `index.ts`-style barrels.
- CI jobs, nightly runs, release process, external oracles. No deprecation shims, re-export modules, or compatibility aliases — the old path dies in the same commit.
- Splitting `extensions/activation.py` below ~1,570: its state machine is guarded inside `_ActivationApi` but driven from outside via module-level capability tokens; subdividing exports a private capability.

## 5. How to tell it worked

1. **Field ownership is machine-checkable.** New test: for every field of `ToolLoopTerminalUi`, `_RunControlState`, and `NativeToolReplSession`, `grep -rn "self\._<field>" src/` returns hits in **exactly one module**. Set the bound at **1**, not at "no worse than today."
2. **Size ratchet** (both files regrew ~14% last time, so pin the *value*, not the trend): a test asserting `tui.py ≤ 550` and `tool_loop_session.py ≤ 450` ast-lines once Wave 4 lands, plus **no file under `src/pipy_harness/native/` above 1,700 lines**. Lower each bound in any slice that shrinks the file; never raise one.
3. **`run()` ast-line bound** at `tests/test_architecture_agent_loop_boundaries.py:643` reaches ~80 (from 787/800 today), and `run()` constructs no collaborator — `repl/wiring.py` returns a frozen `SessionWiring`.
4. **C901 baseline: 13 pinned files → 11 or 12.** `tool_loop_session.py` removed; `tui.py` removed or covering 2 functions. **Zero entries added** across the whole program.
5. **Boundary rules strictly stronger:** `native.ui` forbidden everywhere `native.tui` is (six new sites); `native.ui` may not import `native.tui`, `native.repl`, `native.tool_loop_session`; `native.repl` forbidden everywhere `tool_loop_session` is; `extension_runtime` entries deleted with the file; `_PLANNED_IMPORT_PREFIXES` unchanged except as noted. No rule relaxed anywhere.
6. **Locks are types, not comments:** `grep -c "RLock()" src/pipy_harness/native/` shows exactly one construction site for `PaintLock` (`ui/screen.py`) and one for `SessionStateLock` (`repl/wiring.py`), each with no default parameter anywhere.
7. **Parity diffs are file-to-file:** pi's `components/session-selector.ts` ↔ `ui/components/session_picker.py`, `core/model-runtime.ts` ↔ `repl/provider_selection.py`, `core/bash-executor.ts` ↔ `repl/local_shell.py`, `agent-loop.ts` ↔ `repl/loop_step.py`, `main.ts` ↔ `repl/wiring.py`, `core/extensions/runner.ts` ↔ `extensions/activation.py`.
8. `just check` green on every commit, with the old path deleted in the same commit — no slice depends on the next.