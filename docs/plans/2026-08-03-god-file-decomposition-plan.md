# God-File Decomposition Plan

Status: complete. Sections 2a, 3, 3a and 3b were re-derived from measurement on
2026-08-04 and supersede section 1's line estimates and the original wave order.
Section 2d (also 2026-08-04) re-measures the tui side and extension_runtime.py,
adds the class-level ratchet, corrects the pi-mono bar's scope, and fixes the
slice-44/45/48 rename decisions (`CodingSession`, `TerminalUi`). Section 2e is
the operator-authorized corrective amendment after slice 49 failed closed: it
inserts dependency-ordered slices 48a and 48b without renumbering landed
history, then retries 49. Wave 0 is test-only rule hardening and landed first;
every implementation slice lands individually with `just check` green and the
old path deleted in the same commit.

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

**Checkpoint (2026-08-05, after slice 44).** The explicit post-44 stopping
point is reached. `NativeToolReplSession.run()` is now a 67 AST-line facade
(C901 5) over frozen `SessionWiringInput`/phase records and a frozen
`SessionWiring`; production composition lives in `native/repl/wiring.py` and
uses one named `SessionStateLock` over one explicitly constructed `RLock`.
`native/tool_loop_session.py` is 336 physical lines (1,238 before this slice),
its C901 pin is deleted, and `native/tui.py` remains 1,466 lines. The explicit
phase records, callable captures, and imports make `native/repl/wiring.py` 1,392
physical lines rather than §1's rough ~620 projection; it still remains below
the 2,400-line bar without moving behavior into another god file. Both former
god files are below that bar; the native-module ceiling remains 2,488
at `native/session.py`. Slice 45 is intentionally not folded into this
milestone: the existing session/result/adapter names and residual factory seams
remain. This is internal architecture only, so no changelog or release note
applies.

**Checkpoint (2026-08-05, after slice 46).** The mandated post-46 milestone is
reached. The 2,000-physical-line residual `native/extension_runtime.py` is
deleted with no shim, alias, re-export module, or surviving live importer. Its
activation lifecycle moved as one capability-token state machine to
`native/extensions/activation.py`: 1,807 physical/AST-span lines, 28 top-level
class/function members, maximum C901 10 (`activate_extension_batch`), and no
per-file pin. This lands inside §2d's accepted 1,750–1,950 band and below the
2,488 native-module ceiling. AST remeasurement resolves the task prompt's
"25 definitions" count as a label error: its authoritative enumerated member
list contains 28 definitions, and all 28 moved. The pre-move path blast radius
was 21 production files (including the retired owner), 7 parity scripts, and 41
test files, rather than §2d's historical 17 repo-wide estimate. After
definition-site routing, 9 production files, 6 parity scripts, and 19 test files
directly import activation; the other former consumers import contracts,
extension values, UI helpers, or dispatch/message/tool leaves from their actual
owners. The recursive `native.extensions` boundary governs activation; stale
retired-module entries were deleted, frame/overlay and canonical-adapter rules
were broadened to `native.extensions` where needed, and
`_PLANNED_IMPORT_PREFIXES` is unchanged. `native/tui.py` remains 1,466 physical
lines, `ToolLoopTerminalUi` remains unchanged, and the coding-session facade
remains 336 lines. Slice 47 is next. This is behavior-preserving ownership only,
so no changelog or release note applies.

**Checkpoint (2026-08-06, failed-closed slice-49 audit).** Slices 47 and 48 are
landed, but the final milestone is not. `native/tui.py` is 1,349 physical lines;
`TerminalUi` is 856 AST lines / 43 definitions / 18 fields. The four intended
residual methods account for only 170 AST lines, while 39 definitions omitted
from the old residual arithmetic account for 569: constructor 153; projections
14; modal/chrome/screen/footer facade 231; transcript facade 70; startup/input
helpers 101. Only 32 of those 569 lines have zero production callers. The
remaining roughly 292 production-used facade lines and 245 constructor/startup
ownership lines cannot be deleted mechanically. With 493 lines before the
class, a ~720 file permits only ~227 class lines, so retrying the tests/docs-only
slice 49 against the current structure would be dishonest. The operator
therefore authorized exactly two corrective implementation slices, 48a then
48b, before retrying 49. No target or boundary is relaxed.

**Checkpoint (2026-08-06, after slice 48b).** The corrective implementation
pair is landed, but the final milestone is still retry-49-owned. Slice 48b moved
one complete terminal graph transaction to the frozen/slotted
`native/ui/composition.py` builder, startup history to
`native/startup_chrome.py`, and local-command classification to the input editor
owner. `native/tui.py` measures **580 physical lines** and `TerminalUi` measures
**230 AST lines / 5 definitions / 9 fields**, down from slice 48a's 907 / 498 /
7 / 18. The exact public constructor signature is unchanged, including the sole
`clipboard_config` `InitVar`; both raw-mode loops remain in `tui.py`. The paired
48a+48b reduction is 769 physical file lines and 626 class AST lines. The result
beats the 710–760 planning band because retiring the composition/startup regions
also removed more facade-only imports, comments, and spacing than forecast; the
residual was not padded back upward. `ui/composition.py` is 267 lines and
`startup_chrome.py` is 98, one Screen-created `PaintLock` remains shared by the
exact owner graph, and no C901 pin or boundary relaxation was added.

**Final checkpoint (2026-08-06, retry slice 49).** The tests/docs-only final
audit passes and the program is complete. The alias-resistant member audit pins
`TerminalUi` at 9 fields, `RunControlState` at 10, and `CodingSession` at 24,
including the exact measured writer modules and synthetic alias/dynamic-access
refusals. Lock construction remains exactly one `PaintLock` in `ui/screen.py`
and one `SessionStateLock` in `repl/wiring.py`, each wrapping one explicit
`threading.RLock()` and exposing no default construction. Final shape is
`native/tui.py` 580 physical lines and `TerminalUi` 230 AST lines / 5 retained
definitions / 9 fields; `coding/session.py` is 336, activation is 1,807, and the
native maximum is 2,488 at `native/session.py`. The complete 41-rule import
boundary inventory, recursive `native.ui` back-edge audit, retired surfaces,
and absent old C901 pins remain strict. Parity E2/E6 now name their actual
owners and settings command route. No release note applies to this internal
audit.

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
| `native/repl/reload.py` / `extension_attach.py` | `/reload` preparation / unified startup-and-reload `attach_generation(predecessor=None)` | 300/430 | session |
| `native/repl/loop_step.py` | `step_once` as regions A–E behind `_TurnScope` | 620 | session |
| `native/repl/wiring.py` | phase functions each **returning a frozen record**; `SessionWiring` | **1,399 measured** | session `run()` |
| `native/chrome.py` (grows) | absorbs footer composition; deletes 2 injected callables | 1176 | session |
| `native/extensions/*` (12 modules) | contracts, message_routing, custom_payloads, session_views, dispatch, tool_port, contribution_names, collectors, flag_tokens, provider_normalization, command_context, activation | **1,807 measured max** | extension_runtime |
| `native/tui.py` (residual) | constructor/public `TerminalUi` shell, thin composition assignment, raw-mode `read_line`/`wait_for_active_turn_interrupt`, `start`, `is_supported` | ~~500~~ **~720 (710–760 planning band)** (see 2e/3a) | — |
| `native/coding/session.py` (residual) | `CodingSession` dataclass/composition facade; 67-AST-line/C901-5 `run()` | **336 measured** | — |

**Measured ceiling after the landed moves:** `native/extensions/activation.py` is 1,807 (the accepted §2d capability-token owner), while out-of-scope `native/session.py` sets the native-module ceiling at 2,488. Earlier ~1,570 and “nothing above 1,700” projections are superseded. Slices 48a/48b must keep every new modal/composition/startup module below 1,807 and may not move terminal residue into a replacement god file.

## 2. The hard part — state that resists partitioning

1. **`_paint_lock` is reentrant by necessity.** `paint() → _paint_locked → _frame_snapshot → _standard_frame_inputs → _extension_*_lines` re-acquires it while running trusted extension factories; 19 readers, 15 in the chrome cluster. *Handling:* `ui/screen.py` constructs the single `RLock` behind a `PaintLock` newtype **with no default constructor** and injects it into extension_chrome, footer, custom_editor, transcript. "Forgot to inject" becomes a mypy error, not a hang. A plain `Lock` is unrepresentable.
2. **`session_state_lock` — one RLock, documented only in an inline comment.** Shared by keybindings, settings, coding_state, tool_capabilities, generation_ref, the delivery gate, queue/reference mutexes, so a worker's `set_active_tools` serializes against a `/reload`. *Handling:* `SessionStateLock` newtype, no default, threaded as an explicit typed parameter from `repl/wiring.py`. `provider_selection.py` pins the order against its private `mutation_io_lock` in its module docstring.
3. **The extension-generation teardown has no owner.** `clear_extension_chrome` (54L) atomically resets chrome + custom editor + autocomplete providers + thinking label across two critical sections with an unlocked disposal window running trusted code. *Handling:* `ui/extension_generation.py` — one field (`generation`) plus an **ordered module-level participant tuple**, each exposing `retire_generation()`/`reconcile_generation()`, with a test asserting the order. It lands *before* the chrome and custom-editor slices, which cannot separate until it exists.
4. **`ctl` is a two-party protocol.** `line`/`pending_prefill` (loop_step writes each iteration; `/model` and `/scoped-models` push a prefill for the next), `session_tree` (rebound wholesale by `/new /resume /fork /clone /import`, and the setter re-binds the mutation lock — a cached tree becomes a retired tree), `extension_in_agent_turn` (two writers, one reader in tui's custom-message router; a stuck-true flag routes extension messages as steering forever). *Handling:* `repl/loop_scope.py` holds `_RunControlState` and **every consumer takes the same instance** — never a copy. `pending_prefill` gets `push()`/`consume()`; `session_tree` only a lock-rebinding setter; `extension_in_agent_turn` only `enter_turn()`/`settle()`/`in_turn`.
5. **`provider_state` has two owners today** — declared on the session, written exclusively by `_ProviderMutationEffects` through `self.session.provider_state`. *Handling:* the field and all 16 methods move into `repl/provider_selection.py`; consumers read `current_thinking_level()` instead of the three-variant union.
6. **The concrete terminal object is a custom-editor factory argument, not an ownership port.** `factory(host, theme, keybindings)` still receives the concrete terminal host; measured extension factories accept it but production does not use it to recover owner methods. Slice 48b therefore passes `TerminalUi` only as the builder's opaque `host: object` value. Modal, transcript, screen, chrome, autocomplete, editor, queue, and clipboard work travels through the concrete owners in `TerminalComponents`; no Protocol restates the retired facade and no callable bundle launders it back in.

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
generation proxy), not "~185". Section 2e supersedes that correction with the
post-48 class audit; the shell/proxy measurement remains historical evidence.

## 2e. Corrective amendment after slice 49 failed closed (2026-08-06)

Measured on clean `main` at `bd485e9`. `tui.py` is 1,349 physical lines:
imports/helpers 1–221; `_LiveExtensionUiDriver` 222–438 (217);
`_GenerationExtensionUiDriver` 441–491 (51); `TerminalUi` 494–1349 (856).
The intended residual methods are exactly `is_supported` 13, `start` 12,
`read_line` 64, and `wait_for_active_turn_interrupt` 81 = **170 AST lines**.
The old §3a arithmetic counted those loops and the retained driver shell, but
not the other **39 definitions / 569 AST lines** still in `TerminalUi`:
`__post_init__` 153; owner projections 14; modal/chrome/screen/footer facade
231; transcript facade 70; startup/input helpers 101. Thus 170 was treated as
if it described an 856-line class. It did not. The omitted 569 consists of only
32 zero-production-caller lines, roughly 292 production-used facade delegates,
and roughly 245 constructor/startup ownership lines. With 493 lines before the
class, ~720 allows only ~227 class lines at the present import/helper shape;
**the current structure cannot pass slice 49**. The rest of the audited end
state remains valid: `coding/session.py` 336; `activation.py` 1,807 accepted by
§2d; the 2,488 native ceiling set by out-of-scope `native/session.py`; both
former C901 pins gone; old names/modules deleted; and parity still 49/49. Final
field/lock ownership tests remain deferred to retry 49.

The target remains reachable, but only after ownership changes. The AST-member
inventory and physical-file estimate are deliberately separate. The **569** is
the sum of decorated direct-definition AST spans; it establishes the omitted
ownership, but is not subtracted from the 1,349 physical file lines. A separate
physical scan partitions the 856-line class span into the existing shell/fields
494–550 (57), constructor region 551–704 (154), projection region 705–725
(21), retained raw-boundary region 726–898 (173), modal/chrome/screen/footer
region 899–1159 (261), transcript region 1160–1246 (87), and startup/input
region 1247–1349 (103). Those physical regions sum to 856; the 626 lines outside
the shell and retained raw-boundary region are the source footprint that 48a/48b
rewrite, not a promised net deletion.

Against that physical footprint, the paired two-slice estimate is a **589–639
physical-line net reduction**, producing **710–760 physical lines** and
containing the operator target of ~720. It allows for the thin 10–20-line
constructor/composition assignment, changes to the retained driver/loops, and
imports/helpers/spacing that can disappear in either slice. The separate class
tracking estimate is **at most 310 AST lines**; its exact value is pinned only
after implementation and does not derive the file band. To avoid invalid
Cartesian range arithmetic, let `F` and `C` be the measured physical-file and
class-AST results after 48a: 48b's paired estimate is a physical reduction from
`F - 760` through `F - 710` and a class reduction of at least `C - 310`. Over
48a's forecast this means 219–329 physical lines, conditional on `F`, and at
least 206–246 class AST lines, conditional on `C`—not arbitrary endpoints to
cross-combine. A 48a result near the high end is compatible only if remeasurement
finds the correspondingly larger remaining 48b-owned physical reduction. These
are estimates to lower after implementation measurement, not exact promises. A
retry-49 result above 760, or a class that still owns any retired facade family,
fails closed; 1,349 is not an acceptable new target.

### Exact post-48 member/caller inventory

“Caller” below means production `src/` only, including an in-module call and an
implicit dataclass construction; tests/scripts are characterization, not a
reason to retain a production facade. Receiver types and literal
`getattr`/`setattr` uses were checked, so same-named methods on components are
not counted as `TerminalUi` calls.

| `TerminalUi` definition(s) | exact production callers at `bd485e9` | disposition |
|---|---|---|
| `__post_init__` | implicit `TerminalUi(...)` from `cli._extension_decision`, `coding.session.CodingSession._build_terminal_ui`, `startup_selectors.run_startup_project_trust_selector`, `startup_selectors.run_startup_session_picker` | 48b replaces the 153-line transaction with one typed builder assignment; constructor parameters and public class name stay unchanged |
| `autocomplete` | `repl.reload.ReloadCommandEffects._refresh_presentation_and_persistence`; `repl.wiring._prepare_startup_extension_consumers` | repoint to the concrete `AutocompleteComponent` in `TerminalComponents`; delete projection |
| `custom_overlay_open` getter + setter | none | delete both; component/overlay tests assert `OverlayState` ownership instead |
| `run_model_selector` | `repl.settings_actions.open_theme_selector`, `._run_model_selection`; `repl.provider_config_commands._model`; `repl.selector_actions.open_default_project_trust_selector` | repoint to `TerminalModalDriver.run_model_selector` |
| `run_scoped_models_selector` | `repl.selector_actions.open_scoped_models_overlay` | repoint to `TerminalModalDriver` |
| `run_settings_dialog` | `startup_selectors.run_project_trust_selector`; `repl.settings_actions.drive_settings_dialog` | repoint to `TerminalModalDriver` |
| `run_tree_selector` | `repl.session_commands.run_interactive_tree_selector` | repoint to `TerminalModalDriver` |
| `run_custom_component` | the four `TerminalUi.run_extension_*` wrappers; `repl.collaborators.SessionCollaborators.extension_custom_driver` | wrappers and caller repoint to `TerminalModalDriver`; no method remains on `TerminalUi` |
| `run_extension_select` | `_LiveExtensionUiDriver.select`; `cli._extension_decision._StartupUiDriver.select` | repoint to `TerminalModalDriver.run_extension_select` |
| `run_extension_input` | `_LiveExtensionUiDriver.input`; `cli._extension_decision._StartupUiDriver.input` | repoint to `TerminalModalDriver.run_extension_input` |
| `run_extension_editor` | `_LiveExtensionUiDriver.editor` | repoint to `TerminalModalDriver.run_extension_editor` |
| `run_extension_confirm` | `_LiveExtensionUiDriver.confirm`; `cli._extension_decision._StartupUiDriver.confirm` | repoint to `TerminalModalDriver.run_extension_confirm` |
| `clear_extension_chrome` | `repl.loop_step._ReplLoopStep.clear_extension_chrome` (bound by `repl.wiring._assemble_session_wiring`) | call `ExtensionChromeOwners.generation.retire_generation` directly |
| `reconcile_extension_chrome` | `_LiveExtensionUiDriver._deliver_chrome_event` | call `ExtensionChromeOwners.generation.reconcile_generation` directly, preserving the retirement scope |
| `run_session_picker` | `startup_selectors.run_startup_session_picker`; `repl.session_commands.run_interactive_session_picker` | repoint to `TerminalModalDriver` |
| `close` | `cli._extension_decision`; both startup-selector constructors; `repl.loop_step._phase_f2_run_and_settle` and `_ReplLoopStep.finalize` through the `TerminalUi | NativeReplInput` union | live path calls `Screen.close`; headless path keeps `NativeReplInput.close` |
| `external_io_suspension` | `repl.provider_selection.ProviderMutationEffects.apply_auth_change`; `TerminalUi.__post_init__`, `.read_line`, `.run_extension_editor` | provider/modal/input paths use `Screen.external_io_suspension` directly |
| `set_footer_text` | `chrome._ChromeFooterEffects.refresh_footer_text`; `repl.wiring._start_chrome`; `TerminalUi.read_line` | grow existing `ui/components/footer.py::FooterComponent` with lock-guarded built-in footer state and `set_builtin_text`; repaint after unlock |
| `tools_expanded` | `_LiveExtensionUiDriver.get_tools_expanded`; `repl.settings_actions.settings_dialog_rows`; `repl.view_actions.toggle_view_fold` | read `TranscriptComponent.tools_expanded` |
| `thinking_hidden` | `repl.settings_actions.settings_dialog_rows`; `repl.view_actions.toggle_view_fold` | read `TranscriptComponent.thinking_hidden` |
| `submit_user_message`, `begin_assistant_turn`, `set_working`, `append_assistant`, `settle_assistant`, `append_reasoning` | none | delete all six delegates; `TuiToolLoopRenderer` already calls `TranscriptComponent` |
| `set_thinking_hidden` | `repl.view_actions.toggle_view_fold`; `repl.wiring._compose_product_session` | call `TranscriptComponent.set_thinking_hidden` |
| `set_tools_expanded` | `repl.view_actions.toggle_view_fold` | call `TranscriptComponent.set_tools_expanded`; `_LiveExtensionUiDriver` already does so |
| `add_notice` | `diagnostics.emit_diagnostic`; settings actions (`open_theme_selector`, `_run_settings_exit_action`, `_run_model_selection`); provider-config (`_show_hotkeys`, `_show_changelog`, `_model`); selector actions (`handle_trust_command`, `open_scoped_models_overlay`, `open_default_project_trust_selector`); wiring (`_compose_extension_phase`, `_start_chrome`); constructor clipboard callback | pass `TranscriptComponent` as the existing `NoticeSink` implementation and call its `add_notice` |
| `custom_entry_render_target` | `repl.wiring._compose_runtime_adapters` | construct `CustomEntryTerminalTarget(transcript, screen.render_inputs)` at the caller |
| `create_tool_loop_renderer` | `repl.wiring._compose_product_session` | construct `TuiToolLoopRenderer(transcript, chrome.record, screen.render_inputs, …)` at the caller |
| `add_tool_call`, `append_tool_output`, `add_tool_result` | `repl.local_shell.run_local_shell_shortcut` | pass/use `TranscriptComponent`; keep `TerminalUi` there only for the residual active-turn watcher |
| `rerender_custom_messages` | none | delete; the transcript owner retains the real verb |
| `_startup_blocks` | `TerminalUi.start` | 48b moves definition to `native/startup_chrome.py::startup_history_blocks(cwd, include_workspace_defaults)` |
| `_submitted_text_is_local_command` | `TerminalUi.read_line`, `.wait_for_active_turn_interrupt` | 48b moves the pure classifier to `ui/components/input_editor.py::submitted_text_is_local_command`; both raw loops import it |
| `_is_bash_mode` | none | delete |

The zero-caller set is exactly the two `custom_overlay_open` definitions, the
six unused transcript stream delegates, `rerender_custom_messages`, and
`_is_bash_mode`: **10 definitions / 32 AST lines**. Characterization tests that
currently call those names move to the actual owner; no alias, shim, re-export,
or replacement facade is permitted.

### Slice 48a — terminal facade retirement and repointing

48a is first because the 48b builder must return the final owner graph, not
encode facade calls that the next slice immediately removes.

- Add `native/ui/modal_driver.py::TerminalModalDriver`. Its constructor shape is
  exactly `(overlays: OverlayState, screen: Screen, input_editor: InputEditor,
  external_editor: ExtensionExternalEditor, keybindings_manager:
  Callable[[], KeybindingsManager | None])`. It owns the six `Screen.drive`
  orchestrations (model, scoped models, settings, tree, custom component,
  session picker) and the four extension-dialog projections. It has no terminal
  streams, raw-mode loop, lock construction, `TerminalUi` annotation, or
  `native.tui` import. `Screen.drive` remains the one modal key loop; the two
  raw-mode loops remain physically in `tui.py`.
- Add only the frozen, slotted concrete-owner record
  `native/ui/composition.py::TerminalComponents` in this slice, with exact
  fields `driver: TerminalDriver`, `screen: Screen`, `overlays: OverlayState`,
  `input_editor: InputEditor`, `transcript: TranscriptComponent`,
  `chrome: ExtensionChromeOwners`, `autocomplete: AutocompleteComponent`,
  `pending_messages: PendingMessages`, `clipboard_images: ClipboardImages`,
  `custom_editor: CustomEditorOwner`, and `modals: TerminalModalDriver`.
  `TerminalUi.__post_init__` may assemble that record at the end of its existing
  transaction for 48a; the builder itself is 48b-owned. This is a typed graph of
  existing owners, not a method Protocol or a bag of facade callables.
- Repoint every caller in the table to the narrow concrete owner it uses.
  `_LiveExtensionUiDriver` receives explicit `chrome`, `modals`, `transcript`,
  `autocomplete`, `custom_editor`, and `input_editor` constructor arguments;
  delete its unused `cwd` field. In particular, chrome reconciliation stays on
  `ExtensionGenerationOwner`, local-shell rows on `TranscriptComponent`, auth
  suspension on `Screen`, and diagnostics on the existing `NoticeSink` shape.
- Grow `FooterComponent`, rather than inventing a footer facade: it owns the
  built-in two-line value under the injected `PaintLock`, changes the complete
  pair in one lock section, and repaints after unlock. `FrameSources.footer_lines`
  reads that owner. Existing extension-footer callbacks and disposal keep their
  current order and lock boundaries.
- Delete the exact zero-caller set and all remaining listed delegates. Replace
  `tests/test_native_ui_screen_architecture.py::test_six_modal_methods_delegate_to_one_drive_loop`:
  requiring modal methods on `TerminalUi` is now the stale contract. New AST and
  identity assertions require the ten modal methods on `TerminalModalDriver`,
  one `Screen.drive` loop, no retired facade definitions/call sites, concrete
  owner receiver types at the production callers, and the same owner identities
  used by frame sources. Behavior/PTY tests retain event text, paint sequence,
  extension retirement scope, and callback-before/after-lock ordering.

**48a gates.** Preserve behavior and the concrete public `TerminalUi` name;
keep `native.ui` recursively free of `native.tui`/session/repl back-edges. Add no
alias, shim, re-export, broad Protocol, callable-laundering facade, lock, C901
pin, or boundary relaxation. Lower both ratchets after measurement. Expected,
not promised: remove **300–340 class AST lines** and **310–370 physical file
lines**, leaving about **516–556 class AST lines** and **979–1,039 physical
`tui.py` lines**. This is an intermediate estimate; its endpoints are not
independently combined with a fixed 48b reduction. Section 2e pairs the measured
48a result with the remaining reduction. A result outside the intermediate
range is re-measured and explained; the gate is ownership/call-site
completeness, not forcing the estimate.

### Slice 48b — terminal composition and startup ownership

- Complete `native/ui/composition.py` with frozen, slotted
  `TerminalCompositionInput` and `build_terminal_components(input) ->
  TerminalComponents`. Input is exactly the two streams, `cwd`, opaque
  custom-editor `host: object`, initial built-in footer pair, provider count,
  `ClipboardConfig | None`, and a typed
  `Callable[[], KeybindingsManager | None]`. It does **not** name, import,
  annotate, dynamically import, or string-mention `TerminalUi`; the recursive
  `native.ui` no-back-edge rule remains unchanged.
- Move the current `__post_init__` statement-for-statement into that builder:
  one `EditorState`, one `OverlayState`, one `TerminalDriver`, one `Screen`, the
  exact component constructors, `build_extension_chrome_owners`, and the exact
  `FrameSources` contributor order. Deferred links for the existing construction
  cycle close over the eventually assigned concrete `TerminalComponents` record;
  there is no optional service locator and callbacks cannot run before the
  record is complete. `TerminalUi.__post_init__` becomes only input assembly and
  one `self.components = build_terminal_components(...)` assignment. Its
  dataclass call signature, including `footer_lines`,
  `available_provider_count`, `keybindings_manager`, and `clipboard_config`, is
  unchanged; constructor-only values must remain regular dataclass fields and
  must not become `InitVar`s or otherwise change
  `inspect.signature(TerminalUi)`.
- `Screen` remains the **only** creator of the one `PaintLock(threading.RLock())`.
  Every component receives that exact object. Preserve the component pattern:
  owner record + injected `PaintLock` + repaint; one complete record transition
  per lock section; repaint, extension factory, disposal, terminal I/O, and
  operator callback ordering unchanged and outside locks wherever they are
  outside today. Do not construct another `RLock`, including through an alias or
  default factory.
- Move `_startup_blocks` to
  `native/startup_chrome.py::startup_history_blocks(cwd,
  include_workspace_defaults)`. That destination matches its only caller and
  actual dependencies (`pipy_version_label`, `discover_loaded_resource_names`,
  `HistoryBlockTuple`) without making `chrome.py` import upward into `native.ui`.
  Move `_submitted_text_is_local_command` to the existing input owner module as
  `ui/components/input_editor.py::submitted_text_is_local_command`; its only two
  callers are the retained raw loops. Delete both old definitions, imports, and
  any duplicate implementation.

**48b gates.** 48a must be green first. Assert the exact concrete record fields,
unchanged `inspect.signature(TerminalUi)`, thin constructor shape, one complete
composition graph, contributor order, shared lock identity, no callback under a
new lock, and recursive no-back-edge. Lower ratchets from measurement; add no
C901 pin. Let the measured 48a result be `F` physical file lines and `C` class
AST lines. The paired 48b estimate is a reduction of **`F - 760` through
`F - 710` physical lines** and **at least `C - 310` class AST lines**, producing
the **710–760 file / at-most-310 class** planning result above. Across the 48a
forecast those conditional reductions are 219–329 physical lines and at least
206–246 class AST lines; they are not standalone intervals whose opposite
endpoints may be crossed. Retry 49 is blocked unless the facade inventory is
empty and the upper edge is met.

### Retry slice 49 — tests/docs final audit only (complete)

49 ran after independent implementation review and all 48a/48b gates. It
contains no production refactor. It:

1. adds an AST/type-resolved, literal-`getattr`/`setattr`-aware exact field
   inventory for **`TerminalUi`, `RunControlState`, and `CodingSession`**,
   including simple receiver aliases, and fails on an unowned field, an external
   mutation, an unknown dynamic access, or a second owner module;
2. makes the unique `PaintLock` and `SessionStateLock` construction assertions
   alias-resistant by resolving import aliases and simple assigned call aliases,
   proving one wrapper construction at `ui/screen.py` and `repl/wiring.py`
   respectively, each around its one explicit `threading.RLock()` and with no
   default constructor;
3. pins the exact measured final `tui.py` physical/class values and retained
   definition set (`__post_init__`, `is_supported`, `start`, `read_line`,
   `wait_for_active_turn_interrupt`), keeps `coding/session.py` at or below 336,
   accepts `extensions/activation.py` at 1,807 under the **2,488** native ceiling
   set by out-of-scope `native/session.py`, and confirms both old C901 pins stay
   gone;
4. reruns the complete boundary/retired-name/module/call-site audit with no rule
   relaxation and no alias, re-export, broad facade, or hidden `native.ui` back-edge;
5. corrects `docs/parity-criterion.md` E2 from the stale compaction owner to
   `repl/loop_step.py` and E6 from the stale settings owner to
   `repl/settings_actions.py`/the current command route, then records the final
   milestone in this plan/backlog.

49 still fails closed if `tui.py` is above 760, if measurement does not honestly
support the ~720 target, or if any ownership/boundary/lock assertion is
incomplete. It may lower the upper edge to the measured value; it may not raise
it, adopt 1,349, move production code, or declare the program complete around a
failed gate.

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
| 48 | burn `read_line` 39 and `wait_for_active_turn_interrupt` 35 onto `apply_editing_key`; split `_deliver_chrome_event` 11; rename `ToolLoopTerminalUi` → `TerminalUi` (§2d) | T | none | succeeded historically: tui C901 pin deleted; final size gate later failed closed |
| 48a | retire/repoint the 14-line projections, 231-line modal/chrome/screen/footer facade, 70-line transcript facade and 9-line dead bash helper per §2e; add concrete `TerminalModalDriver` + `TerminalComponents`, repoint every measured production caller, replace stale modal-facade architecture test | T | existing concrete owners only | no new C901; intermediate estimate tui 979–1,039 / class 516–556; pair the measured result with 48b |
| 48b | move the 153-line composition transaction to typed `ui/composition.py` builder; move 78-line startup blocks to `native/startup_chrome.py` and 14-line local-command classifier to `ui/components/input_editor.py`; leave thin unchanged-signature `TerminalUi` constructor | T | `TerminalComponents`; one Screen-created `PaintLock` | no new C901; combined 48a+48b estimate is a 589–639 physical-line net reduction, yielding tui 710–760 / class ≤310 |
| 49 | **complete retry**, tests/docs only: exact field/lock ownership, final measured ratchets, boundary/retired-surface audit, parity E2/E6 ownership docs, final milestone; passed below the §2e upper edge | tests | — | no production code |

**Boundary edits, complete.** Slice 3 adds `"pipy_harness.native.repl"` beside every
`"pipy_harness.native.tool_loop_session"` (25 literal occurrences today — re-read the line numbers,
the 2026-08-03 list has drifted 9-14 lines) plus a new rule `source_package=
"pipy_harness.native.repl", forbidden_imports=("pipy_session",
"pipy_harness.native.tool_loop_session")`; that rule is what forces slice 40 to *delete* the
`session` field rather than quote it. Slice 36 adds the analogous `native.startup_selectors` rule.
Slice 46 deletes all 17 `"pipy_harness.native.extension_runtime"` entries and that module's member
of the four-module host tuple — each already covered by the stricter `native.extensions` rule. No
other slice edits a rule; **no rule is relaxed anywhere**.

**Good stopping points.** Historical stops remain: after **6**, the back-edge was
gone and the ratchet armed; after **21**, both original god files were roughly
halved; after **34**, every widget had a definition-site owner; after **44**, both
files were under the broad bar and the session pin was deleted; after **46**,
`extension_runtime.py` was retired. The old claim that 47–49 were merely polish
is superseded: the clean slice-49 audit blocked on 569 omitted class lines.
Slices 48a and 48b are now landed. The post-48b implementation stopping point and retry-49 final audit are both
reached. **The program is complete; there is no next decomposition slice.** All
§2e gates pass without changing production code or relaxing a boundary.

## 3a. Projected end state

The landed session/activation end state is measured, not projected:
`native/coding/session.py` is **336 physical lines** with a 67-AST-line/C901-5
`run`; `extension_runtime.py` is deleted; and
`native/extensions/activation.py` is **1,807 physical lines**, accepted by §2d.
Both former god-file C901 pins are gone and no replacement pin exists.

The terminal implementation result is now measured:
`native/tui.py` is **580 physical lines** and `TerminalUi` is **230 AST lines /
5 definitions / 9 fields**, a paired **769-file-line / 626-class-AST-line
reduction** from the failed-closed 1,349 / 856 checkpoint. The 710–760 and
at-most-310 figures remain preserved above as planning evidence; the
implementation legitimately beat both because the moved ownership regions also
retired more facade-only imports, comments, and spacing than forecast. Its
irreducible boundary is the four retained public/raw methods, a thin composition
assignment, constructor inputs/component handle, `_LiveExtensionUiDriver`, and
the 51-line generation proxy. Raw-mode loops stay in `tui.py`; modal driving
belongs to `TerminalModalDriver`; state/effects belong to the concrete owners in
`TerminalComponents`. Moving the proxy under `native.ui` is still forbidden
because its full-surface `__getattr__` would launder the back-edge past static
checks. Retry 49's exact audit passes and owns the completion declaration above.

The native-module ceiling is **2,488**, currently and intentionally set by the
out-of-scope `native/session.py`; activation at 1,807 is below it. The prior
“nothing above 1,700” and ~1,570 activation claims were projections superseded
by §2d's accepted measured result, not gates retry 49 may resurrect.

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
- Splitting the accepted 1,807-line `extensions/activation.py`: its state machine is guarded inside `_ActivationApi` but driven from outside via module-level capability tokens; subdividing exports a private capability.

## 5. How to tell it worked

1. **Field ownership is machine-checkable.** The final resolver enumerates every
   field of `TerminalUi`, `RunControlState`, and `CodingSession`, follows typed
   receiver/simple aliases and literal `getattr`/`setattr`, and requires one
   definition owner and no external mutation or unknown dynamic access. A grep
   over old underscored names is not sufficient.
2. **Measured ratchets close the arithmetic gap.** 48a and 48b lower file/class
   ratchets as they shrink. Their paired physical estimate is a 589–639-line net
   reduction from 1,349 into the 710–760 planning band; retry 49 pins the exact
   measured values, requires `tui.py ≤ 760` physical lines and only
   `__post_init__`, `is_supported`, `start`, `read_line`,
   `wait_for_active_turn_interrupt` on `TerminalUi`, keeps
   `coding/session.py ≤ 336`, `extensions/activation.py` at 1,807 or lower, and
   the native ceiling at 2,488 (`native/session.py`). No 550/1,700 fiction and
   no raise to 1,349.
3. **The coding session stays a facade.** `CodingSession.run()` remains at or
   below its measured 67 AST lines/C901 5 (and the existing ~80 architecture
   bound); it constructs no collaborator inline because `repl/wiring.py`
   returns frozen `SessionWiring` values.
4. **C901 stays burned down.** Neither `native/tui.py` nor
   `native/coding/session.py` appears in per-file ignores; no pin is added to a
   destination module.
5. **Boundary rules are strictly preserved:** recursive `native.ui` cannot name
   or import `native.tui`, `native.repl`, or `native.coding.session`; `native.repl`
   cannot import the coding session; retired `tool_loop_session.py` and
   `extension_runtime.py` names/modules remain absent; `_PLANNED_IMPORT_PREFIXES`
   is unchanged. No rule is relaxed and no alias/re-export/callable facade
   substitutes for one.
6. **Locks are typed and uniquely constructed.** Alias-resistant AST assertions
   resolve imports and simple call aliases and find exactly one `PaintLock`
   construction in `ui/screen.py` and one `SessionStateLock` construction in
   `repl/wiring.py`, each wrapping its explicit `threading.RLock()` and exposing
   no default constructor. Every composed UI owner shares the Screen-created
   `PaintLock`; no new `RLock` exists in 48a/48b.
7. **Parity diffs are file-to-file:** pi's `components/session-selector.ts` ↔
   `ui/components/session_picker.py`, `core/model-runtime.ts` ↔
   `repl/provider_selection.py`, `core/bash-executor.ts` ↔ `repl/local_shell.py`,
   `agent-loop.ts` ↔ `repl/loop_step.py`, `main.ts` ↔ `repl/wiring.py`,
   `core/extensions/runner.ts` ↔ `extensions/activation.py`, and
   `core/agent-session.ts` ↔ `native/coding/session.py::CodingSession`. E2/E6
   parity ownership text points to the actual compaction/settings modules.
8. Every implementation slice has focused/PTY characterization, `just check`,
   synchronized docs, and an independent review. Retry 49 is tests/docs only and
   declares the final milestone only after every gate above passes; otherwise it
   reports the block and stops.
