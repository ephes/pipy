# DE14b4 missing-definition orphan evidence

This bounded private control follows actual DE14b3 delivery at
`ad21f13bd103ed39f3326d645d12d8e5f9476307`. Its
[contract](../specs/de14b4-orphan-missing-definition.md) and
[test matrix](../specs/de14b4-orphan-missing-definition-tests.md) retain the
adopted ownership, storage and worker-lifetime requirements. PLAN R1 reported
zero Critical, two Warning and two Suggestion findings. The contained R2 repair
returned CLEAN with no findings. That is plan evidence, not CODE or final runtime
acceptance.

The initial native implementation adds a private strict ownership query and
one Session commit control, with sync/async scheduler wrappers. It leaves
ordinary dispatch ownership, backend revision policy, public SDK/CLI and
model/tool receipt authority unchanged. The first ordinary whole mypy run passed
for 1,102 source files. Initial Ruff reported one complexity issue, resolved by
extracting the iterative cycle traversal without changing graph predicates.
The first core run passed 12 cases and failed two fixture setups: existing Tx
correctly refuses creating a background owned child. The corrected fixture uses
a genuine terminal owned child; its complete core run passed 14 cases on both
Memory and SQLite. Separate unchanged SDK/inspection consumers passed 57 cases.
These are source-bound development results, with private state and retained
failure epochs; they are not summed into a whole-suite or final-source claim.

The isolated D0 broad overlay passed 59 cases. An earlier observer epoch had
59 passing test calls but a module teardown error because its private process
observer rejected the existing fake-MCP cleanup `ps` command; the fresh narrow
administrative observer repair preserves that failure separately. Four actual
normal SQLite lease-openers and their source bindings were checked. The genuine
C2 authority overlay separately passed one test, Ruff/format and ordinary whole
mypy for 1,103 files: a real interrupted receipt (document version 1, value version
2), same-nonce replay, empty-catalog cold owner and all-nine-table refusal/replay
oracles preserve the saved winner without new effects.

The formatted seven-file reference overlay passed its separately scoped cheap
schema/CLI and ordinary type/lint checks. Those checks did not run the actual
paired native/Pi producers or fresh API attesters. Batch integration adds these
exact portable sources and the canonical comparison child; each prior result
retains its own immutable source epoch. New integrated checks and paired
producer results require their own source-bound receipts. No aggregate count
or whole-current-source PASS is inferred from these separate results. CODE review, full-suite, six-runtime, resource and cumulative reference
gates remain separate requirements. General abort/cascade, active cancellation,
wait settlement, public Product controls and all parent DE14/15/16/23 and P
obligations remain open.


The first direct producer attempt stopped during reference-support import:
the unrelated rewindable conversation document lacked its required fork policy.
No Session, target task, store or orphan action was exercised by that failure.
Pinned Pi also requires an explicit policy; there is no implicit fork default.
The coupled fixture correction supplies `fork="current"` in native support and
`fork: 'current'` in the Pi declaration, retaining rewindable history and every
predicate. The earlier integrated bounded tests deliberately excluded producer
fixtures and remain a separate pre-fix source epoch. Corrected producer behavior
and CODE/final acceptance are not inferred from this fixture correction.


The corrected direct development epoch used immutable D0 plus the exact coupled
seven-file reference overlay. Five actual native cases and five real pinned-Pi
Harness cases passed the bounded projection, with twelve successful CLI
comparisons, twelve expected semantic mismatches and one malformed-input exit.
Independent artifact acceptance joined eighteen read-only SQLite snapshots with
all retained tables/revisions, ten callback databases with initialization only,
and actual native/Pi module bindings and recorded process closure. This is
source-qualified direct development evidence, not canonical cumulative chains,
fresh API attesters, full-suite/platform acceptance or CODE review. The failed
pre-fix import epoch remains separate; no product behavior was exercised there.


Implementation CODE R1 reported zero Critical, one Warning and one Suggestion:
strict refusal coverage was incomplete and the adopted test matrix still used
proposal wording. The isolated refusal overlay passed 98 tests on the original
`f478716d` source plus its two test files. Integration of that overlay and the
matrix/closure repair passed 358 tests across the six selected modules on source
`0ee8a712`, together with ordinary whole type/lint/format and strict documentation
checks. These are separate source-bound development epochs, not aggregate or
final-suite results; the refusal controls do not claim mutation completeness.

Contained CODE R2 returned ISSUES with zero Critical, zero Warning and two
Suggestions, and converged to terminal advisory status. Its suggestions concern
confirming that a typed background-child page substitution actually ran and
recording these evidence epochs. The review is not relabelled CLEAN. Both
suggestions receive bounded test/evidence corrections and subsequent checks;
full-suite, six-runtime, resource, cumulative reference and delivery gates remain
pending. The advisory repair does not change native runtime behavior.
