# TOOLS1: Pi output handling for `bash`, `grep`, `find`, `ls`

Status: plan, 2026-09-29. Source: the TOOLS1 follow-on in `docs/backlog.md`
(recorded by READ1). Base: `main` at `0272a961`.

## Problem

None of the four tools is silent when it cuts, but each cuts differently from
Pi, and the model cannot page past the cut:

- `bash` keeps the last 16 KB of bytes and appends `(output truncated)`. The
  rest of the output is gone.
- `grep` is literal-only, stops at 100 rows (32 KB) and appends
  `... (truncated)`. It has no `limit`, `glob`, `ignoreCase`, `literal` or
  `context`.
- `find` stops at 200 results, `ls` at 200 entries (and `path` is required),
  both with `... (truncated)`. Neither takes a `limit`.
- `find` matches with `Path.glob` (so `*.ts` only looks in the search root),
  `ls` prints `file x` / `directory y` rows, and `grep`/`find` print
  workspace-relative paths.

## Pi reference (`~/src/pi-mono` @ `4df157433`)

`packages/coding-agent/src/core/tools/{bash,grep,find,ls,truncate,output-accumulator}.ts`.

### Shared (`truncate.ts`)

- `DEFAULT_MAX_LINES = 2000`, `DEFAULT_MAX_BYTES = 50 * 1024`,
  `GREP_MAX_LINE_LENGTH = 500`. `formatSize` and `truncateHead` are already
  ported (READ1).
- `truncateTail(content, {maxLines, maxBytes})`: no truncation when both limits
  hold; otherwise walk lines from the end, adding `bytes(line) + (n > 0 ? 1 :
  0)` while it fits. If the very last line alone is over the byte limit, keep
  its last `maxBytes` bytes (advanced to a UTF-8 character start) and set
  `lastLinePartial`. `truncatedBy` is `lines` when `outputLines >= maxLines`
  and bytes fit, else `bytes`.
- `truncateLine(line, maxChars = 500)`: `line` if `len <= maxChars`, else
  `line[:maxChars] + "... [truncated]"`.
- `truncateMiddle` has no caller among these tools and is not ported.

### `bash`

- Schema: `command` string, `Shell command to execute`; `timeout` optional
  number, `Timeout in seconds (optional, no default timeout)`.
- Description (exact): `Execute a bash command in the current working
  directory. Returns stdout and stderr. Output is truncated to last 2000 lines
  or 50KB (whichever is hit first). If truncated, full output is saved to a temp
  file. Optionally provide a timeout in seconds.`
- `OutputAccumulator`: streaming UTF-8 decode (replacement characters), a
  rolling decoded tail of `2 * maxBytes`, line/byte counters over the whole
  stream. Raw bytes are kept in memory until the output passes 2000 lines or
  50 KB; then a temp file `os.tmpdir()/pi-bash-<16 hex>.log` is opened, the
  buffered raw chunks are written, and every later chunk is appended. The
  snapshot is `truncateTail(tail)` with the whole-stream `totalLines` /
  `totalBytes` substituted.
- Notice (appended after the output with a blank line), `startLine =
  totalLines - outputLines + 1`, `endLine = totalLines`:
  - last line partial: `[Showing last ${formatSize(outputBytes)} of line
    ${endLine} (line is ${formatSize(lastLineBytes)}). Full output: ${path}]`
  - by lines: `[Showing lines ${start}-${end} of ${total}. Full output: ${path}]`
  - by bytes: `[Showing lines ${start}-${end} of ${total} (50.0KB limit). Full
    output: ${path}]`
- Empty output is `(no output)`. The same notice precedes the `Command timed
  out after N seconds` / `Command aborted` status.

### `grep`

- Schema (exact descriptions): `pattern` string `Search pattern (regex or
  literal string)`; optional `path` `Directory or file to search (default:
  current directory)`, `glob` `Filter files by glob pattern, e.g. '*.ts' or
  '**/*.spec.ts'`, `ignoreCase` boolean `Case-insensitive search (default:
  false)`, `literal` boolean `Treat pattern as literal string instead of regex
  (default: false)`, `context` number `Number of lines to show before and after
  each match (default: 0)`, `limit` number `Maximum number of matches to return
  (default: 100)`.
- Description (exact): `Search file contents for a pattern. Returns matching
  lines with file paths and line numbers. Respects .gitignore. Output is
  truncated to 100 matches or 50KB (whichever is hit first). Long lines are
  truncated to 500 chars.`
- Runs `rg --json --line-number --color=never --hidden [--ignore-case]
  [--fixed-strings] [--glob G] -- pattern searchPath` (Pi downloads `rg` when
  missing). `effectiveLimit = max(1, limit ?? 100)`; `context > 0` else 0.
  Counts `match` events and kills `rg` when the count reaches the limit
  (`matchLimitReached`). A non-0/1 exit that was not the limit kill fails with
  rg's stderr. No matches: `No matches found`.
- Path label: relative to `searchPath` (posix) when it is a directory, else the
  file's basename. Match line `${path}:${n}: ${text}`; context line
  `${path}-${n}- ${text}`; text has `\r` removed and goes through
  `truncateLine`. Context lines come from the file read as UTF-8 and split on
  `\r\n`/`\r`/`\n`; an unreadable file gives `${path}:${n}: (unable to read
  file)`.
- Output is `truncateHead(rows, maxLines = ∞)` (50 KB). Notices, joined with
  `. ` inside one `[...]` after a blank line: `${limit} matches limit reached.
  Use limit=${limit*2} for more, or refine pattern`; `50.0KB limit reached`;
  `Some lines truncated to 500 chars. Use read tool to see full lines`.

### `find`

- Schema (exact): `pattern` string `Glob pattern to match files, e.g. '*.ts',
  '**/*.json', or 'src/**/*.spec.ts'`; optional `path` `Directory to search in
  (default: current directory)`, `limit` number `Maximum number of results
  (default: 1000)`.
- Description (exact): `Search for files by glob pattern. Returns matching file
  paths relative to the search directory. Respects .gitignore. Output is
  truncated to 1000 results or 50KB (whichever is hit first).`
- Runs `fd --glob --color=never --hidden [--no-require-git] --max-results N
  [--full-path] -- pattern searchPath`. Observed on fd 10.5.0:
  - a pattern without `/` matches the **basename** at any depth (`*.ts` finds
    `src/a/y.ts`);
  - a pattern with `/` sets `--full-path` and gets a `**/` prefix (unless it
    starts with `/` or `**/`, or is `**`); `*` and `?` do not cross `/` there
    (`**/src/*.ts` does not match `src/a/y.ts`);
  - smart case: case-insensitive unless the pattern has an uppercase letter;
  - `{a,b}` alternation and `[...]` classes; an invalid glob is an error;
  - directories are listed with a trailing `/`, symlinks are not followed and
    are listed as entries;
  - `--max-results 0` means no cap.
- Output: each path relative to `searchPath`, posix separators, trailing `/`
  kept; `truncateHead(maxLines = ∞)`. Notices: `${limit} results limit reached.
  Use limit=${limit*2} for more, or refine pattern` when `results >= limit`;
  `50.0KB limit reached`. No results: `No files found matching pattern`.

### `ls`

- Schema (exact): optional `path` `Directory to list (default: current
  directory)`, `limit` number `Maximum number of entries to return (default:
  500)`.
- Description (exact): `List directory contents. Returns entries sorted
  alphabetically, with '/' suffix for directories. Includes dotfiles. Output is
  truncated to 500 entries or 50KB (whichever is hit first).`
- Entries sorted case-insensitively (`toLowerCase` + `localeCompare`); an entry
  is `name` or `name/` for a directory (stat follows symlinks); an entry that
  cannot be stat'ed is skipped. The loop stops with `entryLimitReached` when a
  further entry exists after `limit` rows. Empty: `(empty directory)`. Errors:
  `Path not found: …`, `Not a directory: …`.
- Notices: `${limit} entries limit reached. Use limit=${limit*2} for more`;
  `50.0KB limit reached`.

## Decisions

### External binaries (evidence)

Pi shells out to `rg` (grep) and `fd` (find) and downloads them when they are
missing (`utils/tools-manager.ts`). pipy is stdlib-first and never downloads
binaries.

- **grep keeps `rg` when it is on `PATH`, with a stdlib fallback.** pipy's
  grep already does this. `rg` gives Pi's regex dialect and speed on large
  trees; the fallback uses Python `re` (`re.escape` for `literal`,
  `re.IGNORECASE`). Its `glob` follows `rg --glob`, not fd: always
  case-sensitive (no smart case, `ignoreCase` does not apply to it); a glob
  without `/` matches the basename at any depth; a glob with `/` matches the
  path relative to rg's working directory, not the search path (a leading `/`
  anchors there too); a leading `!` negates it (matching files are excluded,
  and matching directories are not descended); and a `path` naming a file is
  searched whatever the glob. Observed on rg 15.2.0: with cwd `t` and path `t/src`, `src/*.txt`
  matches `src/n.txt` and `a/*.txt` matches nothing; with cwd `t/src`,
  `a/*.txt` matches. Pi spawns rg in its process cwd; pipy runs rg (and bases
  the fallback's relative paths) in the resolved root: the workspace root, or
  the reference root for a `--read-root` path. The glob translator is shared
  with `find`, parameterised on case. The fallback
  walks directories in sorted order, stops once `limit` kept matches are
  found, reads files line by line, and skips files with a NUL byte in the
  first 64 KB (rg's binary rule). Code review added: the fallback runs as a
  child process (`python -m …grep_fallback`) that prints rg-style JSON, so
  the tool reads both engines the same way and can kill a backtracking
  Python regex at the limit or on cancellation. Differences in the
  fallback: Python `re` syntax instead of Rust regex, and no size cap (the old
  1 MB scan cap is removed, as rg has none).
- **find is pure Python with fd's observable semantics.** fd is rarely
  installed (Pi downloads it), and the Python walk gives one deterministic
  behaviour everywhere. A small glob-to-regex translator implements globset
  rules (`**`, `*`/`?` not crossing `/`, `[...]`/`[!...]`, `{a,b}`) plus the
  basename/full-path split, the `**/` prefix and smart case. Like `fd
  --max-results`, the walk stops as soon as `limit` matches are found, so a
  bounded call never scans or holds the whole tree. The walk is depth-first
  with each directory's entries in sorted order, so results are deterministic
  (fd's order is unspecified); they are printed in walk order, not re-sorted.
  `limit = 0` walks everything, as `--max-results 0` does.
- **ls is pure Python, as in Pi.**

### Kept pipy policy (out of scope: READ2)

Paths still resolve through `resolve_tool_path` (workspace plus `--read-root`
roots; absolute paths elsewhere and `..` are argument errors), and `.git`,
`.gitignore` matches and generated directories/suffixes are refused as a
search root and filtered from results. `rg --hidden` would descend into `.git`;
its matches are filtered out after parsing, and the limit counts only kept
matches. `find` still refuses patterns that start with `/` or contain `..`.
The path-policy error texts (`path is ignored or under .git/generated
directories`, `path does not exist`, `path is not a directory`) stay; Pi's
`Path not found:` texts arrive with READ2.

One consequence: the bash temp file lives in the system temp directory, which
pipy's `read` refuses (it is outside the workspace and read roots). Pi's model
would `read` it. Until READ2, the model pages the file with `bash` (`sed -n`,
`tail`, `grep`), which has no path restriction; the notice text stays Pi's.
The live verification exercises that route. A narrow `read` exception for
pipy's own temp files is not added: on a shared `/tmp` it would need owner,
regular-file and no-symlink checks, which is READ2's path-policy work.

### Content refusals removed

The stdlib fallback's control-character and secret-shaped refusals go, as READ1
removed them from `read`: the `rg` path never had them, and Pi has none. Binary
(NUL) files are still skipped, like rg.

### Scope limits (follow-on TOOLS2)

- `bash` keeps pipy's status framing (`exit code: N` + `[output]`, a non-zero
  exit is not `is_error`) and its `bash: command timed out after Ns` /
  `bash: command cancelled` lines. Pi appends `Command exited with code N` and
  marks it an error. Only the output body and its notice change here.
- The `!`/`!!` shell shortcut (`run_local_command`) keeps its 16 KB tail; Pi's
  `bash-executor.ts` uses `truncateTail` plus a temp file.
- Tool-row headers keep their current shape (`grep "p" path`); Pi's renderers
  show `/p/ in path (glob) limit N`.
- `limit`/`context`/`timeout` stay `integer` in pipy's schema subset (no
  `number` type), as READ1 did for `offset`/`limit`.
- `grep` line truncation counts code points; JS counts UTF-16 units.
- `ls` sorts with `str.lower()` code-point order; Pi uses `localeCompare`,
  which orders punctuation differently.

### pipy shape

- `truncate.py` gains `truncate_tail`, `truncate_line`,
  `GREP_MAX_LINE_LENGTH`, and a byte-from-end helper; the module docstring's
  "only read's helpers" note goes.
- New `tools/output_accumulator.py`: a port of `OutputAccumulator` with
  `append(bytes)`, `finish()`, `snapshot(persist_if_truncated)`,
  `close_temp_file()`, `last_line_bytes`. Temp files are
  `tempfile.gettempdir()/pipy-bash-<16 hex>.log`, owner-only (`0600`), and are
  not deleted (Pi keeps them).
- `bash.py`: `_stream_output` feeds a store object. The bash tool uses the
  accumulator; `run_local_command` keeps a byte-tail store with today's 16 KB
  behaviour. The `max_output_bytes`/`HARD_MAX_OUTPUT_BYTES` knobs go from
  `BashTool` (no deprecation shims). Schema and description become Pi's; the
  `command` length cap goes (Pi has none); `timeout` keeps `minimum: 1` and
  gets Pi's maximum (2147483 s).
- `grep.py`, `find.py`, `ls.py`: Pi schemas and descriptions, `limit`
  defaults, notices and row shapes; the constructor knobs (`max_results`,
  `max_output_bytes`, `timeout_seconds`, `max_scan_file_bytes`, `max_entries`)
  and the `TRUNCATION_MARKER` exports go. `grep`'s `rg` run streams stdout,
  observes `ToolContext.cancel_event`, and has no timeout (Pi has none). The
  shared glob translator lives in a new `tools/glob_match.py`.
- Empty results use Pi's texts: `No matches found`, `No files found matching
  pattern`, `(empty directory)`.

### Consumers of the old contract

- `session.py` `NATIVE_TOOL_LOOP_SYSTEM_PROMPT`: the `ls`/`grep`/`find` lines
  take Pi's snippets (`List directory contents`, `Search file contents for
  patterns (respects .gitignore)`, `Find files by glob pattern (respects
  .gitignore)`) and the "grep skips binary and secret-looking files" clause
  goes. `session.py` is at the architecture line ceiling; the line count stays
  equal.
- `tool_renderers.py`: the plain header renders `ls` for a call without
  `path` (today it falls back to an argument dump).
- `scripts/parity_score.sh` B9 names `tests/test_bash_tool.py::test_bounds_large_output`;
  the test keeps its name.
- `scripts/parity_checks/bash_behavior.py` asserts `exit code: 3`; unchanged
  because the framing stays.
- Tests: `tests/test_native_tools_{grep,find,ls,truncate}.py`,
  `tests/test_bash_tool.py`, `tests/test_coding_session_symlink_defense.py`
  (error texts unchanged), `tests/test_native_daily_use_baseline.py`.
- Docs: `CHANGELOG.md` `[Unreleased]`, `docs/pi-parity.md` (shell and
  cross-repo rows), `docs/backlog.md` (TOOLS1 to Done, TOOLS2 follow-on).

## Done when

1. Unit tests pin, per tool: the exact schema and description, the default and
   explicit `limit`, every notice string (including the `limit=2N` hint and
   `50.0KB limit reached`), and the empty-result text.
2. `truncate_tail` tests cover no-op, line limit, byte limit, partial last line
   with a multi-byte boundary, and `truncate_line`.
3. `bash` tests: >2000 lines gives the `[Showing lines …. Full output: <path>]`
   notice, the temp file holds the full output; a single huge line gives the
   partial-line notice; small output creates no temp file; `!` keeps 16 KB.
4. `grep` tests run both engines (rg when present, forced fallback): regex,
   `literal`, `ignoreCase`, `glob` (case-sensitive on both engines, with a
   mixed-case file name; a negated `!*.txt` glob; a `/` glob with a nested
   `path`, based on the root;
   a file `path` ignoring the glob), `context` (`-N-` lines), long-line
   truncation, the limit kill, the `.git` filter counted before the limit, a
   file path giving a basename label, and an invalid regex error.
5. `find` tests: basename matching at depth, full-path patterns with the `**/`
   prefix, `*` not crossing `/`, smart case, braces, trailing `/` on
   directories, `limit` notice, `limit=0`.
6. `ls` tests: default path, `name/` suffix, dotfiles, case-insensitive order,
   `limit` notice, broken symlink skipped.
7. A real run through a local stub provider in an isolated config shows the
   model receiving a `limit=` notice and a bash temp-file notice and acting on
   them (the next call uses `limit=2N`; the temp file is paged with `bash`,
   since `read` refuses the temp directory until READ2).
8. `just check` green; `bash_behavior.py` and the tool-related parity checks
   green; different-family review CLEAN.
