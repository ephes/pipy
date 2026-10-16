# minimatch globs for resource patterns

## Status

Accepted for MCP parity slice X3 (goal check r5, finding U1). The slice's
brief prefers a maintained library over a home-made matcher; this record
says why pipy ports minimatch instead. Code review is the slice's review
gate.

## Context

Pi matches the `!` entries of the `extensions`, `skills`, `prompts` and
`themes` settings with minimatch 10.2.6 and its default options
(`matchesAnyPattern` in `package-manager.ts`). Users turn the built-in
extensions off with them: `!builtin:{mcp,codemode}` or
`!builtin:+(mcp|codemode)` turns MCP and codemode off in Pi. pipy matched
the built-in extensions' `!` entries, and its own `-`/`+` resource
directives, with the standard library's `fnmatch` plus a home-made rule for
`**/`, so braces and extended globs matched nothing, the built-ins stayed
on and the MCP servers still connected. `fnmatch` also lets `*` cross a
`/` and match a leading dot, which minimatch does not.

minimatch is more than a glob-to-regex translation: a leading `!` run
negates, a `#` pattern is a comment, braces are expanded by the
brace-expansion package before the pattern is split at `/` (with Bash's
rules for `${...}`, `{a}` and escapes, and a cut at 100 000 results and
4 000 000 characters), `..` drops the segment before it, its globstar walk
stops after 200 sections, its extended globs are merged into their parents
or, nested more than two deep, read as text, `!(...)` looks at the rest of
the pattern, its POSIX classes are Unicode properties, and a segment
without one is matched in UTF-16 code units.

## Decision

Port the parts of minimatch that Pi's matching uses, with no new
dependency:

- `pipy_harness.native.brace_expansion` ports brace-expansion 5.0.9 and
  balanced-match 4.0.4.
- `pipy_harness.native.glob_segment` ports minimatch's `AST`, `parseClass`,
  `unescape` and the fast tests of `Minimatch.parse`, and builds the same
  regular expression for Python's `re`: `$` becomes `\Z`, a Unicode
  property in a set lists its characters, from the general categories of
  Node 26's engine (Unicode 17.0) that `scripts/unicode_properties.py`
  writes into `pipy_harness.native.unicode_properties`, and
  without the `u` flag the pattern and the segment are compared in UTF-16
  code units.
- `pipy_harness.native.minimatch` applies the negation and comment rules,
  splits at `/` with the `..` and adjacent `**` rules, and walks the `**`
  segments with minimatch's head, tail, dot and 200-section rules and its
  bound for each section's last start, in one pass per section, so many
  globstars do not multiply the work.

The resource directives (`is_resource_enabled`) and the built-in
extensions' `!` entries both use it. For the built-in extensions the `!`,
`+` and `-` entries are also tried, as in Pi, on the path relative to the
settings file's directory, resolved against the workspace (pipy's working
directory or `--cwd`) as Pi resolves it against its working directory, and
`pipy config enable|disable builtin` drops the entries that name it that
way too.

## Alternatives

`wcmatch` (with `bracex`, which it uses for braces) was tried first. It
matched most patterns as minimatch does, but each difference needed an
adjustment around it: the negation, comment and dot rules, `**(`, Unicode
classes and UTF-16 matching, `${...}` inside a brace group, brace output
that it refuses where brace-expansion cuts it, and the items of a set after
a POSIX class. Nested extended globs could not be adjusted: minimatch
merges them into their parents, reads them as text beyond two levels and
has its own rule for an empty `!()`, and on generated nested patterns
`wcmatch` gave other results, raised `re.error` for patterns minimatch
accepts, or backtracked for minutes where minimatch took milliseconds. Its
globstar is a regular expression that backtracks too (`**/a/` fifteen
times against thirty `a/` segments takes seconds).

## Consequences

About 1 300 lines of ported code, and a generated table of Unicode
categories, to keep in step with minimatch and Node when Pi updates them.
Compared with minimatch 10.2.6 on Node 26 on about 1 000 000 generated
pairs of path and pattern (random glob text, POSIX classes over 32 000 code
points, emoji and accented letters, and 108 000 pairs of nested extended
globs), the results are the same except where minimatch raises (36 nested
pairs, "Nothing to repeat") and for 35 nested pairs that Python's engine
did not finish within a minute, where Node took at most 0.6 s; the
brace-expansion port gives the same output as the package on 20 000
generated patterns and 3 000 numeric sequences. What still differs:
braces nested so deeply that Python's recursion limit is reached (several
hundred levels) match nothing; a pattern Python's `re` cannot compile, for
which minimatch raises in most cases, matches nothing; the Unicode tables
are Node 26's, so another Node release can read a newly assigned character
differently; and the time a pathological pattern takes can differ, since
both engines backtrack, but not alike. A `*` in a resource directive no
longer crosses a `/` or matches a leading dot.

## Verification/Reversal

`tests/test_native_brace_expansion.py`, `tests/test_native_glob_segment.py`
and `tests/test_native_minimatch.py` pin brace-expansion's and minimatch's
results, their quirks included, and the time the globstar walk takes; the
built-in extension and resource tests cover braces, extended globs and the
relative path. Reversal removes the three modules and returns `_matches`
to `fnmatch`.
