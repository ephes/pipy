# Audit: Session Storage

Scope:
- `src/pipy_session/recorder.py` (308 L)
- `src/pipy_session/catalog.py` (1,179 L)
- `src/pipy_session/cli.py` (683 L)
- `src/pipy_session/export.py` (282 L)
- `src/pipy_session/auto_capture.py` (739 L)
- `src/pipy_session/__init__.py`

Comparison (pi-mono):
- `packages/agent/src/harness/session/jsonl-storage.ts` (293 L)
- `packages/agent/src/harness/session/jsonl-repo.ts` (177 L)
- `packages/agent/src/harness/session/session.ts` (252 L)
- `packages/agent/src/harness/session/memory-storage.ts` (131 L)
- `packages/coding-agent/src/core/session-manager.ts` (1,466 L) — session orchestration only, not storage

## Summary

The recorder gets the lifecycle close to "bad state impossible" — finalize is a `rename` from `.in-progress/pipy/` to `pipy/YYYY/MM/`, and `resolve_active_path` rejects any path outside the active directory — but the design then leaks back into AI-slop territory the moment we leave `recorder.py`. The 1,179-line `catalog.py` mixes five different surfaces (list, search, inspect, verify, reflect) into one file, two of those surfaces (`verify`, `reflect`) are never called by the production harness and exist only to look like a finished product, and the readers swallow every conceivable I/O / decode error with permissive `except (OSError, UnicodeError, json.JSONDecodeError)` blocks instead of trusting `finalize_session`'s own invariants. `auto_capture.py` (739 L) is similarly speculative: aside from `start`/`stop`/`event` (used internally by the wrap helper) and the Claude `SessionStart`/`SessionEnd` hook, the entire `pi.session_reference` adapter, the `prune` machinery, and the model-from-argv parser exist with no production caller. The recorder protects against the worst leak (no payload/prompt/tool bodies live anywhere in `_initial_session_fields`/`_RecorderEventSink`), and `export.py` further allowlists `SAFE_EVENT_KEYS` — but `inspect_finalized_session` reads the raw Markdown summary into memory and `search_finalized_sessions` scans every event's `summary` field, so the privacy contract relies on every event-emitting caller correctly putting only safe text in `summary`/markdown, which is invariant-by-convention, not by-construction. Net: the recorder is clean; the catalog/auto-capture layer is the AI-slop swamp the user described.

## Findings

### F1: `recorder.finalize_session` recovery branch is the canonical "handle the bad state" anti-pattern

- **Where**: `src/pipy_session/recorder.py:203-221`
- **Symptom**: After staging a `.md.partial`, the code calls `active_path.rename(final_jsonl)` then `temp_markdown.rename(final_markdown)` inside a `try / except Exception: temp_markdown.unlink(); raise`. The only ways for the second rename to fail after the first succeeded are (a) the parent dir was deleted between the two renames, (b) a filesystem-level race, or (c) something hit `KeyboardInterrupt`. The cleanup branch papers over the resulting half-finalized state instead of making it impossible. If the JSONL rename succeeds but the Markdown rename fails and the cleanup also fails (very plausible in the same `except Exception:`), the archive is left with a finalized JSONL and no summary — and `verify_session_archive` later flags it as "orphan-summary" / no summary at all.
- **Article principle**: "The correct fix is not to handle the bad state, but to make the bad state impossible." Finalization should be a single atomic step.
- **Pi comparison**: `JsonlSessionStorage` (pi-mono) never moves a session file mid-life; the file lives at one path and the worst case is a half-flushed last line, which is fine because pi-mono treats incomplete lines as the natural truncation point. There is no "active → finalized" hand-off to get wrong.
- **Suggested fix**: Either (a) write the markdown summary into the active directory next to the JSONL so a single rename of a directory or two atomic same-dir renames are gated on both files already existing, or (b) write the JSONL with markdown sibling fully present in `.in-progress/pipy/` first, then do exactly one rename per file in a defined order with the markdown rename done first (so a crash leaves an orphan markdown, which is harmless and explicit), then the JSONL rename last (which is the visible commit point). Drop the `except Exception:` recovery.
- **Severity**: medium

### F2: `recorder.append_event` has no concept of "this record is finalized"

- **Where**: `src/pipy_session/recorder.py:126-163` (append) vs `:226-259` (resolve_active_path)
- **Symptom**: `append_event` calls `resolve_active_path`, which only refuses paths outside `<root>/.in-progress/pipy/`. There is no in-band guard that prevents a caller from passing a finalized path that happens to live under the wrong root, no exclusive file lock during append, and no `O_APPEND` enforcement — `handle.open("a")` is used but two parallel `append_event` calls on POSIX with non-atomic interleaving could still corrupt a line.
- **Article principle**: The "bad state impossible" rule applies: a record that has been moved to the archive should not be reachable through any `append`/`finalize` path; currently the rule is "we put it in a different directory, and we trust that".
- **Pi comparison**: `JsonlSessionStorage.appendEntry` uses `fs.appendFile`, which on the supplied node FS uses a single `writeFile` call (atomic enough for short JSON lines on POSIX). pi-mono also keeps an in-memory `byId` map and uses uuidv7 keys so duplicate appends are recoverable — pipy doesn't track sequence in the recorder itself; sequence is owned by the harness `_RecorderEventSink`.
- **Suggested fix**: Use `os.open(..., O_APPEND | O_WRONLY)` for the append (POSIX guarantees atomic appends ≤ `PIPE_BUF` for `write(2)` calls), or take a `fcntl.flock` for the duration of the write. Add an explicit `_assert_active(path)` that re-checks the active directory invariant just before the write rather than only at `resolve_active_path`. Consider passing only the active-record handle through a context manager so finalization can revoke writes by-construction.
- **Severity**: medium

### F3: `_unique_path` mangles the canonical filename and breaks `FILENAME_RE` round-trip

- **Where**: `src/pipy_session/recorder.py:286-296` (`_unique_path`), `:78` (`basename` includes slug), `:17-22` (`FILENAME_RE`)
- **Symptom**: When two sessions are created in the same second (timestamp truncated to `HHMMSSZ`), `_unique_path` appends `-2`, `-3`, … to the stem: `…-codex-<slug>-2.jsonl`. The inline comment admits the `-2` "becomes part of the slug component parsed during finalize". This means the slug field reported back from `_read_finalized_listing` and stored in the listing is not the slug the caller asked for. Worse, on a tight loop a sequence of timestamp-identical inits will produce `slug`, `slug-2`, `slug-3`, … which deduplicates the *records* but not the *slug semantics*.
- **Article principle**: Plausible-but-wrong implementation. The "uniqueness" is generated by silently mutating a structural field, which then propagates to listings, search, reflection, etc.
- **Pi comparison**: pi-mono uses uuidv7-derived session IDs (`jsonl-storage.ts:35-41` + `uuid.ts`) so the disk filename is keyed by an opaque id, and slugs/names live as separate `session_info`/`label` entries appended later. The session id is unique by construction, not by directory probing.
- **Suggested fix**: Include the run UUID (harness already generates it in `_initial_session_fields`) or millisecond precision in the filename stamp so collisions are impossible without re-using the same identifier. If we keep the current scheme, store the `-N` disambiguator as a distinct filename component (e.g., `…Z-<machine>-<agent>-<slug>--<n>.jsonl`) parsed out separately by `FILENAME_RE`.
- **Severity**: medium

### F4: `catalog.py` is five modules in a trench coat

- **Where**: `src/pipy_session/catalog.py:1-1180`
- **Symptom**: One file holds (1) `FinalizedSessionListing` + listing logic, (2) `FinalizedSessionSearchMatch` + search, (3) `FinalizedSessionInspection` + inspect, (4) `SessionArchiveVerification` + verify, (5) `SessionReflection` + reflect, plus seven formatter functions and ~20 private helpers. The catalog re-implements its own filename-stamp helper (`_filename_stamp`, line 1177) that duplicates `recorder._filename_stamp`. Each `FinalizedSession*` dataclass has ~10 lines of `@property` boilerplate forwarding to `listing.*` (lines 56-91 and 132-165) instead of using inheritance/`__getattr__`.
- **Article principle**: AI slop aesthetics: redundant abstractions, "look how complete this API is" surface area. Verify + reflect are not consumed by the harness at all (confirmed: only `verify_session_archive` is used as a test assertion; `reflect_on_finalized_sessions` has zero non-test callers).
- **Pi comparison**: pi-mono has no equivalent "reflect" or "verify-archive" surface. The closest is `findEntries`/`getEntries` in `session.ts` (252 L total for the whole `session/` directory).
- **Suggested fix**: Split into `catalog/listing.py`, `catalog/search.py`, `catalog/inspect.py`, and delete `verify_session_archive` + `reflect_on_finalized_sessions` (with their `REFLECTION_*` tables, `LOW_SIGNAL_EVENT_TYPES`, `_ReflectionJsonlSignals`, `_is_generic_auto_summary`, all the `format_session_reflection` Markdown formatting) unless a real consumer is found. If reflect is wanted as a CLI command for the user, keep it in `cli.py` and inline the implementation.
- **Severity**: high

### F5: `reflect_on_finalized_sessions` is a 60-line dead surface with a category registry

- **Where**: `src/pipy_session/catalog.py:284-303` (`REFLECTION_EVENT_CATEGORIES`), `:305-320` (`REFLECTION_CATEGORY_ORDER`), `:322-327` (`LOW_SIGNAL_EVENT_TYPES`), `:380-439` (`reflect_on_finalized_sessions`), `:464-525` (`format_session_reflection`)
- **Symptom**: 18 event types are mapped to 14 ordered categories. Production code never emits most of them (`workflow.role`/`subagent.used`/`workflow.evaluation`/`review.outcome` are only emitted by the `pipy-session workflow …` CLI subcommands, which themselves have no production callers — only test coverage). The reflection report distinguishes "low-signal partial sessions" from "real" ones using a hard-coded allow-set, which is the exact "registry with one entry" anti-pattern Ronacher flags. The only known invocation chain is `cli.py:378-384` → user typing `uv run pipy-session reflect`. The audit `tmux` logs in `docs/audit/2026-05-26/` show this being suggested by the AI itself, not used by the harness.
- **Article principle**: AI slop aesthetics: registries, ordered category lists, "this looks like a finished framework".
- **Pi comparison**: None — pi-mono has nothing remotely like this.
- **Suggested fix**: Delete `reflect_on_finalized_sessions`, the three module-level registries, `SessionReflection`, `SessionReflectionItem`, `format_session_reflection`, and the `reflect` CLI command. If the user genuinely wants archive-wide stats, a 20-line `pipy-session stats` that counts event types and prints a sorted table covers the legitimate use.
- **Severity**: high

### F6: `verify_session_archive` exists to let `pipy` brag in its own context but isn't reached by the runtime

- **Where**: `src/pipy_session/catalog.py:572-663` (`verify_session_archive`), `:1076-1129` (`_first_event_verification_issue`), `:1132-1169` (`_ambiguous_name_issues`)
- **Symptom**: The verifier walks the archive, validates filename regex, decodes the first JSON line, checks for orphan summaries, scans for `.partial` leftovers, and reports duplicate basename/stem. Five separate issue kinds (`archive-symlink`, `unexpected-archive-file`, `malformed-filename`, `orphan-summary`, `unsupported-archive-file`, `partial-file`, `unreadable-jsonl`, `malformed-jsonl`, `ambiguous-basename`, `ambiguous-stem`). The only non-test caller is `tests/test_native_approval_sandbox_policy.py`, which uses the *string label* `"pipy-session verify"` as part of a description fixture (`grep "verify_session_archive" src/pipy_harness` returns nothing). Several of the conditions are impossible by construction if `finalize_session` is the only writer (`malformed-filename` cannot happen because `finalize_session` rejects mismatched names; `archive-symlink` cannot happen because `os.rename` of a regular file produces a regular file; duplicate basenames are blocked by `if final_jsonl.exists(): raise FileExistsError`).
- **Article principle**: "Make the bad state impossible." If `finalize_session` is the only producer, four of the six error kinds can never appear except via external tampering, in which case the right place to detect them is at *read* time in the listing path, not in a separate verifier.
- **Pi comparison**: No equivalent. pi-mono assumes its own writer is correct.
- **Suggested fix**: Inline the only useful checks (orphan summary, `.partial` leftover from a crashed finalize) into `list_finalized_sessions` as warnings on the `FinalizedSessionListing`. Delete the rest.
- **Severity**: medium

### F7: Catalog readers swallow every error and silently return empty/None

- **Where**: `src/pipy_session/catalog.py:759-768` (`_read_finalized_listing`), `:842-843` (search), `:848-849` (markdown), `:895-896` (`_read_reflection_jsonl_signals`), `:983-984` (`_markdown_summary_snippet`), `:1044-1045` (`_safe_markdown_sibling`).
- **Symptom**: Every reader catches `(OSError, UnicodeError, json.JSONDecodeError)` and returns `None`, `[]`, or `""`. This means a corrupt finalized record disappears from `list`, `search`, and `reflect` without any indication to the caller. The verifier (F6) exists precisely to compensate for this hidden failure mode.
- **Article principle**: Permissive error handling smell. Each handler is a single try/except that converts every recoverable error into a silent skip — exactly the slop pattern the article names.
- **Pi comparison**: `loadJsonlStorage` (`jsonl-storage.ts:136`) throws `SessionError` on every malformation. There is no swallow-and-skip.
- **Suggested fix**: Decide one policy:
  - (a) Trust `finalize_session` and let reads raise. Records past finalization are immutable; any decode error is a real bug or external corruption.
  - (b) Surface a `FinalizedSessionListing` with a `status="corrupt"` field and let consumers (`list`, `inspect`, `search`) decide what to do. Either way, drop the silent `return None` pattern. Removing this also removes most of the rationale for F6.
- **Severity**: high

### F8: Privacy invariant for `event.summary` and Markdown is convention-only

- **Where**: `src/pipy_session/recorder.py:151-152` (append takes raw `summary`), `src/pipy_harness/runner.py:120-205` (sink passes summary strings via `sanitize_text`), `src/pipy_session/export.py:35-45` (`SAFE_EVENT_KEYS` includes `summary`), `src/pipy_session/catalog.py:832-841` (search scans `summary` and emits a snippet)
- **Symptom**: The pipy archive claims to expose no prompts/tool output/file contents. The recorder is invariant-correct for `payload` (export drops it; inspect counts events without printing payload), but `summary` is a free-form string carried into export and into search-snippet output. The only thing keeping a leak out is each call site remembering to call `sanitize_text` and never pasting raw model output into the summary. Two leak vectors:
  1. `inspect_finalized_session` reads the full Markdown summary via `read_text(encoding="utf-8")` (`catalog.py:560-563`) and `export_session` returns the raw `markdown_summary` string (`export.py:130-134`). If a caller wrote a markdown summary that included verbatim model output, the contract is broken — and there is nothing in `_markdown_text` (`recorder.py:274-276`) preventing that.
  2. `format_session_search_results` returns snippets up to 160 chars of any matching `event.summary` field. If a future emitter ever puts raw text into `summary`, it leaks via search.
- **Article principle**: Bad state should be impossible by construction. The contract "summary is metadata-safe" is currently maintained by convention across at least four files.
- **Pi comparison**: pi-mono explicitly stores message bodies (it is a transcript). It does not claim a privacy contract, so this is purely a pipy-introduced invariant.
- **Suggested fix**: Either (a) enforce a max-length cap and a regex denylist in `recorder.append_event` for `summary` (and reject markdown that contains code-fence sequences), or (b) treat `summary` and the markdown body as opaque text and stop including them verbatim in `export_session` — only expose their `len()` and `event_type_counts`. Document the contract explicitly in `recorder.py` and assert it once at the recorder boundary so all downstream readers can rely on it.
- **Severity**: high

### F9: `auto_capture.py` is mostly speculative surface

- **Where**: `src/pipy_session/auto_capture.py:1-739`
- **Symptom**: 739 lines. Inventory of what is actually called:
  - `start_auto_capture`, `stop_auto_capture`, `append_auto_event`: used internally by `run_wrapped_agent`, by `handle_claude_hook`, and by the `auto start|stop|event` CLI subcommands. Real but the CLI is the only entry point and it is fed by `claude hook` JSON.
  - `handle_claude_hook`: invoked only when the user has configured Claude Code project settings with the hook (README:634). No internal Python caller.
  - `run_wrapped_agent`: invoked only by `pipy-session wrap --` (CLI:480). No internal caller.
  - `reference_pi_session`: zero production callers. Only `test_auto_capture.py`, README, and `docs/session-storage.md` mention it (`auto reference-pi`).
  - `prune_auto_capture_state`: only the `auto prune` CLI subcommand calls it. The state-dir file is `.json` and uses `_write_state` with an `.partial` temp rename, which means the only thing prune cleans up is `.partial` leftovers from `_write_state` (which uses `with temp.open("x")` so the temp can only leak on crash) and stale entries whose active file has been moved or deleted.
  - `_public_model_from_argv`, `_arg_assigns_sensitive_value`, `_arg_requests_sensitive_value`, `_redacted_argv`, `_looks_sensitive`, `_sanitize_metadata`, `_sanitize_value`: duplicates of the same functions in `pipy_harness/capture.py`.
- **Article principle**: AI over-engineers edge-case handling. Adapter helpers for hypothetical platforms, a state-cleanup CLI for a state file that should not normally exist, and per-helper duplication of the sanitization library.
- **Pi comparison**: pi-mono has no equivalent layer because it owns its own runtime — there is no "wrap somebody else's CLI" pattern to model.
- **Suggested fix**: Delete `reference_pi_session` and `auto reference-pi` unless a concrete future user is named (the prompt notes it's already speculative). Move `_sanitize_metadata`/`_redacted_argv`/`_looks_sensitive` to `pipy_session._sanitize` (or import from `pipy_harness.capture`), eliminating ~80 lines of duplication. Fold `prune_auto_capture_state` into `start_auto_capture` (clean stale entries at start time), removing the `auto prune` subcommand. Drop `_public_model_from_argv`; the wrap helper already has access to the model via metadata.
- **Severity**: high

### F10: `auto_capture` exception handlers paper over real bugs

- **Where**: `src/pipy_session/auto_capture.py:450-466` (`_active_and_state_from_args`), `:482-504` (`_stale_state_reason`), `:507-536` (`_existing_live_state`)
- **Symptom**: `_active_and_state_from_args` catches `(ValueError, json.JSONDecodeError)` from `_read_state`, then `unlink()`s the state file silently — losing the active-record pointer. `_existing_live_state` catches `(OSError, ValueError, json.JSONDecodeError)` and returns `None` (caller then creates a new active record and orphans the existing one). These are bug-papering: a real production diagnosis is "state file got corrupted, find out why", not "delete it silently and pretend we never saw it."
- **Article principle**: Permissive error handling smell. The remedy here ("invalid auto-capture state removed") looks like it's the right thing — until you realize it deletes the only pointer to a partially-written active record.
- **Pi comparison**: pi-mono refuses to load a corrupt session (`SessionError("invalid_session")`); it does not silently rewrite or delete state.
- **Suggested fix**: Raise a typed `AutoCaptureStateError` instead of swallowing. The `prune` command becomes the *one* place that intentionally removes corrupt state, with a `--force` flag. Resume/start commands should refuse.
- **Severity**: medium

### F11: Workflow CLI subcommands are emit-only with no reader

- **Where**: `src/pipy_session/cli.py:153-216` (parser), `:523-650` (`_append_workflow_event`)
- **Symptom**: ~150 lines that emit `workflow.role`, `subagent.used`, `review.outcome`, `workflow.evaluation` events. The only readers are (a) `reflect_on_finalized_sessions` (F5, also dead) and (b) `search_finalized_sessions` (which would find them anyway via `summary` substring). The events have no callers in `pipy_harness` and no test outside the catalog/cli test files that exercise them in isolation.
- **Article principle**: AI slop aesthetics — comprehensive subcommands "for completeness" with no consumer.
- **Pi comparison**: None.
- **Suggested fix**: Remove the `workflow` subcommand and `REFLECTION_EVENT_CATEGORIES` mapping entries that target it. If there is a real future intent ("record review outcomes for later analysis"), let the harness emit those events programmatically and remove the per-event CLI surface.
- **Severity**: medium

### F12: `_read_finalized_inspection` and `_read_finalized_listing` parse the same header twice

- **Where**: `src/pipy_session/catalog.py:752-781` (`_read_finalized_listing`), `:999-1036` (`_read_finalized_inspection`), `src/pipy_session/export.py:158-181` (`_read_record_events`)
- **Symptom**: Three separate functions decode the JSONL header. Listing reads only the first line. Inspection reads the entire file to count event types. Export reads the entire file. All three re-validate that `first_event.get("type") == "session.started"`. Each codes its own error-handling convention (listing returns None on failure, inspection raises, export raises).
- **Article principle**: Redundant abstractions, "plausible-but-wrong" because the three implementations cannot disagree without producing user-visible mismatches.
- **Pi comparison**: pi-mono has a single `loadJsonlSessionMetadata` for first-line reads and a single `loadJsonlStorage` for full reads (`jsonl-storage.ts:123-159`).
- **Suggested fix**: Extract one `_read_session_jsonl(path) -> (header, events_iter)` helper that all three callers use. Make it lazy (yield events) so listing can stop after the header without pulling the whole file.
- **Severity**: low

### F13: `format_session_table` builds a tab-separated table that the harness never reads

- **Where**: `src/pipy_session/catalog.py:442-461`, `:528-546`, `:703-727`, `:730-749`
- **Symptom**: Four `format_*` functions exist, each tab-formatted, each consumed only by the same-named `pipy-session` CLI subcommand. There are also four matching `--json` flags. The tab-separated format is fragile (any tab in a summary breaks it; `_table_cell` collapses whitespace including tabs, which means information is silently lost) and is presented as a stable contract in `format_archive_verification` and `format_session_inspection` (each says "stable labeled text" in its docstring).
- **Article principle**: Plausible-but-wrong. The "stable" claim is contradicted by silent whitespace collapsing.
- **Pi comparison**: pi-mono has no equivalent CLI listing format.
- **Suggested fix**: Default to JSON in the CLI; remove `format_*` functions or treat them as best-effort previews, not stable interfaces. If a stable text format is needed, use a column-padded table with proper quoting.
- **Severity**: low

### F14: `export.py` re-implements record resolution and event validation independently of `catalog.py`

- **Where**: `src/pipy_session/export.py:74-105` (resolution checks duplicate `catalog.resolve_finalized_record`), `:158-181` (event read duplicates `catalog._read_finalized_inspection`), `:184-199` (`_safe_event` projection logic).
- **Symptom**: `export_session` calls `resolve_finalized_record` from catalog (good), but then re-checks `record_path.is_symlink()`, `resolved.relative_to(resolved_root)`, and re-parses the JSONL itself with its own malformed-event raises (different message format than catalog's). The `markdown_path` re-derivation duplicates `_safe_markdown_sibling`.
- **Article principle**: Redundant abstractions. The privacy projection (`_safe_event` + `SAFE_EVENT_KEYS`) is the only thing actually unique to `export.py`. Everything else is a hand-copy of catalog.
- **Pi comparison**: pi-mono has a single read path (`jsonl-storage.ts`), and exporters consume the in-memory representation.
- **Suggested fix**: Make `inspect_finalized_session` return the parsed events list (or expose a lower-level `read_finalized_events(path)` from `catalog.py`). Have `export_session` call that and apply `_safe_event` projection. Removes ~50 lines.
- **Severity**: low

### F15: `init_session.initial_fields` re-overrides identity fields on top of the caller's values

- **Where**: `src/pipy_session/recorder.py:89-99`
- **Symptom**:
  ```py
  if initial_fields:
      session_started.update(dict(initial_fields))
      session_started.update(
          {
              "type": "session.started",
              "project": PROJECT_NAME,
              ...
          }
      )
  ```
  This is a classic "permissively accept whatever you're given, then overwrite the parts you care about". It does work — but the contract is then "`initial_fields` can include anything *except* the four fields we'll silently overwrite." A caller that passes `type="custom"` will see their value vanish without diagnostic.
- **Article principle**: Plausible-but-wrong. Silently overwriting caller-provided keys masks bugs.
- **Pi comparison**: pi-mono's `JsonlSessionStorage.create` takes structured options (`cwd`, `sessionId`, `parentSessionPath`) and the header is built directly — no merge of free-form caller fields.
- **Suggested fix**: Accept only the fields the recorder needs (e.g., `run_id`, `event_id`, `sequence`, `harness_protocol_version`) as named parameters. Reject any other key in `initial_fields` with a `ValueError`. The current sole caller (`runner.py:283`) already only supplies those four keys.
- **Severity**: low

### F16: `_safe_component` is duplicated and the duplicate is exported by the auto-capture path

- **Where**: `src/pipy_session/recorder.py:279-283` (`_safe_component`) vs `src/pipy_session/auto_capture.py:706-710` (`_safe_component`)
- **Symptom**: Same regex (`_SAFE_COMPONENT_RE` / `_SAFE_ID_RE`), same body. Two private definitions in the same package, each used independently.
- **Article principle**: Redundant abstractions.
- **Pi comparison**: pi-mono has its own filename-safe helpers in one place.
- **Suggested fix**: Move to `pipy_session._names` (private), import in both.
- **Severity**: low

### F17: `inspect_finalized_session` reads the markdown summary into memory unconditionally

- **Where**: `src/pipy_session/catalog.py:559-563`
- **Symptom**:
  ```py
  summary_text = (
      listing.markdown_path.read_text(encoding="utf-8")
      if listing.markdown_path is not None
      else None
  )
  ```
  No size cap. If someone has appended a multi-megabyte markdown summary (the recorder doesn't bound it — `_markdown_text` only appends a newline), `inspect` pulls the whole thing into RAM and the JSON path returns it inline.
- **Article principle**: Bad state should be impossible. The recorder accepts unbounded markdown but the reader pretends it will be small.
- **Pi comparison**: pi-mono caps message sizes via the compactor.
- **Suggested fix**: Either bound markdown to a known size at finalize time, or lazily expose `markdown_path` only and require `--include-summary` in the CLI to actually read it.
- **Severity**: low

### F18: `_active_dir` invariant relies on `.in-progress` not being symlinked out

- **Where**: `src/pipy_session/recorder.py:243-258`
- **Symptom**: `resolve_active_path` rejects symlinks but only on the *file*, not on the parent. If `<root>/.in-progress/pipy/` is a symlink (or any ancestor is), `resolved.parent != resolved_active_dir` would *correctly* reject it, but only because the comparison is done after `resolve()`. The guard is correct but it is one line of subtle code with no comment explaining why it's correct, and the parent symlink case isn't tested.
- **Article principle**: Plausible-but-wrong without proof. The defensive check is also what `verify_session_archive` flags in another form, so two pieces of code are protecting the same invariant.
- **Pi comparison**: pi-mono doesn't have a two-directory lifecycle and so doesn't need this guard.
- **Suggested fix**: Document the invariant on `_active_dir` ("must be a regular directory; symlinks anywhere on this path are rejected as a security boundary"), assert it once at session-root resolution, and remove the duplicate check in verify.
- **Severity**: low

### F19: `_RecorderEventSink` lock is per-runner, not per-file

- **Where**: `src/pipy_harness/runner.py:246-280` (sink), `src/pipy_session/recorder.py:160-162` (append open in `"a"` mode)
- **Symptom**: The harness sink takes a `threading.Lock` to serialize sequence-number assignment, but the actual file `open("a")` happens *inside* the lock too. If two `HarnessRunner` instances point at the same active record (unlikely but not impossible — e.g., two test processes), there is no cross-process locking.
- **Article principle**: Bad state should be impossible. The "one writer per active record" rule is implicit.
- **Pi comparison**: pi-mono's storage uses uuidv7 ids and `appendFile` per call; the single-writer assumption is made at the session-manager layer.
- **Suggested fix**: Add `fcntl.flock(handle, LOCK_EX)` in `_write_jsonl_event` for the duration of one event write. If two writers ever race, they queue instead of interleaving JSON. Document the one-writer-per-record contract in `recorder.py`.
- **Severity**: low

### F20: Pipy's split (recorder vs catalog) is conceptually right but the catalog imports back from recorder for internals

- **Where**: `src/pipy_session/catalog.py:11` (imports `FILENAME_RE`, `PROJECT_NAME`, `resolve_session_root` from recorder)
- **Symptom**: The split looks clean at the surface (writer / reader). But the catalog needs the recorder's regex and path helpers, and `export.py` imports both. This is fine if the recorder is the canonical home of session identity, but the catalog re-derives stamp parsing (`catalog._filename_stamp` at line 1177) instead of reusing the recorder's regex match — the two `_filename_stamp` helpers in the project are duplicates.
- **Article principle**: AI slop aesthetics — looks split, isn't.
- **Pi comparison**: pi-mono's session module is one cohesive package; nothing is split for the sake of separation.
- **Suggested fix**: Promote a small `pipy_session._identity` module with `FILENAME_RE`, `PROJECT_NAME`, `_filename_stamp`, `_safe_component`, `resolve_session_root`. Have recorder and catalog import from it. Then the split (writer / reader) becomes real.
- **Severity**: low

## Notes

- The Markdown summary in pipy is generated by the **harness runner** (`runner.py:_markdown_summary`), not by the recorder. This is the correct boundary — the recorder is dumb storage. However, `auto_capture._default_summary` and `auto_capture._pi_reference_summary` also generate markdown, so there are three independent markdown generators (runner, auto-capture default, pi-reference). Consolidating these would remove ~30 lines.
- `export.py` is *not* a duplicate of catalog — its purpose (whitelisted-key projection of every event) is genuinely different from inspect (which only counts event types). The duplication in F14 is mechanical, not architectural.
- The recorder's `payload` field is the right place to put unsafe data, because `export._safe_event` drops it by construction and `inspect_finalized_session` only reports event types. That part of the design *is* "bad state impossible" and worth keeping. The leak risk is in `summary` and the markdown body (F8).
