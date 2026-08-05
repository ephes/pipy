# God-File Decomposition Plan

Status: active. Sections 2a, 3, 3a and 3b were re-derived from measurement on
2026-08-04 and supersede section 1's line estimates and the original wave order.
Section 2d (also 2026-08-04) re-measures the tui side and extension_runtime.py,
adds the class-level ratchet, corrects the pi-mono bar's scope, and fixes the
slice-44/45/48 rename decisions (`CodingSession`, `TerminalUi`).
Wave 0 is test-only rule hardening and landed first; every later
slice lands individually with `just check` green and the old path deleted in the
same commit.

**Checkpoint (2026-08-05, after slice 34).** The post-34 good stopping
point is reached: all planned widget/extension leaf owners through slice 34
have definition-site ownership. Current measurements are `native/tui.py` 2,093
lines; `ToolLoopTerminalUi` 1,438 AST-line span / 63 defs / 25 fields;
`native/tool_loop_session.py` 2,006 lines; and `native/extension_runtime.py`
2,000 lines. The native-module ceiling is 2,488, currently set by
`native/session.py`. The next dependency-ordered slice is 36, then 39 and the
strict 40–49 endgame. The residual `extension_runtime.py` is 50 lines above
§2d's projected slice-46 upper edge before slice 46's final activation
rename/import cleanup; this is measured drift, not a failure, and does not
change the accepted 1,750–1,950 final activation range until slice 46 is
measured.

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
| `native/tui.py` (residual) | 8 own fields + one handle per owner, `read_line`, `wait_for_active_turn_interrupt`, `start`, `is_supported` | ~~500~~ **720** (see 3a) | — |
| `native/tool_loop_session.py` (residual) | dataclass surface, `__post_init__`, `provider_port`, `run()` = 2 lines | **420** | — |

**Projected largest file this plan produces:** `native/extensions/activation.py` ~1,570 (pi's counterpart `runner.ts` is 1,236) — comfortably under the 2,400 bar. Then `chrome.py` 1,176, `extensions/packages.py` 1,105, `ui/components/settings_dialog.py` 840. Repo-wide, `cli.py` (2,851) and `session.py` (2,488) become the largest files; both are out of scope.

## 2. The hard part — state that resists partitioning

1. **`_paint_lock` is reentrant by necessity.** `paint() → _paint_locked → _frame_snapshot → _standard_frame_inputs → _extension_*_lines` re-acquires it while running trusted extension factories; 19 readers, 15 in the chrome cluster. *Handling:* `ui/screen.py` constructs the single `RLock` behind a `PaintLock` newtype **with no default constructor** and injects it into extension_chrome, footer, custom_editor, transcript. "Forgot to inject" becomes a mypy error, not a hang. A plain `Lock` is unrepresentable.
2. **`session_state_lock` — one RLock, documented only in an inline comment.** Shared by keybindings, settings, coding_state, tool_capabilities, generation_ref, the delivery gate, queue/reference mutexes, so a worker's `set_active_tools` serializes against a `/reload`. *Handling:* `SessionStateLock` newtype, no default, threaded as an explicit typed parameter from `repl/wiring.py`. `provider_selection.py` pins the order against its private `mutation_io_lock` in its module docstring.
3. **The extension-generation teardown has no owner.** `clear_extension_chrome` (54L) atomically resets chrome + custom editor + autocomplete providers + thinking label across two critical sections with an unlocked disposal window running trusted code. *Handling:* `ui/extension_generation.py` — one field (`generation`) plus an **ordered module-level participant tuple**, each exposing `retire_generation()`/`reconcile_generation()`, with a test asserting the order. It lands *before* the chrome and custom-editor slices, which cannot separate until it exists.
4. **`ctl` is a two-party protocol.** `line`/`pending_prefill` (loop_step writes each iteration; `/model` and `/scoped-models` push a prefill for the next), `session_tree` (rebound wholesale by `/new /resume /fork /clone /import`, and the setter re-binds the mutation lock — a cached tree becomes a retired tree), `extension_in_agent_turn` (two writers, one reader in tui's custom-message router; a stuck-true flag routes extension messages as steering forever). *Handling:* `repl/loop_scope.py` holds `_RunControlState` and **every consumer takes the same instance** — never a copy. `pending_prefill` gets `push()`/`consume()`; `session_tree` only a lock-rebinding setter; `extension_in_agent_turn` only `enter_turn()`/`settle()`/`in_turn`.
5. **`provider_state` has two owners today** — declared on the session, written exclusively by `_ProviderMutationEffects` through `self.session.provider_state`. *Handling:* the field and all 16 methods move into `repl/provider_selection.py`; consumers read `current_thinking_level()` instead of the three-variant union.
6. **`self` is part of the extension contract.** `factory(self, theme, keybindings)` and `_CustomOverlayHandle(self)` pass the whole `ToolLoopTerminalUi`. Verified: accepted in every factory signature, read in none. *Decision, settled now:* keep passing the residual facade (it still holds the key loops, driver and owner handles); pass narrowed ports only to `_CustomOverlayHandle`.

## 2a. Measured re-plan (2026-08-04) — supersedes the 2026-08-03 amendment

**This section governs where it conflicts with section 3.** Numbers come from a typed-receiver
resolver that reads string annotations verbatim (`ast.Constant` → `.value`), resolves quoted *field*
and *return* annotations, follows `getattr(x, "lit")`, cross-checked against a word-boundary sweep
of each definition's own source. Both failure directions were observed and cleared by hand.

### What the measurement says

`port` = distinct god-class members touched. `eff` = port minus `@property` projections onto an
existing record, static/class methods, instance-free methods, and read-only injected values.
`cx` = ruff `--isolated --select C901` (threshold 10; no pin follows moved code).

| Extraction | file | L | port | eff | cx | illusion? |
|---|---|---|---|---|---|---|
| `_LiveExtensionUiDriver` L351-791 band | tui | 441 | **0** | 0 | 14 | n/a — all 24 touches sit in the L793-925 binding, which stays |
| `_GenerationExtensionUiDriver` | tui | 51 | 0 | 0 | 1 | **inverted** — `__getattr__` proxy; never moves |
| `_CustomEntryRenderer` + companions | tui | 421 | 7 | 5→**0** | 6 | yes, by schedule |
| `_TuiToolLoopRenderer` | tui | 281 | 20 | 16→**0** | 8 | yes — 4 `@property`→`_chrome`, 13 transcript verbs |
| `_ExtensionChromeTuiHandle` | tui | 26 | 1 | 1 | 3 | no — real 10-line method |
| `_CustomEditorKeybindings` | tui | 52 | 1 | 1 | 2 | no — real but degenerate field write |
| `run_project_trust_selector` | tui | 78 | 1 | 1 | 6 | no — `run_settings_dialog` is a real 71-line method |
| `run_startup_*` (trust, picker) | tui | 68 | 1 / 2 | 1 / 2 | 5 | no — and both **construct** the god class |
| `_ReplLoopStep` | session | 566 | 7 | **0** | 30 | yes — 4 free functions + 3 never-written scalars |
| `_ProviderConfigurationCommandEffects` | session | 226 | 8 | **0** | 9 | yes — 9 of 18 touches are one `@staticmethod` |
| `_TransferCommandEffects` | session | 79 | 3 | **0** | 5 | yes — all three are static/classmethods |
| `_ReloadCommandEffects` | session | 352 | 4 | **1** | 16 | no — `_maybe_save_implicit_trust_after_reload` |
| `_ProviderMutationEffects` | session | 515 | 2 | **0** | 7 | yes — `provider_state` is never assigned |
| `_SessionCollaborators` / `_SessionCommandEffects` | session | 377/304 | 1/2 | **0** | 5/8 | yes |
| `_ExtensionRuntime` + `_routing_for_activation_batch` | ext_rt | 68 | 1 | **0** | 7 | yes — `_ActivationApi`'s only `@property` |
| activation lifecycle band | ext_rt | ~300 | 2-4 | 2-4 | 9 | no — unbound dispatch under capability tokens; **no port derivable** |

Of 110 top-level defs in `extension_runtime.py`, 5 have non-zero effective port and all 5 are one
lifecycle band. Across `tool_loop_session.py` exactly **one** member is a genuine mutable edge:
`auto_trust_on_reload_cwd` (read L5461, written L5470/L5480).

### The ports

Five survive; none is a Protocol restating private god names.

1. **`ExtensionChromeDelivery`** — *already exists*, `extension_chrome_state.py:65`,
   `Callable[[ExtensionChromeEvent], object]`. One member, `__call__(event) -> object`, passed as an
   opaque value at tui L359/422/440/553/556; already the parameter type of
   `ExtensionChromeSink.attach` (:217) and `.reconcile_attached` (:278). Shell:
   `_LiveExtensionUiDriver` keeps its name, module, constructor and full public surface, holding
   `self._chrome = ExtensionChromeRouter(self._deliver_chrome_event)`.
2. **`PaintLock`** — newtype over the single `RLock`, **no default constructor**, defined in
   `ui/paint_lock.py` (slice 5), constructed once in `ui/screen.py` (slice 43). Every ui owner takes
   `(record, PaintLock, repaint: Callable[[], None])` — the landed `CustomOverlayHandle` shape.
3. **`Callable[[], None]`** for `_ExtensionChromeTuiHandle` (`request_extension_chrome_render`) and
   **`Callable[[str], None]`** for `_CustomEditorKeybindings` (`_queue_custom_editor_action`, a
   2-line write of plain field `_custom_editor_action`, L1646) — plain constructor parameters. The
   second is *retired* at slice 24 when `custom_editor.py` owns the record.
4. **`ImplicitTrustState`** — one-field mutable record (resolved cwd) built in `__post_init__` and
   injected; replaces the last real port member in `tool_loop_session.py`.
5. **`_ReplLoopScope` widened** — delete `session: "NativeToolReplSession"` (L2794, the sole god
   edge, entered once at L2863); add `abort_event`, `file_reference_roots`, `tool_budget`, none ever
   assigned on this class. Name it `file_reference_roots`: `image_reference_roots` is *derived* from
   it under a different clipboard policy and both are consumed 14 lines apart under the same
   parameter name. ~~Do **not** add `provider_state` — the record already carries
   `cycle_thinking_level` (L2821), which runs the identical guard.~~ **Corrected in execution: the
   record carries `provider_state` too, a fourth field.** The guard really is identical --
   `_ProviderMutationEffects.cycle_thinking_level` runs the same
   `isinstance(state, NativeReplProviderState)` check and returns `None` -- but the *diagnostic* is
   not. `cycle_thinking_level_action` says "thinking-level cycling is unavailable for this REPL
   state" for a static provider state and "current model does not support thinking" for a model
   without reasoning. Collapsing onto the callable would have replaced the first message with the
   second. A guard being duplicated is not proof the duplicate is redundant; check what each arm
   *emits* before merging them.

**Dissolved ports.** `CustomEntryTerminalPort` (5) and the `_TuiToolLoopRenderer` port (16) vanish
because `components/transcript.py` lands first and owns `_history_blocks` and the commit verbs while
`ui/screen.py` owns the live `(width, expanded, stream)` triple; the renderer's four `@property`
reads of `_chrome` (L2094/2102/2158/2166) are **deleted** and the component reads
`ExtensionChromeState` directly. The 24-member driver port dissolves by cutting at the seam the code
already has; `_ActivationApi`'s sole `@property` by threading the `GenerationMessageRouting` record;
everything in `tool_loop_session.py` bar `auto_trust_on_reload_cwd` into free functions and records.

### Two facts that decide destinations and no port count shows

- **`native.ui` may never name `ToolLoopTerminalUi`.** The rule at
  `tests/test_architecture_import_boundaries.py:982-997` forbids `native.tui`, and the module
  docstring (L12-15) plus a plain `ast` walk confirm TYPE_CHECKING and function-local imports are
  scanned. So (a) `run_startup_project_trust_selector` (constructs at L6671) and
  `run_startup_session_picker` (L6716) need `native/startup_selectors.py`; (b) every "tui + session"
  row in section 1 is **two** modules — `_drive_settings_dialog` (L5515), `_handle_trust_command`
  (L5409), `_open_scoped_models_overlay` (L5639), `_open_default_project_trust_selector` (L5731),
  `_maybe_save_implicit_trust_after_reload` (L5455), `_toggle_view_fold` (L5136),
  `_cycle_thinking_level` (L5182) all annotate `terminal_ui: ToolLoopTerminalUi`, so their half
  lands under `native/repl/`, which may import `native.tui`. There is no `repl/hotkeys.py`.
- **`ui/screen.py` is a fan-out hub.** `_frame_snapshot` (L5001) reads eight live buffers,
  `_history_blocks`, `input_text`, `_effective_input_cursor()` and `_overlays`;
  `_standard_frame_inputs` (L5050) calls eight `*_lines` owners. Extracted early its port exceeds
  20, so it goes **last** among tui owners, taking an ordered contributor tuple.

## 2b. Placement and ordering rules learned in execution (2026-08-04)

Nine slices in, the measured structure in 2a has held up under verification --
the driver band really is zero-port, the `getattr` sites really were invisible.
Two *other* kinds of error recurred, and both are cheap to prevent.

**Placement rule: check the caller's tier before putting anything under `repl/`.**
Section 3 assigns modules to `native/repl/` by what they do. Twice that was wrong
for the same reason: `emit_diagnostic` is called from `tui.py`, which may not
import `native.repl`, and `production_tool_registry` is called from
`native/__init__.py`, `adapters/native.py` and `cli.py`, three tiers below the
session. Both belong in leaves (`native/diagnostics.py`,
`native/tools/registry.py`). Before placing a module under `repl/`, list its
callers; if any caller sits below the session tier, it is a leaf, not a REPL
module.

**Ordering rule: a candidate can be blocked by its siblings, not just by the god
class.** Slice 3 bundled `_BuiltinCommandInterpreter`, whose session-port is
zero but which names all eight command-effect families still in the file. Port
surface answers "does this need the god class"; it does not answer "is everything
this needs already extracted".

**Measurement rule: a non-zero port is often just a private companion.** Section
3 lists slice 20's `_run_local_shell_shortcut` at port *none*; measured, its
self-port is 1 -- `self._execute_local_shell`, a 69-line private method whose own
port is two class constants. Moving the pair takes the port back to zero. The
same shape covers most of the remaining session methods (`_footer_text` →
`_effort_label` + `_estimated_context_tokens`, `_model_selector_rows` →
`_selection_supports_tool_calls`, `_handle_tree_command` →
`_run_interactive_tree_selector`, `_drive_settings_dialog` → five). Before
declaring a candidate blocked, resolve its port members: a private method
reachable only from the candidate is part of the candidate, not a port.

**Renaming rule: dropping the underscore can shadow what the method delegates
to.** `_handle_tree_command` was a thin adapter around `handle_tree_command`
imported from `session_tree_commands`. Renamed to the public form on extraction,
the new module-level `def` captured its own call site -- the wrapper now called
itself. mypy caught it only because the signatures differ; matching signatures
would have shipped unbounded recursion, and `ruff --fix` had already deleted the
shadowed import as "unused". Before renaming an extracted definition, check the
target name against the new module's imports. It became `run_tree_command`. Same
root cause as failed approach 5 -- one name meaning two things -- arrived at from
the rename direction rather than the regex direction.

**A leaf reached by both the moving code and the code that stays needs one
owner, not two copies.** Duplicating it is how the duplicate `PRICING_TABLE`
shipped. Give it a home in a tier both can import, and move it in the same
slice: `CANCEL_JOIN_TIMEOUT_SECONDS`, `finish_chrome_retirement` and
`raise_first` went to `repl/turn_leaves.py` beside the interrupt translation
they belong with, and the slash-menu builders to `repl/command_menu.py`, because
both startup and `/reload` rebuild the menu. Expect one or two of these per
slice; they are not scope creep, they are the cost of the boundary rule.

**Splitting a `try/finally` method: the shared locals are the contract.** The
182-line `_reload_extension_generation` had four locals set at three different
depths that only its `finally` clause read. Splitting it into phases means those
locals cannot stay locals -- they became one mutable `_ReloadAttempt` record the
phases write and the teardown reads, which is what keeps the teardown seeing
exactly what it saw before. Verify such a split by diffing the *ordered
statement list* of the original against the concatenated phases, not by reading:
every original statement must appear, in order, with only the rebinding and the
phase-dispatch statements added.

**Measure `self.session` only, and include `getattr`.** The table below was
re-derived on 2026-08-04 after the loop-scope slice, and the previous version of
it was wrong in three places for two reasons, both already on the failed-approach
list:

- A `session\.` text scan matches `native_session.name` and
  `coding_session.rebuild_active_history`. An `ast.Name(id="session")` scan
  matches a comprehension variable -- `_SessionCommandEffects` has
  `[session for session in sessions if session.name]`. Only
  `self.session.<member>` is the field.
- `_ReloadCommandEffects` reaches `provider_state` through
  `getattr(self.session, "provider_state", None)` (L1348), invisible to every
  attribute walk. Its port is 4, not 3. This is the same defect as the driver's
  `getattr(self._terminal_ui, ...)` in 3b item 1, found a second time; a port
  measurement that does not resolve literal `getattr`/`hasattr`/`setattr` names
  is not a measurement.

| Family | Lines | Session-port | Also blocked by |
| --- | --- | --- | --- |
| `_SessionCollaborators` | 378 | **0** | forwards the session *by value* into four sibling families (L2961/2986/3007/3040) |
| `_BuiltinCommandInterpreter` | 46 | **0** | names 8 sibling families still in the file |
| `_ProviderMutationEffects` | 516 | 1 (`provider_state`, 7 reads, 0 writes) | -- |
| `_SessionCommandEffects` | 305 | 2 (`_handle_tree_command`, `_run_interactive_session_picker`) | -- |
| `_TransferCommandEffects` | 80 | 3 (`_export_session`, `_import_session`, `_share_native_session_command`) | -- |
| `_ReloadCommandEffects` | 353 | 4 (`_maybe_save_implicit_trust_after_reload`, `tool_registry`, `verbose_startup`, `getattr` `provider_state`) | -- |
| `_ProviderConfigurationCommandEffects` | 223 | 7 | -- |

Every one of these declares `session: NativeToolReplSession` as a *field*, so
each is blocked by the boundary rule and not merely by coupling. A family whose
port is entirely session *methods* unblocks by moving those methods out first; a
family whose port is session *data* unblocks by taking that data as a
constructor parameter. `_SessionCollaborators` is last of the group regardless of
its zero port, because it is the thing that hands the god object to the others.

`_SessionExtensionOperations` (207) and `_ReplLoopStep`'s session edge are both
closed: the first landed at `repl/extension_operations.py`, and the second
dissolved into four record fields when `ReplLoopScope` moved to
`repl/loop_scope.py`.

## 2c. Execution status (2026-08-04, after 35 slices)

| File | Start | Now | Target |
|---|---|---|---|
| `native/tool_loop_session.py` | 6,171 | **2,017** | ~400 |
| `native/tui.py` | 7,210 | 6,288 | ~720 |
| `native/extension_runtime.py` | 4,131 | 4,131 | deleted |

**The session side is nearly done and the boundary now bites.** `native/repl/`
holds 15 modules (5,012 lines). No module under it makes a code-level reference
to `NativeToolReplSession` -- the three remaining mentions are docstring prose --
so the rule forbidding `native.repl` → `tool_loop_session` is load-bearing rather
than aspirational. Every command family, the run scope, the mutable control
state, the collaborators factory and the command router are out.

**What is left in `tool_loop_session.py`:** `_ReplLoopStep` (569 lines,
`step_once` complexity 30, slice 41 → `repl/loop_step.py`), `NativeToolReplSession`
itself (1,088 lines, `run` complexity 40, slice 44 → `repl/wiring.py`), plus
`_ExtensionCustomEntryRunState` (27) and `_build_detached_reload_effects` (8).
Both remaining C901 pins on this file are those two functions; deleting the pin
needs both splits.

**Ordering that held up.** Section 2b's sibling rule was right twice more:
`_SessionCollaborators` and `_BuiltinCommandInterpreter` sat at session-port zero
for eight slices, blocked entirely by families that had not moved, and both
moved byte-identical the moment the last sibling left. The measured-port order
was otherwise followed exactly and did not need revising.

**The tui side and `extension_runtime.py` are untouched.** Neither has been
measured under the corrected methodology (`self.<member>` only, plus literal
`getattr`/`hasattr`/`setattr`). Re-measure before planning either.

## 2d. Tui-side and extension-runtime re-measurement (2026-08-04) — supersedes §2c's "untouched/unmeasured" rows and governs §3 where it conflicts

Measured at `bf1122d` with the corrected methodology (§2a/§2b: `self.<member>`
only; literal `getattr`/`hasattr`/`setattr` resolved; string annotations and
keyword-only receivers resolved as typed receivers; spans from the first
decorator). The companion bands reproduced §2a's numbers exactly (driver 24,
renderer 20, entry renderer 7, handle 1, keybindings 1), so the methodology
transfers to the tui side unchanged.

### The class, not the file, is the residual problem

Of the 922 lines that left `tui.py` (7,210 → 6,288), the god class lost **31**
(4,748 → 4,717 ast-lines, 345 → 337 defs — the eight departed defs are the four
projection property pairs deleted with the custom-overlay handle). Every landed
tui slice so far carved helper bands and companions, not the class. The file
ratchet cannot see this. **New success criterion, ranked with §5 item 2: a
class-level ratchet pinning `ToolLoopTerminalUi` at ≤ 4,717 ast-lines and
≤ 337 defs, lowered by every slice that shrinks it, never raised** (the
43-field pin already exists).

Context for the bar itself: the "no file above ~2,400 lines" statement in this
plan's opening is true only for `packages/tui`+`agent` as scoped there.
pi-mono's functional counterparts of the two god files are
`interactive-mode.ts` (6,353 at `05bf9df65`; above 2,400 continuously since
2026-01-02) and `agent-session.ts` (3,337), both still growing. This plan's
targets are stricter than the reference achieves — a deliberate choice, not a
pi-mono fact. `docs/backlog.md` is corrected accordingly.

### Measured cluster map

All 78 remaining properties are pure projections onto the three landed state
records; zero non-projection properties remain. Five widget slices own no
god-class field and have zero effective port beyond the shared raw-mode drive
loops: **17 session_picker, 27 model+scoped, 32 settings_dialog,
33 custom_overlay and 35 tree_selector are landable immediately, in any
order.** Their six `run_*` drive loops stay in `tui.py` until slice 43 ("six
key loops collapse") — which also resolves slice 33's stated-port
inconsistency: its runner loop reads `_driver.raw_mode`,
`_read_key_polling_resize` and `input_stream.fileno()`, so the loop waits like
the other five rather than gaining a key-read port.

Remaining clusters, measured: transcript(14) 9 fields/24 methods/422L,
effective port 1 — `_force_full_redraw` stays behind a scrollback-reset
callable because it writes screen-owned
`_painted_block_count`/`_live_height`/`_live_input_row` (a port §1 omits);
autocomplete(16) 3 fields/17m/~393L, effective port 1 (the custom-editor
forward at L5418-5424, retired at slice 24); custom_editor(24) 7 fields/12m/
312L, port 14 — the most sibling-blocked cluster; pending+clipboard(30)
3 fields/11m/173L; chrome+footer+listeners(12) needs two ports §1 omits
(`_driver` access for the title OSC at L3450-3458 and region width at
L3220-3268, plus a working-text clear verb at L3638-3645);
extension_generation(11) is only 2m/81L but touches 15 members across six
clusters — confirming §2 item 3's named-teardown-owner design; screen(43)
effective port **33**, including every widget's `*_region_lines` — goes last,
confirmed. Slice 15 is partially landed already (`_CustomEntryDiagnosticHost`
was deleted in `167740b`; the dead `hasattr` is now at L890).

**Slices 24 and 30 form a cycle §3 never names:** `_handle_custom_editor_key`
calls `_paste_clipboard_image`/`enqueue_follow_up`/`restore_pending_to_editor`
(30-owned, L2665-2759) while 30's verbs call
`_custom_editor_text`/`_set_custom_editor_text` (24-owned, L5500-5524,
L5264-5311). Whichever lands first takes explicit callable ports the other
slice retires.

### The nine external write sites (the repo's last ownership violations)

The 8-field external-writer list in the 2026-08-04 comparison has a ninth
site: `_LiveExtensionUiDriver.set_tools_expanded` writes `tools_expanded` at
tui.py:464 alongside `repl/view_actions.py:46`. Retirements, folded into the
owning slices: `command_names` + `command_descriptions` +
`extension_shortcut_keys` + `autocomplete_max_visible` → one frozen
`CommandSurface` record with a single replace verb on the autocomplete owner
(writers at tool_loop_session.py:1271-1277 and repl/reload.py:583-594
repoint); `thinking_hidden` → transcript owner, startup calls the existing
`set_thinking_hidden` verb instead of a field write; `tools_expanded` →
transcript verb that bundles the rerender, both writers repoint;
`clipboard_temp_dir` + `clipboard_image_read` → a `ClipboardConfig` record
injected at wiring — a record, not a verb, because the session co-owns the
path for reference-root policy.

### extension_runtime.py corrections

111 top-level defs, not §2a's 110 (a miscount, not drift; the
five-defs-with-effective-port claim verifies exactly, all one lifecycle band —
which is three non-contiguous clusters, L2769-3039 / L3784-3853 / L3856-4027,
and reads private *fields* `host._guard`/`host._state` across the function
boundary, not just unbound methods). Slice 46's `activation.py` lands at
~1,750-1,950 file lines, not ~1,570: the lower figure silently assigns
`ActivatedExtension`, `ExtensionActivationBatch` and `_ExtensionRuntime` (105
def-lines) to the contracts module — slices 34/46 adopt that placement
explicitly. Still under the 2,400 bar, and the don't-split-further rationale
holds in code (capability tokens at L1160-1161 gate `_commit_activation` at
L2320 and `_seal_and_freeze` at L2676). The `[X]` tag is overstated for slices
18, 25, 34 and 46: each edits a god file's `extension_runtime` import block
under the definition-site rule (only 8 and 29 touch neither god file at all),
and slice 46 fans out to 17 importers repo-wide. Semantically parallel with
[T]/[S] work, but expect import-block merge conflicts, not zero contact.

### Renames, folded into slices 44/45 and 48

`NativeToolReplSession` misnames the product session three ways: "Native" (vs
wrapped agents) is vestigial now that the native runtime is the product;
"Tool" is vestigial since the no-tool REPL retired (`8c9441f`, 2026-06-20);
"Repl" is wrong — all four `repl`-subcommand modes (interactive/json/rpc/
print) drive this class (cli.py:2407-2487, automation/run_modes.py,
automation/rpc.py); only the legacy one-shot `pipy run`/`sdk.py` path bypasses
it. Decisions:

- **Slices 44/45 create the residual as
  `native/coding/session.py::CodingSession`** (companions:
  `NativeToolReplResult` → `CodingSessionResult`, `PipyNativeToolReplAdapter`
  → `CodingSessionAdapter`; the `name="pipy-native"` data string is
  unchanged). Not `AgentSession`: every one of the 15 existing `AgentSession`
  occurrences cites *Pi's* class (e.g. rpc.py:788), and the name collides
  conceptually with `native/agent/`, which the boundary tests keep
  product-free. `CodingSession` matches tau, matches the existing `coding/`
  package vocabulary, has zero collisions — and the boundary suite already
  uses `pipy_harness.native.coding.session.CodingSession` as its synthetic
  fixture (test_architecture_import_boundaries.py:3549). §5 item 7 gains the
  row pi `core/agent-session.ts` ↔ `native/coding/session.py`.
- **Slice 48 renames `ToolLoopTerminalUi` → `TerminalUi`** — the unique
  zero-collision candidate; the test fakes are already `FakeTerminalUi` /
  `_FakeTerminalUi`.
- Timing rationale: slices 3/40 rewrite the boundary literals anyway (**34**
  occurrences of `"pipy_harness.native.tool_loop_session"` at `bf1122d`, not
  §3's 25 — the drift warning now applies to the count), and slices 44/45
  rewrite the residual file wholesale; renaming earlier edits the literals
  twice, renaming later re-edits freshly written docstrings (repl/ already
  cites the old name four times). The `native/` package prefix (94.4% of
  pipy_harness lines) is **not** renamed in this program — that is 178-module
  churn; at most a terminal cleanup after slice 49.
- Mechanical blast radius, measured: session class ~105 files / 713
  occurrences (only 2 real src imports; the rest tests/docs/scripts), 29
  mirrored test filenames, pyproject.toml:98.

### Latent break the slices must handle regardless of renames

`scripts/parity_score.sh` greps `tool_loop_session.py` *contents* seven times
(compact_agent_history, tool_budget, production_tool_registry, attachments=,
/settings, …) and `scripts/architecture_metrics.py` hardcodes both god-file
paths and parses the literal class name `ToolLoopTerminalUi`. Slices 40-45 and
48 move those tokens, so both scripts break during this plan's own execution
even with no rename. The slice that moves each token updates the script in the
same commit.

### Drifted anchors (for §2a/§3 readers)

`_frame_snapshot` L5001 → 4559; `_standard_frame_inputs` L5050 → 4608;
`_queue_custom_editor_action` L1646 → 2582; the driver binding band L793-925 →
L290-496 with the getattr pair at L465/L469; startup selectors L6671/L6716 →
6219/6245; the renderer's `_chrome` reads L2094-2166 → 5906-5931. §3a's
residual arithmetic is ~70L short: the retained driver shell is 207L (+51L
generation proxy), not "~185".

## 3. Ordered slices

`[T]` touches `tui.py`, `[S]` `tool_loop_session.py` — these serialize. Per §2d, slices 18/25/34/46
edit a god file's `extension_runtime` import block only: treat them as `[X]` for code motion but
serialize their import-block edits against concurrent `[T]`/`[S]` slices. `[X]` touches neither and is
always available. Standing rules: every port is a callable, a record, or an existing type defined in
the destination; anything >10 splits **inside the slice that moves it**; import from the definition
site; the old path dies in the same commit.

**Slice 1, in full, so a fresh session can execute it.** `[T]` Move `ExtensionChromePrepareInput`
(tui L284-292), `ExtensionChromeCommitToken` (L295-299) and `ExtensionChromePreparePort` (L302-304)
into `src/pipy_harness/native/extension_chrome_state.py`, directly after `ExtensionChromeSink`.
They are declaration-only and import nothing, so that module's total-leaf rule
(`forbidden_imports=("pipy_harness","pipy_session")`, L1045-1052) holds verbatim. Repoint
`tool_loop_session.py` L384-385 into its existing `extension_chrome_state` import block; **delete**
`session_generation.py:37` (`from pipy_harness.native.tui import ExtensionChromePrepareInput` — a
TYPE_CHECKING back-edge from a dependency-neutral record into the god file); update
`tests/test_native_extension_chrome_staging.py` L43-49 and
`tests/test_native_session_extension_generation.py` L121. Port: none. Boundary edit: none. C901:
max moved complexity 2. Safe alone, and a prerequisite for slice 2, whose `prepare_candidate`
signature names two of the three.

Each row below is safe alone for the reason in its port column: zero port, or a port already landed.

| # | moves → target | tag | port | C901 |
|---|---|---|---|---|
| 2 | 5 `_Chrome*` records + `_LiveExtensionUiDriver` L351-791 → `ui/chrome_handoff.py` as `ExtensionChromeRouter`; `ChromeHandoffOperation`/`ChromeAcceptanceResult` and `route`/`route_bound`/`dispose_handoff_listener`/`retiring_disposal_route` go public | T | `ExtensionChromeDelivery` | **split `accept_candidate` 14** at the inner `except BaseException` arm → `_recover_failed_attach`; `_apply_sink_operation` exactly 10 — byte-for-byte |
| 3 | `repl/diagnostics.py` (`_emit_diagnostic`, 40 refs), `repl/session_adapters.py` (status adapters, `production_tool_registry`, `_BuiltinCommandInterpreter`), `repl/turn_leaves.py` (`_wait_for_*_interrupt`, `_pricing_for`, `_AGENT_HISTORY_*`, `_finish_chrome_retirement`, `_raise_first`, `_CANCEL_JOIN_TIMEOUT_SECONDS`) | S | none | `interpret` 9 — add nothing |
| 4 | `extensions.py` → `extensions/packages.py` + empty `__init__.py` | X | none | none |
| 5 | `ui/paint_lock.py` — `PaintLock` newtype | T | — | none |
| 6 | tests-only ratchet: sizes pinned at today's measured ast-lines, tightened by every later slice | — | — | — |
| 7 | `repl/provider_selection.py` (`_ProviderMutationEffects` + startup projection); pass the record | S | none | max 7 |
| 8 | `extensions/{session_views,command_context}.py` | X | none | max 3 |
| 9 | `repl/session_commands.py` + the 3 instance-free god methods it reaches | S | none | `execute` 8 |
| 10 | `repl/collaborators.py` (`_SessionCollaborators`) | S | none | max 5 |
| 11 | `ui/extension_generation.py` — named teardown owner, ordered participant tuple | T | PaintLock + repaint | none |
| 12 | `ui/extension_chrome.py` + `components/footer.py` + `terminal_input_listeners.py`; `_build_region` lands **here**, not in screen; the 4 `_chrome` projections deleted | T | record + PaintLock + repaint | **split `_apply_extension_terminal_input_listeners` 11** |
| 13 | `repl/reload.py` + `_maybe_save_implicit_trust_after_reload` | S | `ImplicitTrustState` | **split `_reload_extension_generation` 16** activate→prepare→publish→retire |
| 14 | `ui/components/transcript.py` — `_history_blocks`, live buffers, 31 commit/stream verbs | T | PaintLock + repaint | max 8 |
| 15 | `ui/components/custom_entry_renderer.py` + run state + `AcceptedCustomMessageSinks`; `_CustomEntryDiagnosticHost` **deleted**, its one reach inlined; dead `hasattr` L1330 deleted | T | none — 14 and 43 own it | max 6 |
| 16 | `ui/autocomplete.py` grows: suggestions, path completion, slash menu, provider registry | T | `cwd` + buffer accessor | `_attempt_path_completion` 9 |
| 17 | `ui/components/session_picker.py` (14 fields, 3 modes) | T | overlay record + PaintLock + repaint | **split `_handle_session_picker_key` 20** on `session_mode` |
| 18 | `extensions/custom_payloads.py` (incl. `_CustomEntryRedrawRow`) | X | none | max 8 — verbatim |
| 19 | `repl/settings_actions.py` — drive loop, rows, overlay lines, theme selector | S | provider record | **split `_drive_settings_dialog` 16** on its `_local_action` dispatch |
| 20 | `repl/local_shell.py` + `repl/view_actions.py` (fold, thinking cycle) | S | none | `_run_local_shell_shortcut` exactly 10 — verbatim |
| 21 | `repl/{extension_operations,execution_projections}.py` | S | none | max 3 |
| 22 | `ui/components/input_editor.py` + exported `apply_editing_key` | T | EditorState + PaintLock + repaint | all <10 |
| 23 | `ui/components/tool_loop_renderer.py` | T | none — 14 + `ExtensionChromeState` | max 8 |
| 24 | `ui/components/custom_editor.py` grows: 7 fields, wiring, keys, frame, `_CustomEditorKeybindings` | T | record retires slice-5's callable | **split `_handle_custom_editor_key` 28** by key class; `_wire_custom_editor_component`, `_custom_editor_frame_lines` exactly 10 |
| 25 | `extensions/message_routing.py`; retire the `message_routing` `@property` by threading the record | X | none | `accept` exactly 10 — **verbatim** |
| 26 | `repl/session_transfer.py` + its 3 static/classmethods | S | none | `_import_session` exactly 10 |
| 27 | `ui/components/{model_selector,scoped_models_selector}.py` — pool/render/keys only | T | overlay + PaintLock + repaint | `run_scoped_models_selector` exactly 10 |
| 28 | `repl/selector_actions.py` — rows, openers, `_handle_trust_command` | S | none | max 9 — verbatim |
| 29 | `extensions/contribution_names.py`, **excluding** `_activated_/_staged_contribution_names` (they take `ActivatedExtension`/`_FrozenActivation` and stay with activation) | X | none | max 5 |
| 30 | `ui/pending_messages.py` + `ui/clipboard_images.py` | T | EditorState + repaint | max 8 |
| 31 | `extensions/{dispatch,tool_port}.py` | X | none | `invoke` 7 |
| 32 | `ui/components/settings_dialog.py` — tui half only | T | overlay + PaintLock + repaint | **split `run_settings_dialog` 11** at the raw-mode loop |
| 33 | `ui/components/custom_overlay.py` grows — component runner; its raw-mode drive loop stays in `tui.py` until slice 43 (§2d) | T | OverlayState + repaint | **split `run_custom_component` 17** setup/loop/dispose |
| 34 | `extensions/{collectors,contracts,flag_tokens,provider_normalization}.py`; `contracts` also takes `ActivatedExtension`, `ExtensionActivationBatch`, `_ExtensionRuntime` (§2d placement) | X | none | max 6 |
| 35 | `ui/components/tree_selector.py` | T | overlay + PaintLock + repaint | `run_tree_selector` exactly 10 |
| 36 | `native/startup_selectors.py` — both constructing entrypoints + `run_project_trust_selector`; `cli.py` repoints | T | concrete import (legal here) | max 6 |
| 37 | `ui/components/extension_prompts.py` grows — external editor | T | `external_io_suspension` injected | **split `_run_extension_external_editor` 11** |
| 38 | `_ExtensionChromeTuiHandle` → `ui/chrome_handoff.py` | T | `Callable[[], None]` | max 3 |
| 39 | `chrome.py` absorbs footer composition; deletes 2 injected callables | S | none | max 8 |
| 40 | `repl/loop_scope.py`; **dissolve the loop-step port** — delete `session`, add the 3 fields, rewrite the 11 `session.*` sites plus the by-value `session=session` at L3268 | S | the widened record | `run()` grows +2 ast-lines — do **not** lower the `run < 800` bound here |
| 41 | `repl/loop_step.py` | S | none | **split `step_once` 30**: A unpack/prefill, B intake, C1 hotkeys / C2 `!` shell, D dispatch, E accepted input, F1 assembly / F2 run+settle; must not reintroduce the nine status-callback names as nested defs |
| 42 | `repl/provider_config_commands.py` | S | none | `_scoped_models` 9 |
| 43 | `ui/screen.py` — paint core, `_frame_snapshot`, resize, `external_io_suspension`, the one `drive(owner)` loop; constructs the single `RLock` | T | ordered contributor tuple | all <10; six key loops collapse |
| 44 | `repl/wiring.py` — `run()` as ~8 value-returning phases | S | none | **`run` 40 dissolves per phase**; delete `tool_loop_session.py` from per-file-ignores; lower the run bound to ~10 |
| 45 | `repl/command_router.py`; the session residual lands as `native/coding/session.py::CodingSession` with `CodingSessionResult`/`CodingSessionAdapter` companions (§2d renames) | S | none | must be <10; the pin is gone |
| 46 | `extensions/activation.py`; delete `extension_runtime.py` | X | **none, by design** | `activate_extension_batch` exactly 10 — verbatim |
| 47 | `repl/extension_attach.py` — unify startup and `/reload` attach | S | none | merged form <10 |
| 48 | burn `read_line` 39 and `wait_for_active_turn_interrupt` 35 onto `apply_editing_key`; split `_deliver_chrome_event` 11; rename `ToolLoopTerminalUi` → `TerminalUi` (§2d) | T | none | succeed → delete tui.py's pin |
| 49 | final ratchet + boundary audit | tests | — | — |

**Boundary edits, complete.** Slice 3 adds `"pipy_harness.native.repl"` beside every
`"pipy_harness.native.tool_loop_session"` (25 literal occurrences today — re-read the line numbers,
the 2026-08-03 list has drifted 9-14 lines) plus a new rule `source_package=
"pipy_harness.native.repl", forbidden_imports=("pipy_session",
"pipy_harness.native.tool_loop_session")`; that rule is what forces slice 40 to *delete* the
`session` field rather than quote it. Slice 36 adds the analogous `native.startup_selectors` rule.
Slice 46 deletes all 17 `"pipy_harness.native.extension_runtime"` entries and that module's member
of the four-module host tuple — each already covered by the stricter `native.extensions` rule. No
other slice edits a rule; **no rule is relaxed anywhere**.

**Good stopping points.** After **6**: back-edge gone, chrome transaction out, repl package and its
rule exist, ratchet armed. After **21**: both god files roughly halved, every remaining tui owner
independently landable. After **34**: every widget owned. After **44**: both god files under the
bar, session pin deleted. After **46**: `extension_runtime.py` retired. 47-49 are polish; 47 is the
only behaviour-affecting slice and is deliberately last.

## 3a. Projected end state

`tui.py` **6,730 → ~720 ast-lines**. The residual is irreducibly the terminal boundary: `read_line`
and `wait_for_active_turn_interrupt` (~230 after slice 48), the 24-member binding at L793-925
(~185), `_GenerationExtensionUiDriver` (51 — moving a full-surface `__getattr__` proxy into
`native.ui` would launder a back-edge past the boundary test, mypy and grep at once), plus fields,
one handle per owner, imports. **The asserted 500 is not reachable**; slice 49 pins the measured
value. `tool_loop_session.py` **6,221 → ~400**: dataclass surface, `__post_init__`, `provider_port`,
a two-line `run()`. `extension_runtime.py` **4,131 → 0**.

Largest file produced: **`extensions/activation.py` ~1,570** — 35% under the pi-mono ~2,400 bar,
above pi's `runner.ts` (1,236) only because its state machine is driven from outside via capability
tokens with unbound dispatch and one raw-private-field read. Then `chrome.py` ~1,180,
`extensions/packages.py` 1,105, `repl/wiring.py` ~620, `ui/screen.py` ~600, `repl/loop_step.py`
~570, `ui/chrome_handoff.py` ~510. Nothing above 1,700, pinned by slice 49. **C901: 13 pinned files
→ 11**, zero added.

## 3b. What changed from the previous ordering, and why

1. **Port surface is a measured column now.** The old table listed `_LiveExtensionUiDriver` at 22;
   it is 24, and the two extras (`getattr(self._terminal_ui, "rerender_custom_messages"/"paint")`,
   L894/L898) are invisible to any `_terminal_ui\.` grep — an extraction built on 22 fails at
   runtime, not at type-check.
2. **All three "needs a port" verdicts were wrong.** The driver's 441-line transaction touches the
   god class **zero** times; `_CustomEntryRenderer`'s 5 and `_TuiToolLoopRenderer`'s 16 dissolve
   once `transcript.py` and `screen.py` own the state. Port surface is a property of the *schedule*.
3. **`ui/screen.py` moved from Wave 1 to slice 43** — it is a fan-out hub whose early port exceeds
   20; that was the worst placement in the old order.
4. **Every "tui + session" row is two modules.** Seven session-side helpers annotate
   `terminal_ui: ToolLoopTerminalUi`, which `native.ui` may not name even under TYPE_CHECKING; the
   old layout would have failed the rule Wave 0 added — slice-1-blocks-slice-3 at 840 lines.
5. **`ui/component.py` and `ui/extension_chrome_driver.py` are deleted from section 1.** The first
   invents four Protocols with no implementors; the second *is* the 24-member port.
6. **Section 2 item 5 is corrected.** `provider_state` is never assigned: 7 reads, 0 writes, so
   `repl/provider_selection.py` is a pass-the-record move, not an ownership transfer — and the
   entire genuine port surface of `NativeToolReplSession` is one mutable `Path | None` slot.

## 4. Explicitly out of scope

- `cli.py` (2,851) and `session.py` (2,488) — the two largest files after this plan. Separate track; absorbing them here would double the program.
- Renaming for taste, arbitrary sub-packaging quotas, "N top-level modules" targets.
- Growing `ui/__init__.py`: it is a small curated barrel for the reducer/adapter pair and must not gain the new modules. No `ui/utils.py`, ever — that is pi's own accretion sink.
- Copying pi's `TuiBase` strategy hierarchy (pipy has one paint strategy), retained mutable component tree with `invalidate()` (pipy's immutable `FrameSnapshot` under a reentrant RLock is strictly better), module-level mutable singletons, or `index.ts`-style barrels.
- CI jobs, nightly runs, release process, external oracles. No deprecation shims, re-export modules, or compatibility aliases — the old path dies in the same commit.
- Splitting `extensions/activation.py` below ~1,570: its state machine is guarded inside `_ActivationApi` but driven from outside via module-level capability tokens; subdividing exports a private capability.

## 5. How to tell it worked

1. **Field ownership is machine-checkable.** New test: for every field of `ToolLoopTerminalUi`, `_RunControlState`, and `NativeToolReplSession`, `grep -rn "self\._<field>" src/` returns hits in **exactly one module**. Set the bound at **1**, not at "no worse than today."
2. **Size ratchet** (both files regrew ~14% last time, so pin the *value*, not the trend): a test asserting `tui.py ≤ 550` and `tool_loop_session.py ≤ 450` ast-lines once Wave 4 lands, plus **no file under `src/pipy_harness/native/` above 1,700 lines**. Lower each bound in any slice that shrinks the file; never raise one. Per §2d, a **class-level ratchet** additionally pins `ToolLoopTerminalUi` at ≤ 4,717 ast-lines and ≤ 337 defs immediately (not deferred to Wave 4), lowered by every slice that shrinks the class — the file ratchet alone missed 35 slices that removed 922 file lines but only 31 class lines.
3. **`run()` ast-line bound** at `tests/test_architecture_agent_loop_boundaries.py:643` reaches ~80 (from 787/800 today), and `run()` constructs no collaborator — `repl/wiring.py` returns a frozen `SessionWiring`.
4. **C901 baseline: 13 pinned files → 11 or 12.** `tool_loop_session.py` removed; `tui.py` removed or covering 2 functions. **Zero entries added** across the whole program.
5. **Boundary rules strictly stronger:** `native.ui` forbidden everywhere `native.tui` is (six new sites); `native.ui` may not import `native.tui`, `native.repl`, `native.tool_loop_session`; `native.repl` forbidden everywhere `tool_loop_session` is; `extension_runtime` entries deleted with the file; `_PLANNED_IMPORT_PREFIXES` unchanged except as noted. No rule relaxed anywhere.
6. **Locks are types, not comments:** `grep -c "RLock()" src/pipy_harness/native/` shows exactly one construction site for `PaintLock` (`ui/screen.py`) and one for `SessionStateLock` (`repl/wiring.py`), each with no default parameter anywhere.
7. **Parity diffs are file-to-file:** pi's `components/session-selector.ts` ↔ `ui/components/session_picker.py`, `core/model-runtime.ts` ↔ `repl/provider_selection.py`, `core/bash-executor.ts` ↔ `repl/local_shell.py`, `agent-loop.ts` ↔ `repl/loop_step.py`, `main.ts` ↔ `repl/wiring.py`, `core/extensions/runner.ts` ↔ `extensions/activation.py`, `core/agent-session.ts` ↔ `native/coding/session.py` (the slice-45 residual, renamed `CodingSession` per §2d).
8. `just check` green on every commit, with the old path deleted in the same commit — no slice depends on the next.