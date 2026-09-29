# READ1: Pi `read` tool (offset/limit, continuation notices)

Status: plan, 2026-09-29. Source: DF1-F1 in
[the DF1 acceptance note](../acceptance/2026-09-29-df1-dogfooding.md) and the
READ1 follow-on in `docs/backlog.md`.

## Problem

pipy's `read` (`src/pipy_harness/native/tools/read.py`) returns at most 200
lines / 8 KB, takes only `path`, and says nothing when it cuts. On a 901-line
CSV the model concluded row 198 was the last row, and automatic compaction kept
that wrong fact. It also refuses files over 256 KB, non-UTF-8 files, files with
control characters or NUL bytes, and files with secret-shaped content.

## Pi reference (`~/src/pi-mono` @ `4df157433`)

`packages/coding-agent/src/core/tools/read.ts` and `truncate.ts`.

Schema (`readSchema`), all fields as Pi declares them:

| Field | Type | Optional | Description (exact) |
| --- | --- | --- | --- |
| `path` | string | required | `Path to the file to read (relative or absolute)` |
| `offset` | number | optional | `Line number to start reading from (1-indexed)` |
| `limit` | number | optional | `Maximum number of lines to read` |

Tool description (exact, with `DEFAULT_MAX_LINES = 2000`,
`DEFAULT_MAX_BYTES = 50 * 1024`):

> Read the contents of a file. Supports text files and images (jpg, png, gif,
> webp, bmp). Images are sent as attachments. For text files, output is
> truncated to 2000 lines or 50KB (whichever is hit first). Use offset/limit
> for large files. When you need the full file, continue with offset until
> complete.

Text path, in order:

1. Read the whole file; decode as UTF-8 (invalid bytes become U+FFFD; no size
   cap, no binary/control/secret refusal).
2. `allLines = text.split("\n")`; `totalFileLines = allLines.length` (a
   trailing newline counts one extra empty element; `\r` is kept).
3. `startLine = offset ? max(0, offset - 1) : 0`; `startLineDisplay =
   startLine + 1`. If `startLine >= allLines.length` the tool fails with
   `Offset ${offset} is beyond end of file (${allLines.length} lines total)`.
4. If `limit !== undefined`: `endLine = min(startLine + limit, len)`,
   `selected = allLines.slice(startLine, endLine).join("\n")`,
   `userLimitedLines = endLine - startLine`. Otherwise `selected =
   allLines.slice(startLine).join("\n")`.
5. `truncateHead(selected)` with 2000 lines / 50 KB: counting lines drops a
   trailing empty element; no truncation when both limits hold; if the first
   line alone exceeds the byte limit, empty content with
   `firstLineExceedsLimit`; otherwise keep whole lines while
   `bytes + (i > 0 ? 1 : 0)` fits, `truncatedBy` `lines` or `bytes`. Never a
   partial line.
6. Output:
   - first line too large: `[Line ${startLineDisplay} is ${formatSize(lineBytes)}, exceeds ${formatSize(50KB)} limit. Use bash: sed -n '${startLineDisplay}p' ${path} | head -c 51200]`
   - truncated by lines: `content + "\n\n[Showing lines ${start}-${end} of ${totalFileLines}. Use offset=${end+1} to continue.]"`
   - truncated by bytes: `content + "\n\n[Showing lines ${start}-${end} of ${totalFileLines} (50.0KB limit). Use offset=${end+1} to continue.]"`
   - user limit stopped early (`startLine + userLimitedLines < len`):
     `content + "\n\n[${len - (startLine + userLimitedLines)} more lines in file. Use offset=${startLine + userLimitedLines + 1} to continue.]"`
   - otherwise the content unchanged.
   `formatSize`: `<1024` → `${n}B`; `<1 MiB` → `${(n/1024).toFixed(1)}KB`;
   else `${(n/MiB).toFixed(1)}MB`. `toFixed` rounds an exact tie up
   (1280 B → `1.3KB`), unlike Python's `f"{x:.1f}"` (`1.2KB`); both quotients
   are exact in binary, so the port uses `Decimal` with `ROUND_HALF_UP` and a
   test pins 1280 B → `1.3KB`.

Pi also exports `promptSnippet: "Read file contents"` and the guideline
`Use read to examine files instead of cat or sed.`; pipy's tool-loop system
prompt is not Pi's builder (separate gap), so only its `read` line is updated.

## pipy change

1. New `src/pipy_harness/native/tools/truncate.py`: stdlib port of Pi's
   `DEFAULT_MAX_LINES`, `DEFAULT_MAX_BYTES`, `TruncationResult`,
   `truncate_head`, and `format_size` (only what `read` needs; `truncateTail`,
   `truncateLine`, `truncateMiddle` arrive with the tools that use them).
2. `ReadTool`:
   - schema: `path` (string), `offset`, `limit` with Pi's descriptions;
     `required: ["path"]`. pipy's schema subset has no `number`, so `offset`
     and `limit` are `integer` (the existing bash `timeout` does the same); no
     `minimum`, because Pi has none. `additionalProperties: false` stays, as
     on every pipy tool.
   - description: Pi's text, except the image sentence, which must describe
     what pipy does (see images below). The description is:
     `Read the contents of a file. Supports text files; images (jpg, png, gif,
     webp, bmp) are not supported yet and return an error. For text files,
     output is truncated to 2000 lines or 50KB (whichever is hit first). Use
     offset/limit for large files. When you need the full file, continue with
     offset until complete.`
   - body: a line-for-line port of steps 1–6. Offset/limit arithmetic keeps
     JS semantics (Python slicing matches `Array.prototype.slice` for negative
     ends), so zero/negative values behave as in Pi.
   - removed: the 200-line / 8 KB excerpt, the `byte_limit`/`line_limit`
     constructor fields, the 256 KB file cap, the NUL/control-character,
     non-UTF-8 and secret-shaped refusals.
   - images: detection is a port of Pi's `detectSupportedImageMimeType`
     (`utils/mime.ts`, first 4100 bytes): JPEG (not `ff d8 ff f7`), PNG with an
     `IHDR` and no `acTL` (animated PNG is not an image to Pi and reads as
     text), GIF87a/89a, RIFF/WEBP, and BMP with Pi's header checks. pipy tool
     results are text-only (`ToolExecutionResult`), so a detected image
     returns `read error: image files are not supported by read yet
     (<mime>). Ask the user to attach it with @image:<path>.` instead of
     decoding the bytes as text; the model cannot attach it itself, so the
     text tells it to ask the user. `@image:` does not take BMP, so a BMP
     returns only `read error: image files are not supported by read yet
     (image/bmp).` Sending images from `read` is follow-on
     READ-IMG (image content in tool results across every provider adapter,
     session tree and replay).
   - errors keep pipy's `read error: …` prefix; the offset error uses Pi's
     message text after it.
3. Deferred divergence (scope, not safety): path resolution stays on pipy's
   shared `resolve_tool_path` (workspace plus `--read-root` roots, `.git` and
   `.gitignore` refusal). Pi's `resolveReadPath` reads any path, expands `~`,
   strips a leading `@`, normalizes unicode spaces and tries the macOS
   screenshot/NFD/curly-quote variants. No security boundary requires pipy's
   restriction (`bash` already reads anything), so it is a divergence to
   remove, not to keep. It is not in READ1 because it flips a separately
   documented contract shared by `ls`/`grep`/`find`, `@file`, `@image:` and
   the symlink-defense tests, and READ1's defect is the silent truncation.
   It is queued as follow-on READ2, first after READ1, in `docs/backlog.md`.
4. `@file` references keep calling `ReadTool`, so they now load up to 2000
   lines / 50 KB with Pi's continuation notice, and secret-shaped files load
   (Pi's `@file` arguments read files unfiltered too). The 64 KB `@file`
   context budget is unchanged.
5. Tool-row header: `ToolLoopRenderer._read_range_label`
   (`src/pipy_harness/native/tool_renderers.py`) assumes a 0-indexed offset
   and a default `:1-200` range. Port Pi's `formatReadLineRange`
   (`core/tools/renderers/read.ts`): no range when both `offset` and `limit`
   are absent or null; otherwise `start = offset ?? 1`, and `-end` with
   `end = start + limit - 1` only when `limit` is present and `end` is
   truthy. Its tests move with it.
6. System prompt: `read: read a bounded UTF-8 file excerpt` becomes Pi's
   snippet `read: Read file contents`, and `read` is dropped from the sentence
   claiming the readers filter binary, control-character and secret-looking
   content.

## Other tools (checked, not changed here)

None is silent: each appends a marker. They differ from Pi in schema, limits
and notice text, and each is its own port:

- `bash`: keeps the last 16 KB (configurable up to 60 KB) and appends
  `(output truncated)`. Pi keeps the
  last 2000 lines / 50 KB, saves the full output to a temp file and appends
  `[Showing lines X-Y of N. Full output: <path>]`.
- `grep`: literal only, 100 results, `... (truncated)`. Pi takes
  `pattern/path/glob/ignoreCase/literal/context/limit`, regex, 500-char lines,
  and appends `[N matches limit reached. Use limit=2N for more, or refine pattern]`.
- `find`: 200 results, `... (truncated)`. Pi: `limit` (default 1000),
  50 KB cap, `[N results limit reached. Use limit=2N for more, or refine pattern]`.
- `ls`: 200 entries, `... (truncated)`, `path` required. Pi: optional `path`,
  `limit` (default 500), `[N entries limit reached. Use limit=2N for more]`.

These become follow-on TOOLS1 in `docs/backlog.md`.

## Tests (done-when)

- schema property descriptions equal Pi's; the description equals the text
  pinned above; `offset`/`limit` optional.
- a 901-line file: the default read returns all lines without a notice; a
  2500-line file returns lines 1–2000 with
  `[Showing lines 1-2000 of 2501. Use offset=2001 to continue.]` (trailing
  newline counted as Pi does), and `offset=2001` returns the rest with no
  notice.
- byte truncation notice with `(50.0KB limit)`; first-line-too-large bash hint.
- `limit` stopping early gives `[N more lines in file. Use offset=M to continue.]`.
- offset beyond EOF error text; offset 0 and a negative offset read from
  line 1; `limit=0` returns empty content plus the `more lines` notice;
  a negative `limit` follows JS slice arithmetic (drops lines from the end,
  and the notice numbers match Pi's formula).
- renderer header: `read a.txt` without range, `:5-14` for offset 5 limit
  10, `:5` for offset only, `:1-10` for limit only.
- `truncate_head` and `format_size` unit tests against Pi's rules (trailing
  newline, byte vs line, never partial lines).
- large (>256 KB), secret-shaped, non-UTF-8 and control-character files now
  read; PNG, JPEG, GIF, WebP and BMP files return the image error; an
  animated PNG and a `ff d8 ff f7` file read as text, as in Pi.
- `@file`: a formerly refused (secret-shaped, >256 KB) file now loads; a
  file over 2000 lines loads 2000 lines plus the continuation notice inside
  the 64 KB budget; two such references exceed the budget and the second is
  reported over budget.
- existing path-policy tests keep passing.
- live: an isolated `--mode json` run on openai-codex `gpt-6-sol` asks for the
  last row of a ~900-line CSV larger than 50 KB (so the first read is cut by
  the byte limit and the model must page with `offset`). Passing requires
  the JSON event stream to show a `read` result ending in the
  `(50.0KB limit). Use offset=N to continue.]` notice, a later `read` call
  with an `offset` past the lines shown, no `bash` call reading the file,
  and the correct answer.

## Result (live, 2026-09-29)

openai-codex `gpt-6-sol` at medium, `--mode json`, isolated config and
session state, a 901-line / 68 KB `orders.csv`: `find` → `read`
(`offset=1, limit=2000`) returned lines 1–675 with
`[Showing lines 1-675 of 902 (50.0KB limit). Use offset=676 to continue.]`
→ `read` (`offset=875, limit=100`) using the total from the notice → answer
`00900,Fnjmkhnhbc`, the true last row. No `bash` call.
- `just check` green; docs (`docs/backlog.md`, `CHANGELOG.md`,
  `docs/pi-parity.md`, `docs/harness-spec.md`) updated.

## Implementation order

1. `tools/truncate.py` + `tests/test_native_tools_truncate.py` (truncate_head
   cases, format_size tie rounding). Accept: tests pass.
2. `tools/image_mime.py` port of `detectSupportedImageMimeType` + tests.
   Accept: each type detected, APNG and `ff d8 ff f7` not.
3. `ReadTool` rewrite + `tests/test_native_tools_read.py` rewritten to the
   Pi contract (schema, notices, offset/limit edge cases, removed refusals,
   image errors, path policy unchanged). Accept: tests pass.
4. `@file` tests for the new behaviour. Accept: tests pass.
5. Renderer range label + tests. Accept: tests pass.
6. System prompt line; docs (`CHANGELOG.md`, `docs/backlog.md`,
   `docs/pi-parity.md`, `docs/harness-spec.md`). Accept: `just check` green.
7. Live `--mode json` check on openai-codex `gpt-6-sol`.
