# Project trust interactive and management integration — execution plan

Status: implemented 2026-07-15; the slice design received a direct Claude Opus
`CLEAN` review before implementation; a final implementation gate is required
before commit.

Reviewed slice design:
[`2026-07-15-project-trust-interactive-management-design.md`](2026-07-15-project-trust-interactive-management-design.md).
Reviewed parent design:
[`2026-07-15-project-trust-design.md`](2026-07-15-project-trust-design.md).
Pi reference: `/Users/jochen/src/pi-mono` at `b084d2fb`.

## Ordered tasks

1. **Add shared trust choices and the startup selector.** Add typed trust-option
   construction to `native/project_trust.py`, including exact/parent/session
   choices and atomic update tuples. Add a standalone product-TUI selector in
   `native/tui.py` that reuses the settings-dialog navigation/frame, shows the
   canonical cwd and trust explanation, handles resize, and returns one option
   or cancellation. Wire it into the unresolved interactive-TTY resolver branch
   before project-derived runtime construction.

   Acceptance: choice order and parent-root omission match Pi; current/parent
   writes are exact; cancel is false/no-write; write errors fail closed; print,
   JSON, RPC, piped input, and list-models remain non-interactive and
   protocol-clean.

2. **Carry startup trust evidence into the live session.** Replace the interim
   slice-1 stderr diagnostic with a structured resolution result that records
   whether trust came from the no-resource short circuit. Thread only the
   resulting auto-reload candidate and resolved settings state through the
   adapter/session. Render a live untrusted warning after TUI creation only when
   protected project input exists.

   Acceptance: no protected input produces no warning; untrusted protected
   input produces one non-archived warning; selector/warning text never enters
   the native session tree or metadata archive; no project state is observed
   before resolution.

3. **Implement `/trust` and guarded reload persistence.** Add `/trust` to every
   executable/completion/description surface and dispatch it before provider
   input. In a live TUI, show canonical cwd, exact/inherited saved state, current
   state, and persistent current/parent/decline choices. In captured mode emit a
   non-blocking diagnostic. Before manual `/reload`, apply the reviewed
   auto-persist guards and include the result in the reload notice.

   Acceptance: `/trust` makes no provider/tool/session turn and never hot-loads
   resources; parent save is one atomic parent-true/child-delete update;
   restart-required status is explicit; auto-persist occurs only for the exact
   no-resource-start candidate when protected input newly exists and no saved
   decision exists; every negative guard and store-error path is tested.

4. **Expose the global default in `/settings`.** Add a typed global-only
   `SettingsManager.set_default_project_trust`. Add a settings row and local
   three-choice `Ask`/`Trust`/`Do not trust` selector, persist the chosen enum,
   and reopen/repaint the dialog without re-resolving the current run.

   Acceptance: only `ask|always|never` is accepted; the project scope cannot own
   this field; selection persists globally; cancel is inert; current trust and
   loaded resources remain unchanged.

5. **Gate package and config management.** Add ordered command-local trust flags
   to `install`, `remove`/`uninstall`, `list`, and `config`, plus `config
   -l/--local`. Build an initially untrusted settings manager, resolve the same
   core order for canonical `--cwd`, and only then enable project settings.
   Omit the project path from untrusted package listing and resource discovery;
   reject local writes before package/cache/discovery/settings mutation while
   retaining global operations.

   Acceptance: last override wins/no persistence; `--no-approve` overrides
   saved true; `--approve` permits local writes; untrusted list/config retain
   globals and omit project values; refusal names `--approve`; `update` code and
   target behavior are unchanged.

6. **Expand deterministic and real-PTY coverage.** Extend
   `tests/test_native_project_trust.py`, package/config/settings tests, and TUI
   real-PTY coverage. Expand
   `scripts/parity_checks/project_trust_conformance.py --json` with option,
   reload, settings, and management checks. Use isolated `PIPY_CONFIG_HOME` and
   session roots; do not mutate the user's theme/trust state.

   Acceptance: focused tests cover all reviewed rows and guards, including
   startup choices at 80x24, resize/cancel recovery, session-tree privacy, and
   management mutation ordering; the conformance gate remains deterministic
   and network/model-free.

7. **Close documentation and parity state.** Update the parent design/track
   plan status, settings/security/usage/package docs, `CHANGELOG.md`,
   `docs/pi-parity.md`, `docs/parity-plan.md`, `docs/pi-mono-gap-audit.md`, and
   `docs/backlog.md`. Mark slice 2 shipped while keeping slice 3 extension trust
   and update/config realignment as separate next gaps.

   Acceptance: user-facing selector, `/trust`, default enum, command flags,
   trust-not-sandbox warning, and restart/reload semantics are documented; no
   page claims extension trust APIs or update realignment shipped.

8. **Run the exact-diff gates and commit.** Run focused tests, the project-trust
   conformance gate, `just check`, and `prek run --all-files` only if configured.
   Run the direct Claude Opus review loop over the complete code/tests/docs
   diff, fixing and re-running all gates until `CLEAN` (maximum three review
   rounds). Commit that exact reviewed diff on `main` without pushing.

## Done when

All acceptance criteria pass; full validation is green; the unscoped final
review is `CLEAN`; the exact reviewed diff is committed on `main`; and only this
single parity gap has advanced.
