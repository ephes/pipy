# DE14b2 live restart-policy evidence

The implementation follows the [adopted plan](../specs/2026-10-28-de14b2-live-restart-policy-plan.md)
against delivered DE14b1 `2d3c7c0cda15c19be9333eba500f1220ba3c4fd3`.
The first PLAN review found the sticky open-admission marker and exact-policy
input requirements; the contained repair was independently verified CLEAN.
That plan result is not whole-code or runtime acceptance.

Initial development ran in complete private source copies. A first core run
retained 110 passing cases and four setup failures plus four teardown errors:
the new fixture attempted to patch a slotted Session instance method. The
correction wraps the class method only for the actual target Session. The
114-case follow-up passed, including a real recovery commit-release before
open-publication barrier, cancellation of the open waiter, both closed SDK
guards, full inspection shape and the exhaustive AST inventory. Mypy passed
for 1,088 files and lint passed. A formatter-only line wrap in the new test was
recorded separately; the AST was identical, and the final format check passed.

The first complete development snapshot contains 4,256 files and exactly two
native changes plus the new core test. Worker tests and the pinned reference
profile must bind immutable snapshots, not evolving Main. Old comparison
manifest hashes remain intentionally unpromoted until canonical integration.
All failures, actual source maps, normal-exit module observations and process
receipts remain outside Git; normal-exit observations are not an exhaustive
child-process census.

Subsequent current-source checks passed 857 existing migration/pause and C1/C2
receipt/progress/atomic/reader cases, 182 import-boundary cases and 152 strict
coverage checks. A separate direct-native bridge uses the existing real
receipt-after SIGKILL fixture: recovery acquires count five, default parking
holds, private publication to six preserves the row/receipt/effects, and explicit
resume settles the actual winner without repeating the old call. Its initial
oracle incorrectly expected a receipt2 envelope; the corrected passing test pins
normal receipt1's unchanged output/call/key entry carrier. It is not evidence for
C2 nonce replay or an exposed Product setter. Final platform helpers must register
this new test context explicitly before effects. Native bytes are unchanged from
the initial worker snapshot; added tests/docs have their own source epochs.

A subsequent core-local observation keeps the shared SQLite actor unchanged:
the exact hashed actor receives one scoped cut-event extension, recording actual
loaded native modules, arguments, working directory, executable and process group
before SIGKILL. A separate sidecar records the real child-context exit, wait and
observed group absence. The focused case passed on those new test bytes; the
earlier process record and proof epochs are not retroactively completed. This
captures that one child, not an exhaustive process census.

The integrated seven-file reference source patch initially needed one mechanical
import-order correction under repository Ruff settings. That source-only patch
has no producer execution claim; actual reference checks must bind the integrated
bytes and complete 27-phase closure. The first docs build and 3,633 local-link
checks passed on their captured source epoch, not future edits.

The isolated worker's complete 62-case follow-up passed on the initial snapshot.
Its initial 38 passing cases, 24 setup failures and 24 teardown errors are
retained: class-level, exact-owner-filtered observers replaced invalid instance
patches of slotted Session methods without changing the test assertions. The
passing cases cover real Session ticket ordering, revision-sensitive no-write
oracles, cancellation/close/loop teardown and six actual normal-exit lease actors.
Their recorded origin and process scope is explicit; they are not physical
SIGKILL or current integrated-source evidence. Only the two portable test files
are adopted; the original snapshot and all proof packets remain immutable.

The first bounded integrated reference execution passed the 17 native comparison
tests and fresh base acceptance, then Pi exited 13 at an unsettled top-level
`waitForIdle`: a parked live root correctly remains non-idle. Paired native,
semantic CLI controls and fresh APIs had not run. The retained failure led to a
driver-only correction: the first resumed parked branch uses an actual FIFO
Session-line observation after verifying the fresh owner is paused; runnable
branches keep completion waits. All five cases and 13 mutants remain. Corrected
source and manifest bindings require their own authorized execution; neither the
failed reference epoch nor the earlier whole-code preparation is rebound.

Delivered at `be63fcd142b754a400dc5e60a22d0725afff0e0f` after independent whole-code
CLEAN, exact-source final checks, 20,254 full-suite passes plus the same 20 known
skips, all six runtimes with 2,764 focused passes each, and two fresh cumulative
27-phase reference chains. Resource acceptance retains three actual passing
cases, 105 census rows and three joined overlaps with unchanged finite limits.
The observer was narrowly optimized to ignore non-return events after its
disabled check; original failed candidate/baseline diagnostics and the original
controller metadata-guard failure remain separate immutable evidence. The
corrected artifact-only audit qualifies the derivative instead of claiming
original observer byte identity. All earlier paragraphs describe their actual
historical source epochs, not final-source rebinding. No Product live setting,
registry handover, parent DE14/DE14b or whole parity closure is asserted.
