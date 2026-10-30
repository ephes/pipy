# Durable release 1.4 bridge

The target is literal release `v1.4.0`, commit `4a080252f06caf63a525d7873c5d94d3c23887e0`, not postrelease HEAD. Public retryTask and its reset-then-resume semantics remain present. Existing dated 1.3 delivery and development evidence remains historical.

## Required document policy

Native Doc, DocFamily and both factories require keyword-only callable `checkpoint_when`; typed acquisition revalidates it. Owners intending ordinary deltas supply an explicit false callback. Existing true, threshold and bound-receiver callbacks are preserved. Eligible nonempty loaded updates invoke the policy with the prepared value/ops and prior delta count; creation/empty/noneligible plans do not. Python rejects missing/None definitions at declaration; TypeScript requires the member statically, while erased untyped Pi input may fail only at an eligible update. These error timings are not asserted equal.

The current checkpoint input/expected/manifest files are `session-checkpoints-v2.*`, profile `binary64-utf16-ordered-required-policy-v2`; phase key/order and parent edges stay `session-checkpoints-j-v1`. The v2 expected payload is a normative subset derived from actual historical Pi1.3 data, with a new profile label, not a fresh1.4 observation or native-generated expected output. Original compressed fixture SHA256: `80fcd130aa535a13c2a6191565a1df60436dc91d65190cc18290759b06649d10`. Retained case order and raw retained case values are unchanged. Removed obsolete successful missing-policy case IDs: `counts-absent`, `zero-absent-positive-simple-empty`, `zero-absent-positive-simple-dirty`, `zero-absent-positive-accessed-empty`, `zero-absent-positive-accessed-dirty`, `zero-absent-positive-untouched-empty`, `zero-absent-positive-untouched-dirty`, `zero-absent-negative-simple-empty`, `zero-absent-negative-simple-dirty`, `zero-absent-negative-accessed-empty`, `zero-absent-negative-accessed-dirty`, `zero-absent-negative-untouched-empty`, `zero-absent-negative-untouched-dirty`. Native constructor/resolve tests replace the unsupported declaration obligation; false/true/count/empty/creation/rollback and receiver behavior remain. A real release run must validate this expected oracle before release acceptance.

## Open portable obligations

Release Harness wraps Models with merged ClientIdentity (default Pi attribution, host override, request override last, explicit null attribution clears). Model proxies must forward request options and receivers and retain allowed identity raw facts. Phase34 generic retry callbacks make no model request and do not prove P16; native identity carrier/merge/header work is the next separate slice. No production identity behavior is changed here.

CodeMode StoreDoc uses prior `deltasSinceBase >= 64`; RunDoc/JournalDoc use false. This is an open DE35–37/P36–38 obligation with boundary63/64/65, fork/as-of and atomic store/result witnesses, not a global Doc default. Concrete browser IndexedDB remains excluded by host scope; portable storage contracts remain included. Windows Node changes do not imply Windows runtime qualification. All43 parent criteria remain open.

## Validation epochs

The earlier4371 source and queued-four development are separately qualified historical inputs. New release bootstrap, comparison, cumulative chains and final gates require actual receipts; no old PASS is rebound here.

The 2026-10-29 CODE review identified an additional native signed-zero test
owner whose existing keyword could still evaluate to None. Its obsolete
missing-policy success lane was removed; false/true policies retain exact
persisted replay and callback identity assertions. The earlier lexical
missing-keyword audit did not establish that all supplied policy expressions
were callable. Earlier test totals remain tied to their recorded epochs; current
collection and ordinary full validation must establish the revised coverage.

## Complete reference pin identity

Reference identity includes each manifest's `upstream_tests` as well as its
`reference_sources`. The Session-commit, document and checkpoint manifests' declared upstream test pins
must come from literal release `4a080252f06caf63a525d7873c5d94d3c23887e0`;
retaining their older `2ec55d808d455b1a64a2bf7888198207a096120a` digests under
a new `reference_commit` is not a valid release binding. Live differential phases verify those
actual archived files before their drivers run. Native/unit manifest shape and
parent-digest checks alone do not attest these Pi file bytes. The nine upstream-test field
corrections change no producer, runtime, oracle values or validation predicate;
earlier exact-source receipts retain their original epoch and require an
explicit source qualification before reuse.
