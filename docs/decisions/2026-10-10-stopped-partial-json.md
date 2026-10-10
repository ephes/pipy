# Stopped partial tool arguments

## Status

Accepted for the F6b stopped-turn semantics slice after independent
Claude Opus 5.5/high design repair review CLEAN (two rounds). Runtime
dependency expansion follows that acceptance; code review remains a separate gate.

## Context

Stopped provider calls contain streamed JSON prefixes. Pi stores the value from
`parseStreamingJson`, not the unfinished string. Pipy currently archives the
string and exposes an `_raw` wrapper in automation. Successful calls must keep
strict execution validation. Existing saved entries must not be rewritten.

## Decision

Use the zero-dependency Python `partial-json-parser` package
(`>=0.2.1.1.post7,<0.3`, locked by uv) in a small canonical agent leaf. It shares
upstream authorship with Pi's `partial-json` JavaScript parser. The package's
[primary documentation](https://github.com/promplate/partial-json-parser)
describes recoverable strings, objects, lists, numbers and booleans.

Normalize only when assembling a newly completed aborted/error assistant.
Try strict JSON, repaired strict JSON, partial JSON, then repaired partial JSON;
repair only raw string control characters and invalid backslash escapes, following
Pi's string repair. No generation or retry is involved. At most four parsing
attempts run once per stopped call. Unrecoverable input yields `{}`; non-finite
values and excessive recursion also yield `{}`, keeping durable/automation JSON
valid. Preserve recoverable JSON values, including top-level arrays, strings,
numbers and booleans: automation accepts JSON values, not only dictionaries.
Following the actual Pi function, a strictly parsed complete `null` stays null;
a partial null from the fallback parser becomes `{}`. Test all these shapes.
Keep call IDs, order and signatures. Store the recovered value encoded as
JSON in the existing canonical argument field; no session migration is required.
Successful calls, raw provider stream adapters and old saved entries are unchanged.

## Alternatives

A custom recursive parser adds avoidable correctness and maintenance risk.
An ordinary strict parser cannot recover streamed prefixes. Broad JSON repair
libraries repair beyond Pi's narrowly defined string/stream behavior.

## Consequences

One small pure-Python runtime dependency is added. Upstream omits `py.typed`;
a local stub describes only the used `loads` interface, verified against the
installed signature. No untyped-import suppression or strictness relaxation is
introduced. The WASI guest does not import
this leaf and remains isolated; parent runtime packaging carries the dependency
normally. An unfinished property name may disappear, an unfinished string value
may be retained, and malformed input may become empty, following Pi's best-effort stream parsing. The finite-value guard is an
intentional pipy divergence: if any member is non-finite, the whole value becomes
`{}` (including otherwise recoverable siblings), rather than Pi's JavaScript
serialization of non-finite values as null. Do not claim preservation of every raw argument byte. Stopped
calls never execute or replay as calls; their recovered partial paths may appear
in bounded attempted-file metadata, explicitly labelled as unexecuted/partial. Existing transcript entries stay intact.

## Verification/Reversal

Production-session tests reproduce the baseline failures, then check cancellation
and failure, prefixes, nesting, escaping, non-finite input, durable reopen,
automation object shapes and later continuation. Existing ordinary malformed-call
and architecture/isolation tests remain gates. Compare representative prefix
results with the local Pi implementation. Run focused tests, required codemode
runtime tests, full `just check`, docs build and independent Opus 5.5/high review.
Reversal removes the dependency and assembly normalization, returning newly
stopped calls to raw strings without rewriting saved entries.
