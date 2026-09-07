# Sessions

Pipy saves product conversations in a Pi-style native session tree. A session is
an append-only JSONL file that stores the raw product transcript needed for
resume, branching, export, and `/tree` navigation.

## Where sessions live

By default, native sessions are stored outside the repository:

```text
~/.local/state/pipy/native-sessions/--<encoded-cwd>--/<timestamp>_<id>.jsonl
```

Each working directory gets its own encoded subdirectory. The native session
file is the product transcript: it may contain user prompts, assistant text,
tool calls/results, bash output, model/thinking changes, names, branch
summaries, and compaction summaries.

This is separate from `pipy-session`, which remains a summary-safe
catalog/learning utility. Use native sessions for product resume and `/tree`;
use `pipy-session list/search/inspect` only for metadata-first workflow records.

## Starting and opening sessions

A normal interactive run creates or continues a native session automatically:

```bash
pipy
pipy "start with this prompt"
```

Session startup flags mirror Pi's product workflow:

| Flag | Behavior |
| --- | --- |
| `-c`, `--continue` | Continue the most recent native session for this workspace; creates one if none exists. |
| `-r`, `--resume-session` | Open the resume picker in a TTY; outside a TTY, continue the most recent session. |
| `--session <path|id>` | Open a specific session file or partial session ID. |
| `--session-id <id>` | Open or create a session with an exact safe ID. |
| `--fork <path|id>` | Fork an existing session into a new native session file. |
| `--session-dir <dir>` | Use a custom native session store root for lookup and writes. |
| `--name <name>`, `-n <name>` | Set the session display name after open/create/fork. |
| `--no-session` | Run ephemerally: do not create a native session tree or metadata archive record. |
| `--verbose` | Force startup/resource chrome for this run, even when `quietStartup` is enabled. |
| `--offline` | Disable startup network operations for this run by setting pipy's offline/version-check guards. |

`--fork`, `--session-id`, `--session`, `-c`/`--continue`,
`-r`/`--resume-session`, and `--no-session` are mutually constrained like Pi; an
invalid combination exits with a CLI error instead of guessing.

If `--session <id>` resolves only to a different project, pipy reports that and
prompts in a TTY before forking it into the current project.

## In-session commands

| Command | Use |
| --- | --- |
| `/session` | Show the current session file, ID, branch/status details, usage, and counts. |
| `/name <name>` | Rename the current session. |
| `/new` | Start a fresh native session. |
| `/resume` | Pick or continue a previous native session. |
| `/tree` | Browse the session tree and continue from an earlier point. |
| `/fork` | Create a new session from an earlier message/session. |
| `/clone` | Duplicate the current active branch into a new session. |
| `/compact` | Reduce provider-visible context and append a durable compaction entry when enough history exists. |
| `/export [file]` | Export the current session to HTML by default, or active-branch JSONL when `file` ends in `.jsonl`. |
| `/import <file>` | Import a native session JSONL file. |
| `/share` | Upload the session as a private GitHub gist when configured. |

## Branching model

A session file is a tree. Every entry has an ID and parent ID, so choosing an
earlier point creates a new branch without deleting old history.

Common workflows:

- Use `/tree` to jump to an earlier user message, edit or submit a new prompt,
  and continue from there.
- Use `/fork` when you want a new session file starting from a selected earlier
  point or another session.
- Use `/clone` when you want a new session file containing the current active
  branch.

When you resume a session, pipy rebuilds provider context from the active branch
only. Sibling branches stay in the file and remain available through `/tree` or
export, but they are not sent as active context unless selected.

## Compaction and long sessions

Long sessions can be compacted manually with `/compact` and automatically when
history grows. Compaction keeps recent turns verbatim, drops older provider
context from the next request, and normally records a durable `compaction` entry so
resume and `/tree` rebuild the same reduced active branch. See
[Compaction](compaction.md) for details and limitations.

## Daily-use evidence and current limits

The automated baseline uses a scripted provider with real production `read`,
`edit`, and `bash` tools in a temporary workspace. It fixes a tiny Python program,
runs its check, exits, and opens the durable JSONL in a fresh coding-session
object. Assertions verify the changed file, the check's zero exit status and
output, and exact ordered tool calls/results in the continuation request without
executing tools again. Run it with:

```sh
uv run pytest tests/test_native_daily_use_baseline.py
```

This proves deterministic product-path continuity through stream input. It does
not establish a separate-process restart, live provider/model configuration,
semantic-summary quality, or the experience of completing a real repository
task. Live-provider dogfooding remains unverified. Existing automated coverage is
separate for the following workflows; test references are under `tests/`.

| Workflow | Automated evidence | Current limit |
| --- | --- | --- |
| Edit/check/reopen | `test_native_daily_use_baseline.py::test_production_edit_check_and_durable_reopen_preserve_exact_context` | Scripted provider; no interruption or compaction in this scenario. |
| Interrupt/steer | `test_native_coding_session_terminal_pty.py::test_pty_active_turn_interrupt_cancels_and_returns_to_prompt` and `::test_pty_steering_and_follow_up_queue_and_drain_order` | Real PTY input with fake-provider cancellation and queue ordering; live-provider behavior unverified. |
| Provider failure | `test_coding_session_provider_failure.py::test_exhausted_transport_failure_leaves_repl_usable_for_next_prompt` | A new prompt succeeds after failure; there is no agent-wide automatic request retry. |
| Compaction | `test_native_coding_session_resume_compact.py` and `test_native_coding_session_tree.py::test_durable_compaction_entry_survives_reload` | Whole-group reduction and one durable reopen are covered. Summaries currently contain counts; repeated semantic continuity is not established. |
| Extension load/close | `test_native_extension_conformance.py::test_golden_conformance_extension` exercises the existing conformance example, tool/command hooks and shutdown. | Separate from the persisted edit/check/reopen scenario. |
| Extension reload | `test_native_coding_session.py::test_successful_reload_publishes_one_coherent_generation_across_real_consumers` | Tests generation coherence across real consumers, not live-provider usability. |
| Headless control | `test_native_automation_rpc.py::test_prompt_emits_correlated_success_then_event_sequence` and `test_architecture_mode_contracts.py::test_json_mode_preserves_real_loop_order_with_mode_boundaries` | Protocol/event evidence; equivalent edit/check/resume through a supported public multi-turn Python API is not yet available. See [RPC](rpc.md) and [SDK](sdk.md) limits. |

Repeated compaction also has a current persistence gap: a later reduction can
advance live context without writing a durable compaction entry when too few new
user groups have arrived since the previous compaction. Reopening can therefore
lose the latest reduction. The selected D1a work in the [backlog](backlog.md)
owns this repair; it is not covered by the single-compaction reopen test above.

`just test-pty-smoke` covers streaming, chrome, and project trust. The
interrupt/steer tests above live in the separate terminal PTY module.

## Export, import, and sharing

Session export/share surfaces operate on full product transcript data:

```bash
pipy --export ~/.local/state/pipy/native-sessions/--project--/session.jsonl out.html
```

Inside an interactive session, `/export` writes HTML to the default export path,
`/export <file>` writes HTML to that path, and `/export <file.jsonl>` writes the
active branch as JSONL.

Review exported HTML/JSONL before sharing. `pipy-session export` is a separate
metadata catalog export and is not the product session export path.
