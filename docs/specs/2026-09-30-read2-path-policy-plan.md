# READ2: Pi path resolution for read, ls, grep, find, `@file` and `@image:`

Status: plan (READ2 in `docs/backlog.md` follow-ons).
Pi reference: `~/src/pi-mono` at `1b347794e`,
`packages/coding-agent/src/core/tools/{path-utils,read,ls,grep,find,write,edit}.ts`,
`packages/coding-agent/src/utils/paths.ts`, `src/cli/file-processor.ts`.

## What Pi does

- `resolveToCwd(path, cwd)` (`path-utils.ts`) = `resolvePath(path, cwd,
  {normalizeUnicodeSpaces: true, stripAtPrefix: true})` (`utils/paths.ts`):
  1. replace `[  -   　]` with a plain space;
  2. strip one leading `@`;
  3. expand `~` (exactly `~`) and `~/…` to the home directory (not `~user`);
  4. a `file://` URL becomes its path (`fileURLToPath`);
  5. absolute → `path.resolve(p)`, else `path.resolve(cwd, p)`. This is
     lexical normalization: `..` collapses, symlinks are not resolved, no
     containment check.
- `resolveReadPath(path, cwd)` (read tool, CLI `@file` args in
  `cli/file-processor.ts`): `resolveToCwd`, then if the result does not exist
  try, in order, the macOS screenshot variant (` AM.`/` PM.` →
  ` AM.`/` PM.`, case-insensitive), the NFD form, the curly-quote
  form (`'` → `’`), and NFD + curly quote; the first that exists wins,
  otherwise the plain resolved path.
- `read` uses `resolveReadPath`; `ls`, `grep`, `find`, `write`, `edit` use
  `resolveToCwd`. None has a deny list, root containment, suffix or
  generated-directory check.
- `ls` lists every entry `readdir` returns (dotfiles included); an entry
  whose `stat` fails is skipped.
- `grep` runs `rg --json --line-number --color=never --hidden [...] --
  pattern <abs searchPath>` in Pi's process cwd. `find` runs `fd --glob
  --color=never --hidden [--no-require-git] --max-results N [--full-path] --
  pattern <abs searchPath>`, adding `--no-require-git` only when no ancestor
  of the search path has a `.git`; a pattern containing `/` gets
  `--full-path` and a `**/` prefix unless it starts with `/`, `**/` or is
  `**`. Neither tool filters results further.

### rg / fd ignore rules, measured (rg 15.2.0, fd 10.5.0, scratch trees)

| Case | rg `--hidden` | fd `--hidden` (Pi flags) |
| --- | --- | --- |
| `.git/` contents | searched | listed |
| hidden files/dirs | included | included |
| `.gitignore` inside a repo, nested and in ancestors up to the repo root | applied | applied |
| `.gitignore` above the repo root | not applied | not applied |
| `.gitignore` outside any repo | not applied | applied (`--no-require-git`), ancestors too |
| `.ignore` (any ancestor, in or out of a repo) | applied | applied |
| `.rgignore` | applied | not read |
| `.fdignore` | not read | applied |
| `.git/info/exclude` | applied | applied |
| explicit file as search path, ignored | searched | n/a |
| absolute glob pattern `/abs/dir/*.txt` (`--full-path`) | n/a | matches |
| pattern with `..` | n/a | matches nothing |

Precedence (the `ignore` crate): the tool-specific file (`.rgignore` /
`.fdignore`) beats `.ignore`, which beats `.gitignore`, which beats
`.git/info/exclude` and the global git excludes; within a class, the deepest
directory's last matching line wins.

## Security boundary check

No documented boundary requires the read-side policy. The model-visible
`bash` tool is a real shell in the workspace (`tools/bash.py`, Pi parity), so
it can already read any path the read tools refuse. The only documented
boundary is the allowlisted argv sandbox in `command_sandbox.py` (see its
module docstring and the sandbox allowlist history), used by
`verification.py` and RPC `bash`. That path keeps `resolve_tool_path` and
`_is_ignored_or_generated` unchanged. Everything the model-visible read tools
and the prompt references do moves to Pi's resolver.

## Changes

1. New `tools/path_utils.py`, a port of Pi `path-utils.ts` plus the parts of
   `utils/paths.ts` it uses: `normalize_path`, `resolve_to_cwd(path, cwd) ->
   Path` and `resolve_read_path(path, cwd, *, exists=os.path.exists) -> Path`
   (the variant order above; `exists` is injectable so tests can prove the
   NFD / curly-quote fallbacks on case- and normalization-insensitive APFS).
   Lexical normalization uses `posixpath.normpath` on the joined absolute
   path (a leading `//` collapsed to `/`, like Node `path.resolve`).
2. `read`: `resolve_read_path(path, context.workspace_root)`; drop
   `resolve_tool_path` and `_is_ignored_or_generated`. Everything after the
   resolution (exists / regular file checks, image error, offset/limit,
   truncation, notices) is unchanged. A non-string `path` still raises
   `ToolArgumentError`.
3. `ls`: `resolve_to_cwd(path or ".", cwd)`; list every entry; skip only
   entries whose stat fails (Pi). Drop both ignore checks and the
   symlink-target relabel. Error texts stay pipy's.
4. `grep`: `resolve_to_cwd`; drop the search-root refusal and the per-match
   `kept` filter; `rg` runs with `cwd=workspace_root` (Pi's process cwd, so a
   slash glob is relative to it as before). Fallback: walk with the new
   ignore walker (item 6) in rg mode; its slash-glob base stays the workspace.
   The `glob` is an rg override and is checked before the ignore rules
   (measured, rg 15.2.0, `.gitignore` = `ignored.txt`, `logs/`, `buildx/`):
   - a file matching a positive glob is searched even when ignored
     (`--glob '*.txt'` finds `ignored.txt`);
   - a file not matching a positive glob is skipped;
   - a directory matching a positive glob is entered even when ignored
     (`buildx*` enters `buildx/`, whose `b.txt` then fails the glob);
   - a directory not matching falls through to the ignore rules (`logs/**`
     does not enter the ignored `logs/`);
   - an entry matching a `!` glob is skipped (a directory is pruned); with
     only a `!` glob, other entries fall through to the ignore rules.
   Tests cover these rows for rg and the fallback.
5. `find`: `resolve_to_cwd`; walk with the ignore walker in fd mode. Accept
   patterns starting with `/` (matched against the absolute path, no `**/`
   prefix, like Pi), containing `..` (they match nothing) and containing `\`
   (a glob escape, as in globset). Output rows unchanged.
6. New `tools/ignore_walk.py`: a per-directory, immutable rule state for a
   walk from a search path, with its own line compiler,
   `compile_gitignore_line`, that ports the `ignore` crate's
   `gitignore.rs` line handling onto globset rules
   (`tools/glob_match.py`, already used for fd/rg globs): `#` comment,
   trailing spaces trimmed unless escaped, `!` negation and `\!`/`\#`
   escapes, a leading `/` anchors (so `/foo` does not match `nested/foo`),
   a trailing `/` is directory-only, a line with no other `/` gets `**/`,
   a trailing `/**` becomes `/**/*`, compiled with `literal_separator`
   and backslash escapes, so `{a,b}` alternation and `[...]` classes match
   like rg and fd (`*.{log,tmp}` excludes `trace.log` and `trace.tmp`).
   `ignore_rules.py` (skill discovery, node `ignore` semantics after Pi's
   prefixing) is untouched. Tests pin anchored file/directory lines,
   alternation, classes, negation and `/**`, each checked against rg and fd
   on the same tree where the binaries are installed.
   - rules are stored with their base directory and tested against the
     entry's path relative to that base, so a non-anchored line in `a/b/`
     matches at any depth below `a/b/` (gitignore semantics);
   - three classes checked in precedence order: tool file (`.rgignore` or
     `.fdignore`), `.ignore`, git (`.git/info/exclude` first, then
     `.gitignore` files); within a class the last matching rule wins; the
     first class with a match decides;
   - initial state: ancestors of the search path from `/` down; the nearest
     ancestor (or the search path) holding `.git` is the repo root, which
     resets the git class and adds its `info/exclude`; `.gitignore` files
     are read inside a repo, and outside one only in fd mode;
   - `info/exclude` lives in the common git directory: for a `.git`
     directory that is `.git/info/exclude`; for a `.git` file (linked
     worktree, submodule) the `gitdir:` target, then its `commondir` file
     (relative to the gitdir) when present. Measured: rg and fd honor the
     main repository's `info/exclude` inside a linked worktree. A test uses
     a real `git worktree add`;
   - entering a directory that holds `.git` starts a nested repo: a
     git-aware walk (rg always; fd when the search path is inside a repo)
     resets the git class there; fd's `--no-require-git` walk (search path
     outside any repo) keeps the ancestor `.gitignore` rules across the
     boundary and only adds the nested repo's `info/exclude` (measured, fd
     10.5.0: an outside-repo root `.gitignore` `*.txt` still excludes
     `nested/hidden.txt` under `nested/.git`). A differential test pins it;
   - hidden entries and `.git` itself are walked; symlinks are listed but
     not followed (unchanged); an entry that matches is pruned.
   The global git excludes file (`core.excludesFile`, default
   `$XDG_CONFIG_HOME/git/ignore`) is not read: a documented deviation.
7. `@file` (`file_references.py`) already reads through `ReadTool`, so it
   follows `read` (Pi CLI `@file` uses `resolveReadPath`). Drop its
   `reference_roots` parameter.
8. `@image:` (`image_attachment.py`): `resolve_read_path`; drop
   `_is_blocked_path`, the `ignored` reason and `reference_roots`. Size caps,
   magic-byte type check and the per-turn limit stay (image handling is
   READ-IMG, not this slice).
9. Retire `--read-root` (no-deprecation policy: the flag has no remaining
   effect once read tools read any path). Remove the CLI flag, the
   `PIPY_READ_ROOTS` env var, the `AGENTS.md`/docs `~/` auto-discovery, the
   pipy-only `Reference roots (read-only, absolute paths):` system-prompt
   block, the skill-directory root widening, `ToolContext.reference_roots`,
   `CodingSession.reference_roots`, the adapter/wiring/loop-scope plumbing
   (`file_reference_roots`, `image_reference_roots`; a pasted clipboard image
   in the temp dir is read by absolute path now). `CommandPolicy.reference_roots`
   (command sandbox) is untouched.
10. The bash full-output temp file (`pipy-bash-*.log` in the system temp dir,
    TOOLS1) is readable by `read` as a consequence; a test pins it.

`write` and `edit` are unchanged in this slice. Pi's use `resolveToCwd` with
no deny list, and Pi's `write` overwrites and creates parent directories while
pipy's is create-only, workspace-relative and byte-capped. That is a larger
mutation-tool parity gap and is reported as a follow-on, not folded in here.

## Tests (done-when)

- `path_utils`: `~`, `~/x`, `@` prefix, unicode spaces, `file://`,
  absolute outside cwd, `..` collapsing, AM/PM, NFD, curly quote,
  NFD + curly (with an injected `exists`), and a real narrow-NBSP file on
  disk.
- `read`: absolute file outside cwd, `~/file` (HOME isolated), `.git/HEAD`,
  a `.gitignore`d file, `node_modules/x.js`, `yarn.lock`, `../sibling`, a
  macOS screenshot-name variant; the bash temp file named in a truncated
  `bash` result.
- `ls`: absolute dir outside cwd, `~`, lists `.git/`, gitignored and
  generated entries; `.git` itself as path.
- `grep` (rg and fallback): absolute dir outside cwd, `~`, `.git/HEAD` as a
  path, `.git` contents found under `--hidden`, gitignored file skipped in a
  directory walk but searched when named, `.ignore`/`.rgignore`, parent
  `.gitignore` inside a repo, no `.gitignore` outside a repo.
- `find`: absolute dir outside cwd, `~`, `.git/` entries listed, gitignored
  entries skipped (in repo and outside with `--no-require-git`), `.fdignore`,
  nested `.gitignore` non-anchored line, `info/exclude`, absolute pattern,
  `..` pattern → no results.
- `@file` / `@image:` outside the workspace and in `.git`/gitignored paths.
- Existing tests asserting the old refusals are rewritten to the Pi
  behavior (not deleted); `--read-root` tests are removed with the flag.
- `just check`, every `scripts/parity_checks/*`, `scripts/parity_score.sh`.
- Live: openai-codex `gpt-6.1-sol`, isolated config and session dirs, a
  truncated `bash` result whose temp-file path the model then reads with
  `read`.

## Docs

`docs/pi-parity.md` (read, `@file`, cross-repo row, `@image:`),
`docs/usage.md` (`--read-root` row), `docs/harness-spec.md` references,
`docs/customization.md` / `docs/settings-config.md` (skill roots),
`docs/parity-plan.md` Decision 3 row, `docs/architecture.md`, `docs/sdk.md`,
tool module docstrings, `CHANGELOG.md` `[Unreleased]`, `docs/backlog.md`
(READ2 struck to Done; the TOOLS2 "temp file sits outside `read`'s path
policy until READ2" sentence dropped).
