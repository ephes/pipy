# Changelog

All notable changes to pipy are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); `/changelog` renders these
entries oldest-first, and a version bump shows the new entries at startup.

## [Unreleased]

### Changed

- Internal agent-loop refactor (CM1 T4): separate tool settlement from
  top-level result/history recording, preserving policy, event, counter,
  cancellation and failure ordering. Groundwork only; no codemode tool yet.

### Added

- Internal Python codemode durability (CM1 T7b): bounded parent `nestedCalls`
  records survive script errors, timeouts and aborts, preserve partial effects,
  and reconstruct child lines on resume/tree navigation. JSON/RPC exposes
  canonical codemode parent details; compaction and branch summaries receive
  bounded attempted file paths before details are stripped. Nested results remain absent from
  persisted/provider history, and provider HTTP serializers omit details.
  Public tool/settings/CLI remain T8; live end-to-end/tmux evidence remains T9.

  Pipeline exceptions retain live/event evidence, with no saved-session guarantee.
  Summary path input preserves tool pairing; direct argument parsing is capped
  at 1 MiB and nested argument storage stays at 8 KiB per call / 32 KiB total.
  Ordinary tool/extension JSON/RPC shapes remain unchanged.

- Internal Python codemode nested lifecycle and live projections (CM1 T7a):
  session-thread child start/completion events, JSON/RPC `parentToolCallId`,
  numeric-only workflow counters and bounded sanitized child lines inside one
  TUI parent row. Resize/Ctrl+O preserves live evidence, with expanded errors
  on separate indented rows. Typed cancellation
  evidence marks backend/deadline-stopped children cancelled while preserving
  parent errors and operator-stop accounting. Only parent results persist;
  T7b adds durable nested records/resume; public tool/settings/CLI remain T8.

- Python codemode internal runtime integration (CM1 T6b): a fresh CPython-on-WASI
  worker composes canonical nested builtins through a session-thread pump, with
  operator/local-command cancellation and backend/deadline stops cancelling the
  active executor and reaping workers. Post-loop I/O failures always publish
  completion and wake the session; signal faults still attempt group cleanup
  and bounded reaping. Compute Ctrl-C without a waiter cancels the parent turn,
  skips later calls and prevents provider continuation; standalone host waiting
  Ctrl-C returns an aborted script outcome. Frozen builtin identity checks keep ordinary
  extension `codemode` behavior unchanged. No public tool or model opt-in yet;
  T7b adds durable nested records; public delivery remains T8–T11.

- Python codemode internal service/port groundwork (CM1 T6a): sequential,
  session-thread-owned nested calls reuse tool policy, hooks, validation and
  executor paths; optional composite dispatch preserves child counters and
  releases parent reservations even on acquisition/validation faults. Child
  counters publish before later canonical failures, and sticky child interruptions
  survive evidence limits and a runner returning settled. T6a uses fake-runner
  coverage; T6b adds real runtime integration. T7b adds durable nested records; T8–T11 public delivery remains.

- Python codemode policy groundwork (CM1 T5): shared nested admission and
  settlement, a reserved parent budget slot, and separate non-fatal nested
  malformed accounting. No model-visible script tool yet.

- Groundwork for Python codemode (CM1 T1, not yet a tool): an optional
  `codemode` extra (`wasmtime` 49), `python -m pipy_harness.native.codemode
  install [--from-file ZIP]` to install the pinned CPython 3.14.7 WASI runtime
  (sha256-checked, never downloaded during a turn), and `... status`, a
  fail-closed self-test that reports why codemode is unavailable.
- Codemode worker and guest prelude (CM1 T2, not yet a tool): the worker runs
  one script in CPython-on-WASI with an empty environment, the stdlib
  read-only at `/lib` and no writable directory, under a memory cap and a CPU
  backstop, and reports `ready`/`exit` on a private status fd. Scripts call
  `tools.<name>(...)`, emit output with `text()` or `print()` and see
  `ToolError`; failures carry a traceback trimmed to the script. Isolation
  probes (filesystem, `/proc`, `/dev`, symlinks, fds, native code, processes,
  network, environment, signals) run as tests and in a Linux x86_64 CI job.
- Codemode host runner (CM1 T3, not yet a tool): `run_script` runs one script
  in a fresh worker and serves its tool calls through a callback on the
  calling thread. One I/O thread does all pipe I/O without blocking and
  checks the wall deadline and cancel on every iteration; malformed or
  hostile guest lines, any message while a tool call is pending (a second
  call, a `done`, output), oversized lines and output floods end the run with
  a sandbox or script error instead of hanging or crashing the host; a call
  followed by a violation in the same write never reaches the tool. Lone
  UTF-16 surrogates in guest strings become U+FFFD, so output, tool arguments
  and errors always encode as UTF-8. A misbehaving callback (raising or
  returning a non-`CallResult`), fd or thread exhaustion at start-up, and a
  reused or shared `tool_stop` event are sandbox errors rather than
  exceptions; `ScriptLimits` refuses limits the worker cannot honour.
  The worker exits as soon as its host dies instead of running on until
  the CPU backstop, its backstop no longer drifts late, and it refuses a
  runtime whose `lib` is a symlink. The worker's process group is always
  killed and reaped.
  `format_result` renders Pi's result text: the completed/failed header with
  wall time, the output truncated to a token budget, and on failure the
  error and the "not undone" list of tool calls already made.
- `docs/codemode.md` documents the codemode sandbox core (CM1 T1–T3): where
  the runtime is installed and how, availability reasons, platform support,
  the `run_script` API and its limits.
- `quietStartup: "header"` keeps startup version and key hints while hiding
  details and resource listings. `--verbose` restores the full display.

### Fixed

- Provider errors saying "Selected model is at capacity" now use the existing
  bounded retry policy; quota and billing exhaustion remain non-retryable.
- Compaction records `tokensBefore` as Pi's context-token estimate instead of
  the byte size of the dropped history, so `Compacted from N tokens` matches
  the footer's context figure. Existing session entries keep their old value.
- The terminal shows Pi's `Compacting context... (escape to cancel)` loader
  during manual `/compact` and `Auto-compacting...` during automatic
  compaction; previously nothing was shown while the summary ran.

### Documentation

- Recorded the Python-codemode isolation spike: CPython-on-WASI under
  wasmtime is the recommended backend; Seatbelt and Landlock+seccomp probes
  were broken by red teams. Includes the first-slice contract and tasks.
- Queued a Python-codemode isolation spike and first-slice plan, with MCP and
  parallel execution retained as separate follow-ons.

- Audited Pi 1.0 selected surfaces, reran the catalog/session comparison gates,
  and refreshed the pinned upstream system-prompt documentation topics.

- Recorded the upstream parity scope: retain the established Pi coding agent
  as the daily-use reference, queue a drift audit, and track MCP/codemode and
  the experimental durable runtime as separate scope decisions.

## [0.3.0] - 2026-09-30

### Highlights

A curated summary of the largest changes in this release; the full entries
follow.

- GPT-6.1 Sol (`gpt-6.1-sol`) on `openai`, `openai-codex` and
  `github-copilot`, and the new default `openai-codex` model.
- Sign in with ChatGPT for the `openai` provider: `/login openai` and
  `/logout openai`.
- Sessions survive trouble: provider retries follow Pi's auto-retry and are
  shown with a countdown, an aborted or failed turn keeps its partial answer,
  a tool result that overflows the context no longer wedges the session, and a
  resumed session redraws its conversation and gets its model and thinking
  level back.
- Every assistant message stores its usage and cost, so totals survive resume
  and model switches; `/session` prints Pi's `Session Info` with a `Cost`
  section.
- The built-in tools behave like Pi's: `read`, `ls`, `grep`, `find`, `write`,
  `edit` and `@file` resolve paths like Pi with no deny list, `bash`, `grep`,
  `find` and `ls` cut long output with Pi's limits and notices, `edit` takes
  Pi's `edits[]`, and `bash` reports a non-zero exit as an error.
- Tool calls are drawn as Pi's tool boxes with Pi's rows and colours, and the
  default `pi` theme uses Pi's `dark` palette.
- The `/model`, `/thinking` and `/resume` selectors and the slash menu use Pi's
  fuzzy search.
- The system prompt is Pi's (`<tools>`, `<rules>`, `<docs>` sections) and is
  recorded in the transcript as system messages; models that accept
  mid-conversation system messages get prompt and tool changes in place, so
  the prompt cache prefix survives them, and Anthropic models that support it
  get the thinking effort mid-conversation.
- Reasoning is stored and replayed like Pi: the same model gets its thinking
  back with provider signatures and encrypted reasoning, other models get it
  as plain text.
- Skills run as Pi's `/skill:<name> [args]` commands.

### Breaking

- `--read-root`, `PIPY_READ_ROOTS` and the automatic reference roots are
  removed; the read tools open any path, including `.git`, ignored files and
  paths outside the workspace.
- `/skill <name>` and the bare `/skill` listing are removed; use
  `/skill:<name>`. `enableSkillCommands: false` only hides the commands. A
  skill without a frontmatter `name` is named after its directory.
- `edit` takes `edits[]` of `{oldText, newText}`; `old_string`, `new_string`
  and `replace_all` are gone.
- `write` overwrites an existing file and creates parent directories.
- `bash` reports a non-zero exit as an error ending in `Command exited with
  code N`; the `exit code: N` / `[output]` framing is gone.
- `grep`, `find` and `ls` take Pi's parameters and return Pi's output format.
- `/model <ref>` switches only on an exact reference and only for the current
  session; other text opens the model selector.

### Added

- The default system prompt is now Pi's (`system-prompt.ts`, with pipy's name
  and docs): a short preamble, then Pi's `<tools>` (one line per active tool),
  `<rules>` (the rules the active tools add, then Pi's two defaults) and
  `<docs>` (where pipy's README, docs and examples live) sections, before the
  appended prompt, context files, skills and working directory. The tool,
  rule and skill sections are rebuilt for each prompt from the active tools,
  so an extension that changes them sends only the changed sections. A
  replaced prompt drops the three new sections, as in Pi.
- Skills run as Pi's `/skill:<name> [args]` commands: the skill file is read
  again and sent as Pi's `<skill>` block, followed by the text after the name,
  and the TUI draws it as Pi's collapsible `[skill] <name> (ctrl+o to expand)`
  box, live and on resume. The slash menu lists the skills with their
  descriptions.
- Skill frontmatter `disable-model-invocation: true` keeps a skill out of the
  system prompt; it still runs through its command. Without `read`, skills are
  advertised for loading with `bash`, as in Pi.

- Reasoning is stored and replayed like Pi (DF1-F2b, DF1-F6b, SYS1c): an
  assistant message keeps Pi's ordered content -- thinking blocks with their
  provider signatures, text with its Responses message id and phase, tool
  calls with Gemini thought signatures -- and the adapter API that answered.
  The same model gets it back exactly (Anthropic signed and redacted
  thinking, OpenAI Responses/Azure/Codex reasoning items with
  `encrypted_content`, message ids and `fc_` item ids, Gemini
  `thoughtSignature`s); any other model gets the thinking as plain text, as
  Pi's `transformMessages` does. Azure now asks for the encrypted reasoning
  (`include`, `summary: "auto"`). Gemini thought parts are no longer part of
  the answer text. An aborted or failed turn keeps the thinking and tool
  calls streamed so far and Anthropic's `providerThinkingLevel`. The TUI
  draws thinking in block order for answers that were not streamed and on
  resume, and Ctrl+T (or a new hidden-thinking label) re-renders every
  thinking row, like Pi; the pipy-only deferred-reasoning buffer is gone.
  JSON/RPC messages carry the thinking blocks and `api`, and partials stream
  `thinking_delta` events.

- Sign in with ChatGPT for the `openai` provider, as in Pi (Pi `02eed88fd`):
  `/login openai` opens the OpenAI sign-in page, takes the browser callback
  when you press Enter (or a pasted redirect URL) and stores the subscription
  login in the auth store; `/logout openai` removes it. A new global
  `deviceId` setting names the installation. The access token is refreshed
  before a request and the rotated refresh token is written back under the
  auth-file lock. Requests with a ChatGPT token omit `prompt_cache_retention`
  and `prompt_cache_options`, the footer cost shows `(sub)`, the
  `subscription_sharing_usage_limit_exceeded` error links to ChatGPT usage and
  is not retried, and `subscription_sharing_usage_unavailable` /
  `subscription_sharing_user_unavailable` are retried.

- GPT-6.1 Sol (`gpt-6.1-sol`) on the `openai`, `openai-codex` and
  `github-copilot` providers, with Pi's rows (MC6, Pi `12c416e1a`): $2 input,
  $0.10 cached input, $2.50 cache writes, $10 output, 272K context (1.05M on
  Copilot) and 128K output. It rejects `reasoning.effort: "none"`, so thinking
  cannot be switched off: `off` is not offered and no off-state `reasoning`
  field is sent. A carried `off` clamps to the lowest offered level: `minimal`
  on Codex (sent as `low`), `low` on OpenAI and Copilot. Levels run up to
  `max`.
- Models that accept mid-conversation system messages now receive them in
  place, as in Pi (SYS1b, Pi `9e05370b2`). The leading prompt stays fixed
  and later prompt or tool changes arrive where they happened, so the prompt
  cache prefix survives them. The catalog carries Pi's compat flags:
  `supportsMidConvoSystemMessages` (OpenAI and Codex GPT-5.4 and later,
  Anthropic Opus 4.8/5/5.5, Sonnet 5.5, Fable 5/5.1, and the GitHub Copilot
  rows that already carried Pi's flag, including through its per-request
  OAuth token refresh),
  `supportsAdditionalTools`, `supportsMidConvoToolChanges` and
  `supportsDeveloperRole`; `supportsMidConvoToolAdditions` is honoured from
  `models.json`. Per API:
  - OpenAI Responses, Azure and Codex send a later update as a `developer`
    message (`system` when the model is not a reasoning model or has
    `supportsDeveloperRole: false`), and load tools added later in place as
    `additional_tools` or, without that flag, as a client `tool_search` pair;
  - Chat Completions sends updates in the instruction role, and with
    `supportsMidConvoToolAdditions` a `{role: "system", tools}` message;
    Mistral sends `system` messages;
  - Anthropic sends `system`-role messages before the next assistant turn,
    and on models with tool changes adds and removes tools with
    `tool_addition`/`tool_removal` blocks, a deferred placeholder tool and
    the `mid-conversation-tool-changes-2026-07-01` beta;
  - Google and Bedrock keep sending the replayed prompt, like Pi.

  A `before_agent_start` suffix or a `before_provider_request` prompt change
  forces the prompt for that request, which then collapses as before. The
  request budget counts the updates and every declaration kept on the wire.
- Anthropic mid-conversation effort, as in Pi (SYS1c, Pi `4e69b0c28`): on
  Claude Opus 5, Opus 5.5, Sonnet 5.5 and Fable 5.1 (`supportsMidConvoEffort`)
  requests think adaptively with `block_binding`, send the effort as
  `output_config` system messages (the current one last, and each earlier
  answer's own before it) and add the `mid-conversation-output-config` and
  `thinking-binding-controls` betas. Each answer records
  `providerThinkingLevel`, stored in the session and emitted in JSON/RPC
  messages.
- `/tree` navigation restores the active tools the target branch declares,
  as Pi's `navigateTree` does (SYS1c). Resume keeps the configured tools.

### Changed

- `/skill <name>` and the bare `/skill` listing are gone; use
  `/skill:<name>`. `enableSkillCommands: false` now only hides the skill
  commands from the menu, as in Pi: the skills stay in the system prompt and a
  typed `/skill:<name>` still runs.
- A skill without a frontmatter `name` is named after its directory, a plain
  `.md` file included (Pi): an unnamed `.pipy/skills/lint.md` is now `skills`.
- Skills and prompt templates are searched in Pi's order: installed packages
  first, then the project and global directories, then `--skill` /
  `--prompt-template` paths, so a default resource wins a name clash.
- Prompt-template and custom-command stores follow symlinks like Pi.
- The leading prompt has Pi's shape (SYS1c). OpenAI Responses and Azure send
  it as the first `input` item in the instruction role instead of
  `instructions`, and a request without messages sends a user item instead of
  a bare string; Chat Completions sends it in the instruction role
  (`developer` for reasoning models that accept it); an empty prompt sends no
  message; Codex sends `"You are a helpful assistant."` when the prompt is
  empty. A session made before SYS1a sends its prompt as a later system
  message to models that accept one, as in Pi.
- Auth store writes (`/login`, `/logout`) re-read `auth.json` under a file lock
  and change only their provider's entry, as Pi's `AuthStorage` does, so a
  credential another process or a token refresh wrote meanwhile is kept.
  `/logout openai` and `/logout github-copilot` keep the selected model when an
  environment key still covers its provider.
- The system prompt is recorded as Pi's tagged sections (SYS1b): the untagged
  `preamble` (the default or custom prompt), then
  `<addendum>` (appended prompts, now wrapped in their tag), `<project_context>`,
  `<skills>`, a new `<cwd>` section with the working directory, and pipy's
  `<resume>` block. A later run patches only the sections that changed. A
  `before_agent_start` suffix is no longer recorded in the transcript; it
  forces that run's prompt, as a forced prompt does in Pi.
- Tools loaded mid-run are declared by the next turn's system message instead
  of a marker on the loader's tool result, as in Pi (which removed
  `addedToolNames` and `supportsToolReferences`). Models without the new
  compat flags now receive such tools as ordinary definitions; Anthropic
  `tool_reference` result blocks are gone. Stored sessions no longer write
  `added_tool_names`.
- Selectors and slash completion follow Pi (DF1-F7b). `/model <ref>` switches
  only on an exact reference among the scoped or available models, for this
  session only (`Model: <id>`); other text opens the model selector with it
  as the search. The `/model` selector is Pi's: a search box with Pi's fuzzy
  ranking, only models with configured auth, the current model and the saved
  default marked, Tab between scoped and all models, Enter for the session
  and Ctrl+S (`app.models.save`, rebindable) to also save the default. The
  `/thinking` selector gains the same search box. The slash menu ranks
  commands with Pi's fuzzy filter, accepting a command inserts `/<name> `,
  and `/model` and `/thinking` complete their arguments. The `/resume`
  search takes Pi's fuzzy tokens, `"phrases"` and `re:` patterns. Slash
  commands are no longer drawn as user messages; a prompt template run draws
  its expanded text. `/model` and `/thinking` failures are red `Error: …`
  lines. The plain REPL keeps its `/model <ref>` resolver.
- Tool calls in the terminal UI are drawn as Pi draws them (TOOLS3): one
  padded box per call, grey while it runs, then green or red, with each
  tool's own rows. A collapsed `read` shows `read <path>:<range>` (or
  `[skill] <name>`, `read resource AGENTS.md`, `read docs …`); `grep` shows
  15 lines and `find`/`ls` 20, with `[Truncated: …]` warnings; `write`
  previews 10 lines of the content; `edit` shows its numbered diff in its box,
  computed before the edit runs, with the changed words of a one-line edit
  highlighted; `bash` shows `$ command`, the last 5 lines, a `[Full output: …]`
  warning and `Took Ns`. The `$ ` prompt on non-bash rows, the
  `[error] tool reported a failure` line and `Took` on non-bash rows are
  gone. Ctrl+O now re-renders every tool row, extension-rendered ones
  included, live and after a resume, and clears the scrollback like Pi.
  Rows use Pi's `toolPendingBg`/`toolSuccessBg`/`toolErrorBg`/`toolOutput`
  colours, which theme files can set (`tool_pending_bg_*`,
  `tool_success_bg_*`, `tool_error_bg_*`, `tool_output_*`). There is no
  syntax highlighting yet.
- Tool results carry Pi's `details` and the session file stores them
  (TOOLS3): truncation facts and limits for `read`/`grep`/`find`/`ls`/`bash`
  (with `fullOutputPath`), `edit`'s `diff` and `firstChangedLine`, and an
  extension tool's `ToolResult.details` (its JSON copy). They are never sent
  to a provider, and restored rows draw from them.
- The default `openai-codex` model is now `gpt-6.1-sol` (was `gpt-5.5`), as in
  Pi (MC6, Pi `12c416e1a`). `--native-provider openai-codex` without
  `--native-model` starts on it; the other defaults are unchanged.
- `read`, `ls`, `grep`, `find`, `@file` and `@image:` resolve paths the way
  Pi's tools do (READ2, Pi `path-utils.ts`): relative to the working
  directory or absolute, with `~` expanded, a leading `@` stripped and unicode
  spaces normalized, and without any deny list. Files under `.git`,
  `.gitignore` matches, generated directories (`node_modules`, `build`,
  `dist`, `.venv`, `.pipy`, …), suffixes such as `.lock`, `.map`, `.min.js`
  and `.d.ts`, `..` and paths outside the workspace all read now, including
  the `bash` full-output temp file. `read`, `@file` and `@image:` also try the
  names macOS gives screenshots (a narrow no-break space before `AM`/`PM`,
  NFD, a curly apostrophe). `ls` lists every entry. `grep` and `find` leave
  out only what `rg --hidden` and `fd --hidden` leave out: `.git` is searched,
  and `.gitignore` (inside a repository, and for `find` also outside one),
  `.ignore`, `.rgignore`/`.fdignore` and `.git/info/exclude` apply. `grep`'s
  Python fallback and `find` follow those rules, including rg's `glob`
  override. `find` accepts patterns that start with `/` or contain `..` or
  `\`.
- `write` and `edit` follow Pi's tools (READ2b, Pi `write.ts`, `edit.ts`,
  `edit-diff.ts`). Both resolve paths like the read tools, with no deny list
  and no size cap. `write` creates parent directories and overwrites an
  existing file. `edit` takes `edits[]` of `{oldText, newText}` (the
  `old_string`/`new_string`/`replace_all` arguments are gone; a JSON-string
  `edits`, a single edit object and top-level `oldText`/`newText` are still
  accepted, as in Pi). Each `oldText` must match once, exactly or after Pi's
  fuzzy normalization (trailing spaces, smart quotes, dashes, special
  spaces), and the edits must not overlap. A UTF-8 BOM and CRLF line endings
  survive the edit. Results and errors use Pi's texts (`Successfully wrote
  to …`, `Successfully replaced N block(s) in ….`, `Could not find the exact
  text in …`, Node's `EACCES: permission denied, open '…'`), and the diff row
  shows Pi's numbered `+NN`/`-NN` lines. Mutations of one file are
  serialized, as in Pi.
- `bash` reports results like Pi (TOOLS2): the output alone on success; a
  non-zero exit is an error ending in `Command exited with code N` (a
  signal-killed shell reports `128 + n`), and a timeout or abort ends in
  `Command timed out after N seconds` or `Command aborted`. The `exit code:
  N` / `[output]` framing is gone.
- The `!`/`!!` shortcut keeps its output like Pi's `bash-executor.ts`:
  ANSI escapes and control characters are removed, the last 2000 lines or
  50 KB are kept (was 16 KB) and the full output goes to a temp file. The
  model sees Pi's `bash` message text (``Ran `cmd` `` with the output in a
  fenced block, `Command exited with code N`, `(command cancelled)`, `[Output
  truncated. Full output: …]`), and the rows show the last 20 lines with
  `(exit N)`, `(cancelled)` and the full-output path, following Ctrl+O. In
  the terminal UI a `!` command no longer stops after 600 seconds; Escape
  cancels it, as in Pi.
- Tool rows show Pi's headers: `grep /pattern/ in path (glob) limit N`,
  `find pattern in path (limit N)`, `ls path (limit N)`, and `write`/`edit`
  paths with the home directory shortened to `~`.
- `ls` sorts like Pi's `localeCompare` (ICU root collation): punctuation,
  then digits (`10` before `9`), then letters with accents and case as minor
  differences, other scripts after Latin. pipy uses a collation table
  measured on Node, so characters outside it can differ from Pi.
- `find` and `grep`'s Python fallback apply the global git excludes file
  (`core.excludesFile`, else `~/.config/git/ignore`) where rg and fd do.

- The terminal UI follows Pi's look more closely (DF1-F7, Pi `1b347794e`):
  - The default `pi` theme uses Pi's `dark` theme colours: accent, dim and
    muted text, `[Context]` headings, errors, warnings, the blue user message
    background, reasoning text and the tool boxes' 256-colour fallbacks. The
    editor border takes the thinking level's colour and turns green while the
    input starts with `!`; the ` ! bash ` label on the border is gone.
  - `/compact` and automatic compaction redraw the chat as Pi does: the kept
    messages, then the `[compaction]` row at once (a padded box in Pi's
    summary colours, also for `[branch]` rows), instead of a
    `compacted conversation context` notice.
  - A running `bash` row shows `Elapsed Ns`, updated every second, until
    `Took Ns` replaces it.
  - Status lines lose the `pipy  pipy:` prefix and are dim, like Pi's; the
    Ctrl+O, Ctrl+T, Shift+Tab and `/thinking` statuses use Pi's texts
    (`Tool output: expanded`, `Thinking level: high`, …).
  - Startup no longer leaves a stale editor frame above the header, and
    `quietStartup` hides the header and resource listing in the terminal UI
    too. The untrusted-project warning follows the restored session.
  - `--print`, `--mode json` and `--mode rpc` no longer write the startup
    chrome, prompt echo or footer to stderr; a successful run leaves stderr
    empty.

- The `bash`, `grep`, `find` and `ls` tools handle long output the way Pi's
  do (TOOLS1, Pi `4df157433`), and take Pi's parameters and descriptions:
  - `bash` keeps the last 2000 lines or 50 KB (was 16 KB), saves the full
    output to a temp file (`pipy-bash-<id>.log` in the system temp directory)
    and ends with `[Showing lines X-Y of N. Full output: <path>]`, which
    `read` can open (READ2).
  - `grep` takes a regex (or `literal`), `glob`, `ignoreCase`, `context` and
    `limit` (default 100). Rows are `path:N: text` relative to the search
    path, with `path-N- text` context lines; long lines are cut to 500
    characters. It uses `rg` when installed, else a Python search with the
    same rules. It no longer skips control-character or secret-shaped files.
  - `find` matches like `fd --glob`: a pattern without `/` matches names at
    any depth, smart case, directories end in `/`; `limit` defaults to 1000.
  - `ls` takes an optional `path` and `limit` (default 500) and lists names
    sorted case-insensitively, with `/` after directories.
  - Every cut ends with Pi's notice, such as `[100 matches limit reached. Use
    limit=200 for more, or refine pattern]` or `[50.0KB limit reached]`,
    instead of `... (truncated)`. Empty results read `No matches found`,
    `No files found matching pattern` or `(empty directory)`.

- The system prompt and tool declarations are now part of the transcript, as
  in Pi (`9e05370b2`, SYS1a). The first run of a session records a
  `role: "system"` message:
  - its only section, `preamble`, holds pipy's prompt;
  - `toolsAdded` lists every tool the model can call.
  Later runs record one only when the prompt or the tools change (section
  patches, `toolsAdded`/`toolsRemoved`), and so does a tool change inside a
  run. `--mode json` and `--mode rpc` emit it as
  `message_start`/`message_end` after `turn_start` and before the user
  message, and `agent_end.messages` starts with it. RPC `get_messages`
  returns it. The session file stores it as a `message` entry. A compaction
  entry stores the replayed state as `systemMessage` and replaces earlier
  system messages with it. The TUI draws nothing for it, and `/tree` shows
  `[system]`. Provider requests are unchanged: pipy sends the prompt out of
  band and never sends later system messages, which is what Pi does for
  models without mid-conversation system messages. `automation_pi_comparison.py`
  passes against Pi `4df157433` again. Per-model mid-conversation
  serialization and Anthropic mid-conversation effort are backlog SYS1b.

- Ctrl+O now expands and collapses tool results already on screen, not only
  the ones rendered afterwards.

### Removed

- `--read-root`, the `PIPY_READ_ROOTS` environment variable, the automatic
  reference roots found in `AGENTS.md` and docs, and the `Reference roots`
  block in the system prompt (READ2). They only widened what the read tools
  could open; those tools now open any path, as in Pi.

### Fixed

- Session cost and token totals are kept (USAGE1, Pi `1b347794e`). Every
  assistant message now stores its usage (uncached input, output, cache reads
  and writes, total tokens and the cost per part, priced from the model's
  catalog row) and the provider and model that answered. The footer, `/session`
  and `--mode rpc` `get_session_stats` sum the stored messages of the whole
  session, every branch included, so the totals no longer reset on `pipy -r` or
  a `/model` switch. The footer shows Pi's parts: `↑` is uncached input, each
  count appears only when non-zero, `CH` is the latest response's cache hit
  rate, and counts use Pi's format (`12k`). `/session` prints Pi's
  `Session Info` block with message counts, tokens (cached and uncached input)
  and a `Cost` section with a per-model breakdown and re-billed cache.
  `--mode json`/`--mode rpc` assistant messages carry Pi's `usage` object and
  `provider`/`model`. Sessions stored before this change load unchanged and
  count as zero.
- An aborted or failed turn is no longer lost (DF1-F6, Pi `4df157433`). The
  assistant message is kept with the text streamed so far and a stop reason
  (`aborted`, or `error` with its message), stored in the session and shown
  again on resume as the partial text followed by `Operation aborted` or
  `Error: <message>`. As in Pi, it is never sent to a provider again: every
  request (and the compaction summary request) skips it, so the next prompt
  continues from the last complete turn. `--mode json`/`--mode rpc` assistant
  messages now carry `stopReason` (`stop`, `toolUse`, `aborted`, `error`) and
  `errorMessage`; `/tree` shows a stopped turn without text as
  `assistant: (aborted)`. The fake `fake-tools` model streams a partial
  answer before waiting when a prompt starts with `STREAMBLOCK`.

- Provider retries are visible and follow Pi's agent-level auto-retry
  (DF1-F4, Pi `4df157433`):
  - The TUI shows the failed attempt as `Error: <message>` and replaces
    `Working...` with `Retrying (1/3) in 2s... (escape to cancel)`, counting
    down. Escape cancels the retry. Exhausted retries end with `Retry failed
    after 3 attempts: <error>`.
  - Every provider is retried, not only OpenAI-Codex. Pi's classifier decides
    (overloaded such as a 529 `overloaded_error`, rate limits, 5xx, network,
    timeouts, early stream ends, "you can retry your request"). Quota, billing
    and context-overflow errors are never retried.
  - A failure after partial output is retried too, and the new answer replaces
    the partial one. Backoff is exactly `retry.baseDelayMs * 2^(n-1)`, with no
    jitter.
  - A Codex stream `error` event now reads `Codex error: <message or code>`,
    and `response.failed` reads the server's message, so the classifier sees
    their text. A mid-stream `server_is_overloaded` is retried.
  - JSON and RPC emit `auto_retry_start` for each retry and one
    `auto_retry_end` per sequence (it was one per attempt). A cancelled retry
    reads `Retry cancelled`. The retried attempt's `message_update` text starts
    over.
  - After a failed turn the footer context no longer jumps (e.g. 0.6% to
    7.2%). It keeps the last successful response's usage, as in Pi.
- A turn whose tool result alone overflows the context window no longer wedges
  the session (DF1-F5). That turn is still refused, as in Pi, but the next
  prompt now compacts it away. Two changes follow Pi (`4df157433`):
  - Compaction and branch-summary requests cut each tool result to its first
    2000 characters plus `[... N more characters truncated]` (Pi
    `serializeConversation`), so the summary request no longer fails its own
    size check.
  - In a persistent session, an automatic cut that keeps only the new prompt
    writes a compaction entry whose `firstKeptEntryId` is its own id (Pi
    `appendCompaction(summary, null)`) instead of refusing with `retained
    history has no durable origin`. Reopen, fork and clone honor it.
  A removed range that is still too large after truncation (for example a huge
  paste) stays refused until `/new`, as in Pi.
- `--mode rpc` no longer stops running turns after a `follow_up` arrives
  before an idle `steer` has started. The session worker could exit, so later
  prompts were accepted but never ran, `compact` never answered, `get_state`
  kept reporting `isStreaming: true` and `new_session` or `cycle_model`
  answered `session is not idle`. It depended on timing and also affected
  0.2.0.

- A resumed conversation is shown again. Startup with `-r`, `--continue` or
  `--session`, `/resume`, `/tree` navigation, `/fork`, `/clone`, `/new` and
  `/import` redraw the transcript from the active branch, as Pi does: user and
  assistant messages, tool calls with their results, `!` shell rows,
  `[compaction]` and `[branch]` summaries (Ctrl+O expands them) and extension
  entries. Before, the screen stayed empty although the model had the context.
- A resumed session gets its model and thinking level back. pipy now records a
  `model_change` with a new session's first message and on every model switch
  (plus a `thinking_level_change` when the switch clamps the level), and
  opening a session restores its last model (unless `--native-provider` or
  `--native-model` pins one) and thinking level (unless `--thinking` is
  given), at startup and after `/resume`, `/fork` and `/clone`. A model that
  cannot be used prints `pipy: Could not restore model …` at startup and keeps
  the default. Before, a resumed session started at the default thinking
  level and model.

## [0.2.0] - 2026-09-29

### Highlights

A curated summary of the largest changes in this release; the full entries
follow.

- Current model rows from Pi's generator (`4df157433`): Claude Opus/Sonnet 5.5
  and 5, Fable 5/5.1, Opus 4.8, GPT-6 Sol/Luna/Astra, GPT-5.6 and Gemini 3.5
  Flash, with Pi's provider defaults and thinking maps.
- Context files and skills load the way Pi loads them: `AGENTS.md` or
  `CLAUDE.md` per directory, `.agents/skills/` roots, and symlinks followed.
- Prompt caching for OpenAI, OpenAI-Codex, Azure, Anthropic and Bedrock, with
  `PIPY_CACHE_RETENTION=long`.
- `/thinking` sets the session's thinking level, and the footer shows the live
  level.
- Session cost comes from each model's catalog row, priced like Pi's
  `calculateCost`.
- New `xai` and `github-copilot` providers, with `/login github-copilot`.
- `read` pages like Pi's `read`: `offset`/`limit`, 2000 lines or 50 KB per call,
  and a notice that says where to continue.
- `just catalog-drift` reports where the built-in catalog differs from Pi's.
- Fixes from live dogfooding on OpenAI-Codex: a model switch keeps the
  conversation, `edit`/`write` diffs no longer corrupt the TUI, Escape no longer
  freezes the frame on a WebSocket stream, and tool rows no longer show argument
  dumps.

### Added

- New `xai` and `github-copilot` providers, following Pi (`4df157433`).
  `xai` serves Pi's Grok 4.3 to 4.7 rows over the OpenAI Responses API with
  `XAI_API_KEY` (default `grok-4.7`). `github-copilot` serves Pi's 33 Copilot
  rows (Claude, GPT, Grok, Gemini, Kimi, MAI), each on the API family Copilot
  uses for it (default `gpt-5.4`). See `docs/providers.md` (GitHub Copilot).
  - `/login github-copilot` runs GitHub's device-code login, including GitHub
    Enterprise domains. It lists the account's models and enables the ones
    whose policy is unconfigured. `/logout github-copilot` removes the
    credential. `COPILOT_GITHUB_TOKEN` also works.
  - Requests go to the endpoint named in the Copilot token and refresh the
    token when it has less than five minutes left. They carry Copilot's editor
    headers plus `X-Initiator`, `Openai-Intent` and, when an image is sent,
    `Copilot-Vision-Request`. Claude models use Bearer auth. After a login,
    model lists show only the account's models.
  - Not ported: Pi's xAI OAuth login, and cost tiers. A refreshed Copilot
    token is kept in memory rather than written back to the auth store.
- `/thinking` sets the thinking level for the current session, as in Pi
  (`4df157433`). `/thinking <level>` takes any level the model offers, in any
  case. A bare `/thinking` opens a selector of the model's levels with Pi's
  descriptions, the current level marked `✓` and the default marked
  `· default`. Enter applies the level for this session. The new
  `app.thinking.save` binding (Ctrl+S, rebindable) also saves it as
  `defaultThinkingLevel`.
- `just catalog-drift` compares the built-in model catalog with Pi's
  generated catalog and reports rows or values that differ. This covers
  context window, max tokens, cost, thinking maps, API family, base URL and the
  compat flags pipy reads. Intentional differences are listed in
  `scripts/catalog_drift_allowlist.json`. It is a manual check, not part of
  `just check`. See `docs/provider-catalog.md` (Catalog drift check).
- Context files now load the way Pi loads them (Pi `4df157433`). In each
  directory pipy takes the first file that exists from `AGENTS.override.md`,
  `AGENTS.md`, `AGENTS.MD`, `CLAUDE.md`, then `CLAUDE.MD`. It stops ignoring
  `CLAUDE.md`, so projects whose instructions live only in `CLAUDE.md` now get
  them.
  - Inside a linked git worktree nested in its main checkout, the main
    checkout's same-named file is skipped, as in Pi.
  - The files reach the model as Pi's `<project_context>` section, with one
    `<project_instructions path="...">` block per file.
  - The startup `[Context]` list comes from the same loader.
- Skills now come from Pi's roots and in Pi's layout:
  - project `.agents/skills/` directories from the cwd up to the git root
    (trusted projects only), and `~/.agents/skills/`;
  - a `<name>/SKILL.md` directory is one skill, named after its directory;
  - `.gitignore`, `.ignore` and `.fdignore` files inside a skills directory
    apply.

  A `.agents/skills/` directory in the project or any parent directory now makes
  the project need trust. The skills advertisement is wrapped in Pi's `<skills>`
  section.

- The built-in catalog now carries Pi's current frontier rows. Costs, context
  windows, max tokens and thinking maps come from Pi's generator at
  `4df157433`. The new rows are:
  - Anthropic: Claude Opus/Sonnet 5.5, Opus/Sonnet 5, Fable 5/5.1, Opus 4.8 and
    Haiku 4.5.
  - OpenAI and Codex: GPT-6 Sol/Luna/Astra and GPT-5.6 Terra/Luna, plus
    GPT-5.6 Sol on OpenAI.
  - Google and Vertex: Gemini 3.5 Flash and 3.1 Flash Lite.
  - Bedrock: `us.` mirrors of Claude 5.x, Fable 5, Opus 4.8 and Haiku 4.5.

  Claude 5.x and Fable use adaptive thinking, and `max` reaches
  `output_config.effort`. OpenRouter Claude rows are not mirrored yet, because
  Pi routes them through an Anthropic-Messages transport that pipy lacks.

- OpenAI prompt caching now follows Pi (`4df157433`), so consecutive turns in
  one session can reuse the provider's prompt cache:
  - OpenAI-Codex sends the session id (cut to 64 characters) as the body
    `prompt_cache_key` and as the `session-id` and `x-client-request-id`
    headers, on SSE and WebSocket. Before, the WebSocket used a fresh id per
    attempt and SSE sent none.
  - OpenAI Responses sends the key plus Pi's affinity headers (`session_id` and
    `x-client-request-id`, or `x-session-id` for OpenRouter).
    `PIPY_CACHE_RETENTION=long` (Pi's `PI_CACHE_RETENTION`) asks for 24-hour
    retention, or a 30-minute `prompt_cache_options` TTL on GPT-5.6 and later.
  - Azure OpenAI sends the key.
  - Compaction and branch summaries send no cache key and use a fresh routing
    id, as in Pi. Azure is the exception: Pi sends it that routing id as the
    key.

- Anthropic prompt caching now follows Pi (`4df157433`). Before, pipy sent no
  `cache_control`, so Claude never reused a cached prefix.
  - Anthropic Messages marks the system prompt, the last tool, and the last
    block of a trailing user message with `cache_control: {type: "ephemeral"}`.
    The system prompt is now sent as a text block, and omitted when empty.
  - Bedrock Claude marks the system prompt and the last user block, but not
    tools. Only Pi's cache-capable Claude families get the markers:
    3.5 Haiku, 3.7 Sonnet, 4.x and 5.x. `AWS_BEDROCK_FORCE_CACHE=1` turns them
    on for ids that do not name Claude, such as application inference
    profiles.
  - `PIPY_CACHE_RETENTION=long` asks for the 1-hour TTL (`ttl: "1h"`).
    Compaction and branch summaries send no markers.
  - The 1-hour part of a cache write is read from the usage
    (`cache_creation.ephemeral_1h_input_tokens`) and priced at twice the input
    rate, as in Pi.
  - models.json `compat.supportsLongCacheRetention`,
    `compat.supportsCacheControlOnTools`, `compat.sendSessionAffinityHeaders`
    and `compat.sessionAffinityFormat` work as in Pi. An OpenRouter Anthropic
    endpoint gets the `x-session-id` header.

- Terminal `/import` now stages its permissive copy through the existing
  presentation path, then adopts that durable copy through the native transition
  owner. The owner validates the current terminal lease before the switch hook,
  claims the imported file before guarded state-first publication, retires the
  old claim before rebuilding history, and releases the adopted claim on normal
  or fatal teardown. Confirmations, recovery for missing recorded workspaces,
  controlled diagnostics, and partial-copy behavior remain unchanged.

- Terminal `/fork` and `/clone` now use the native transition owner. They
  snapshot the guarded active tree, claim and publish the child before releasing
  the source lease, and preserve terminal diagnostics, footers, and the detailed
  fork extension gate. Empty terminal sessions now refuse either command rather
  than creating an empty child; import and Python/RPC transition behavior are
  unchanged.

- Terminal `/new` now uses the native transition owner. Persistent terminal
  sessions create and claim a sibling before state-first replacement and release
  the adopted claim on teardown; in-memory terminal sessions remain file-free.
  Existing terminal diagnostics, footers, and extension-switch gating are
  preserved, while Python and RPC `new_session` semantics remain unchanged.

- Terminal `/resume` now adopts resolved native session targets through the
  canonical transition owner. Each stream lifetime claims its initial durable
  tree, strict-loads and workspace-validates a candidate before state-first
  replacement, and releases the current claim on normal or fatal teardown.
  Same-file aliases are no-ops; picker, listing and session-management
  presentation remain unchanged.

- Persistent Python product sessions can now fork or clone, create an empty
  sibling session, and strictly switch to one exact durable session in place.
  The native transition owner preserves the facade lifetime, canonical path
  claims, extension gates, and state-first history rebuild. RPC now adopts the
  same native owner for `new_session`, strict `switch_session`, `fork`, and
  `clone`, preserving correlated outcomes and the native idle-only control
  boundary.

- Direct RPC bash now emits bounded, line-gated and secret-redacted
  `bash_execution_update` records before its correlated terminal response.
  Per-operation relays isolate update correlation and let direct process cleanup
  proceed while a stdout consumer is blocked; terminal bash output remains the
  authoritative independently redacted result.

- Direct RPC bash operations can now be cancelled as an abort-all snapshot of
  their current exact operation identities. The restricted command sandbox
  starts each child in a separate process group, terminates and reaps that
  group on explicit abort or timeout, preserves bounded redacted output, and
  distinguishes explicit cancellation from timeout in the correlated result.

- Python embeddings can now reopen one exact durable native product-session
  JSONL file with `open_product_session(...)`. Reopen validates the workspace
  header, strictly rejects malformed or structurally invalid durable records,
  reconstructs the active native context with the explicitly supplied provider,
  and prevents simultaneous same-process public facades for canonical-path and
  symlink aliases. CLI recovery behavior and cross-process coordination are
  unchanged.

- RPC `compact` now runs the native semantic-compaction owner as one idle-only,
  cancellable worker operation, with correlated compaction lifecycle records and
  truthful state/result projection. `set_auto_compaction` now persists the real
  effective policy rather than an RPC-local flag; automatic preflight cuts emit
  threshold or overflow lifecycle reasons. Summary provider output, retry phases
  and usage remain private.

- RPC model and thinking controls now use the native provider-mutation owner.
  Catalog-backed sessions expose available tool-capable selections, switch the
  actual next provider request while idle, and refresh supported thinking levels
  with durable state-first entries. Model switches reset coding history and
  usage; thinking-only refreshes retain them. Static injected providers remain
  truthful singleton fallbacks. Live credentials and provider acceptance remain
  unverified dogfood work.

- RPC now adopts the native coding-session control atomically. Native queue
  state owns prompt/steer/follow-up admission, abort, exact claims and
  settlement; RPC retains LF JSONL framing, correlation, output projection and
  direct bash. The wake channel carries wake/EOF only, literal product content
  remains intact, and promoted runs publish `agent_end`, `queue_update`, then
  the next `agent_start`; protocol idle follows the later true-idle re-poll.

- Ordinary persistent Python product-session submits now use the native coding
  queue's atomic managed-operation claim for the complete submit-to-idle drive,
  including extension settled-hook continuations. The native lifetime settles
  the exact token on every exit, and a once-bound queue signal view replaces the
  facade-owned cancellation slot. Public submit, cancellation, result and close
  semantics are unchanged; no concurrent SDK controls or RPC serialization
  changes are introduced.

- The coding input owner now contains an internal, guarded external-admission
  mechanism. It reserves and claims one exact operation, queues steering before
  follow-ups, atomically promotes settlement, and signals a fresh accepted-abort
  latch outside the queue guard. Ordinary product-session submits now adopt its
  idle-only operation entry; RPC, selectors, extensions, and agent-loop delivery
  remain unchanged.

- Branch-summary tree selection now runs through canonical private provider
  execution with one frozen request and captured bounded retry policy. Retry
  events, deltas, and summary usage stay outside ordinary product projections;
  provider and backoff cancellation settle manual pending input. Selection
  rejects generated text when its original
  tree, leaf, coding history, provider binding, extension generation, or
  publication window changed. Successful selection publishes the tree and coding
  projection coherently, clears branch-bound extension inputs, and durably appends
  the exact accepted entry in order. Append failure preserves accepted live state;
  retained controls invoked by a provider worker do not require a new effect lease.

- Manual and automatic semantic compaction now use the canonical bounded retry
  mechanism for prepared providers. One captured policy and frozen private
  summary request span all logical attempts; retry admission and final acceptance
  validate the original compaction witness, and cancellation or exhaustion
  publishes no summary. Branch summaries keep their existing provider-owned
  behavior.

- Ordinary native product requests now capture retry settings once per request
  and use bounded caller-managed retries for the prepared OpenAI Codex
  capability. Retries reuse the prepared body and headers, preserve prior tool
  effects, stop on provider progress or reported usage, and revalidate the
  original coding context before reissue. Cancellation covers both backoff and
  provider phases; exhausted failures still leave the next prompt usable.
  Other providers, branch summaries, and the compatibility SDK runtime retain
  their existing behavior. RPC now persists exact-boolean `set_auto_retry`
  through the precedence-aware settings owner and can cancel only the exact
  active ordinary retry phase with `abort_retry`; it neither owns a retry latch
  nor exposes retry activity in state.

- Native compaction entries can now durably retain one exact user message plus a
  later tool-cycle suffix. Reopen and fork apply anchored and subsequent cuts in
  effective chronological order, reject invalid or synthetic anchors, strictly
  remap both references, and restore model/thinking settings from full ancestry.
  Known-limit automatic pressure now uses this form when the newest user group
  still does not fit after older whole groups are removed.

- Canonical history compaction now records exact retained and removed message
  objects and can purely select older settled tool cycles after the latest exact
  user while preserving the newest cycle. Guarded acceptance verifies a real,
  identity-preserving removal and permits a truthful zero whole-group count.
  Live known-limit automatic compaction can now activate the cycle selector;
  manual and unknown-limit compaction keep their existing whole-group behavior.

- Terminal native agent results now enumerate the canonical messages appended
  during the accepted run independently of retained provider context. This
  preserves earlier tool cycles for automation and completion consumers when
  within-run context cuts remove them from retained provider context.

- Native requests now use estimated declared/explicit context budgets, including
  system/messages/tools/images, safety and an output reserve. Optional pipy-only
  `compaction.contextWindow` limits the selected model. Known pressure permits one
  safe summary attempt, including older complete tool cycles when needed;
  ordinary hooks run once before final admission.
  Manual and automatic summaries preflight their own input. Invalid budgets and
  final overflow refuse recoverably; `keepRecentTokens` remains inactive and
  estimates do not guarantee provider fit. A persistent first-iteration cut to the
  new user can refuse its not-yet-durable origin; normal settlement then records
  that user once for subsequent operations. Manual `/compact` remains available.

- Native request preparation can now settle a typed recoverable refusal without
  calling the provider or creating an empty assistant message. Product snapshots
  retain the separate failure, earlier tools and usage survive, and the next
  prompt can continue. Bounded archive results expose only a safe classification;
  print mode suppresses stale earlier answers. Model-budget admission now uses this
  same refusal path.
- Python embeddings can now use `create_product_session` with an explicit
  tool-capable provider for persistent native coding: repeated literal prompts,
  real tools, fixed full-content events, immutable idle snapshots, cross-thread
  cancellation and once-only disposal. Workspace preparation retains existing
  instruction loading and fail-closed project trust. Sessions are ephemeral by
  default and create no workflow archive; the one-shot CLI runtime keeps its
  compatibility semantics. Optional diagnostic callbacks receive write fragments.
- RPC active-run `abort` now interrupts model-driven tools through the canonical
  cancellation worker, as well as provider and semantic-summary execution.
  Completed tool effects remain recorded and uncooperative tools retain bounded
  cleanup. The separate direct RPC bash cancellation boundary is unchanged.

- Native coding composition now has an internal persistent lifetime that yields
  at idle and continues with the same conversation and resources. The stream
  entrypoint shares its loop and once-only cleanup.

- Python extension tools can now activate additional registered tools during
  execution with `ctx.set_active_tools(...)`. Purely additive changes persist a
  provider-agnostic load-point marker on that tool result; supported first-party
  Anthropic Claude 4.5+ requests keep the new definitions out of the immediate
  cache prefix with `defer_loading: true` and load them at the result with
  `tool_reference`, while supported OpenAI Responses and OpenAI Codex Responses
  models now load them at the same durable result with completed client
  `tool_search_call`/`tool_search_output` items. Older/custom-disabled models
  and removals safely send the complete current tool list. Kimi Chat
  Completions deferred tools remain a separate follow-on.
- Python extensions can now register Pi-shaped durable entry renderers with
  `api.register_entry_renderer(...)`. `ctx.append_entry(...)` records receive a
  live product-TUI component with full stored-entry metadata plus current
  expanded/width/theme context; startup replay, `/resume`, expansion changes,
  and `/reload` use the same independent registry. Entry renderers stay inert
  in print/JSON/RPC modes, and their output never enters session JSONL,
  provider context, protocol stdout, or the summary-safe archive.
- Python extensions can now observe Pi's payload-free `agent_settled`
  lifecycle hook once a provider/tool run is truly idle. Automatic retry,
  compaction work, and queued steering/follow-up/extension prompts finish
  first; unexpected mid-run failures still settle, and a settled handler may
  schedule a new run without blocking on stdin. JSON/RPC protocol events remain
  mode-owned, so their streams still emit exactly one `agent_settled`.
- Python extensions can now register Pi-shaped `before_provider_headers`
  handlers. Each real HTTP provider request exposes a mutable header map after
  request-scoped assembly; handlers run serially, may add/override values or set
  one to `None` to delete it, and fail soft. Bedrock mutations occur before
  SigV4 signing, while OpenAI Codex retries and WebSocket-to-SSE fallback reuse
  one transformed snapshot without re-firing the hook. Header data remains
  live-only and never enters session archives or JSON/RPC protocol output.
- Project-trust extensions now ship end to end. Before an unresolved decision,
  pipy activates only global and explicit CLI extensions and runs their
  `project_trust` handlers serially; `undecided` continues, the first yes/no
  wins, failures warn and continue, and only exact `remember=True` persists the
  exact cwd. Headless UI choices are inert and notifications remain stderr-only.
  The same activation instances feed provider-catalog construction and the
  initial live session, so module top-level code runs once while project and
  project-package extensions remain gated. Normal extension contexts expose
  zero-argument `is_project_trusted()` and `isProjectTrusted()` run-local reads.
- RPC mode now implements Pi's read-only `get_entries` (including optional
  `since` slicing) and `get_tree` session-inspection commands, bringing pipy's
  green RPC baseline to all 31 Pi command types. Both return a coherent session
  entries/tree and leaf snapshot; deep linear trees use depth-safe iterative
  serialization.
- RPC mode now emits Pi's payload-free `agent_settled` event after the final
  `agent_end` when the session reaches true idle. Queued steer/follow-up runs do
  not emit a premature settled event between runs.
- `--mode json` now also emits Pi's payload-free `agent_settled` as the final
  event after the run's `agent_end`. The one-shot json driver settles into idle
  when the run returns, matching Pi's `_runAgentPrompt` `finally` that `--mode
  json` forwards. The extension-surface hook now ships independently without
  duplicating this protocol event.
- `openai-codex/gpt-5.6-sol` is now a built-in Codex model (372K context, image
  input) with a seventh thinking level, `max`. The thinking vocabulary is now
  `off|minimal|low|medium|high|xhigh|max` across the CLI `--thinking`/`:level`
  suffix, settings, extension controls, RPC, and `models.json`. Shift+Tab
  cycling is model-aware: every reasoning model cycles the ordinary tier and
  appends `xhigh`/`max` only when the active model maps them, so Sol cycles all
  seven levels. The Codex request now clamps an unsupported stored level to the
  nearest supported one and emits it as `reasoning.effort` (matching Pi's
  per-request `clampThinkingLevel`); e.g. a stored `max` sends `effort: "max"`
  on Sol and clamps to `xhigh` on GPT-5.5. Sol renders a `372k` status budget.
  GPT-5.5 remains the Codex default; no bare `gpt-5.6` alias is added.
- Extension UI editor text helpers now expose Pi-canonical camelCase aliases:
  `ctx.ui.getEditorText()`, `ctx.ui.setEditorText(text)`, and
  `ctx.ui.pasteToEditor(text)`. The existing snake_case helpers remain
  available as Python convenience aliases.
- Rich extension custom-message renderers now refresh their existing TUI block when the live tool-output expanded flag changes, matching Pi's `MessageRenderer(..., { expanded })` behavior without persisting rendered rows.
- Live custom editor components now receive Pi-shaped app-action delegation:
  keybinding specs, model/thinking/tool/follow-up handlers, external-editor
  (`app.editor.external`) handoff, Escape/Ctrl-D and paste-image callbacks,
  draft preservation, and Ctrl-C remains on the terminal interrupt path. The
  built-in editor now also opens `$VISUAL`/`$EDITOR` from the resolved
  `app.editor.external` binding, default Ctrl-G, as an undoable edit. The
  default Ctrl-G editor binding is reserved from extension shortcuts; extensions
  that still register Ctrl-G now fail activation as a reserved shortcut.
- Project-trust core now gates project `.pipy` settings and resources before
  startup loads them. Decisions live in owner-private `<config>/trust.json`
  with closest-ancestor lookup; global-only `defaultProjectTrust` accepts
  `ask|always|never`; and `--approve`/`-a` plus `--no-approve`/`-na` override
  one run without persistence (the last flag wins). Untrusted runs retain
  global resources/packages and explicit CLI sources, keep `AGENTS.md`/`pipy.md`
  context behavior, and exclude project settings, extensions, skills,
  templates, commands, system-prompt files, and project package declarations.
  Print/JSON/RPC/help/list-model paths fail closed without trust prompts or
  protocol output.
- Interactive project trust now ships end to end. An unresolved interactive TTY
  opens Pi's five-choice startup selector (current folder, parent, session-only,
  decline, or session-only decline); `/trust` shows saved/inherited and current
  state and persists a next-restart decision without hot-loading resources; and
  `/reload` narrowly saves trust when a previously resource-free trusted run
  explicitly loads a newly created protected input. `/settings` now controls the
  global `defaultProjectTrust` enum. `install`, `remove`/`uninstall`, `list`, and
  `config` accept command-local `--approve`/`--no-approve`; untrusted listings
  omit project entries, global operations remain usable, and local writes fail
  before mutation. Package `update` realignment remains separate.


### Removed

- `pipy.md` and `PIPY.md` are no longer context files. Pi has no equivalent;
  rename them to `AGENTS.md`. Skills without a frontmatter `description` no
  longer load, matching Pi's `loadSkillFromFile`.
- The pipy-only one-shot Python SDK facade has been removed outright. Python
  embedding now uses `create_product_session(...)` or `open_product_session(...)`
  with an explicit provider; `pipy run --agent pipy-native` retains its separate
  compatibility runtime and CLI/archive behavior.

- The pipy-only model-visible `edit_diff` tool and its unified-diff
  implementation have been removed outright, with no alias, compatibility
  dispatch, or deprecation shim. `edit` is now the sole edit tool, matching
  Pi's seven-tool product manifest while retaining its existing path and trust
  policy.
- The pipy-only model-visible `truncate` tool has been removed outright, with
  no alias, compatibility path, or deprecation shim. Read excerpts, `bash`
  output, provider-visible tool results, and rendered previews retain their
  independent automatic bounds.
- The no-tool REPL has been retired. There is now one product REPL — the
  model-visible tool-loop session. The `--repl-mode` flag and the no-tool
  commands `/read`, `/ask-file`, `/propose-file`, and `/apply-proposal` (and
  their archive-side observation/patch-proposal events) are gone; the model uses
  `read`/`edit`/`write`/`bash` directly.
- The `--native-output json` one-shot flag has been removed. Automation callers
  use `pipy repl --mode json` (the full Pi-shaped session event stream) or
  `--print`/`-p` for final-text output; the removed flag now prints guidance
  naming `--mode json`. `pipy run` keeps its default human/exit-code behavior.
- The `--archive-transcript` transcript sidecar has been removed, along with the
  `pipy-session export` `--export-transcript`/`--include-transcript` reader (the
  export schema is bumped v1→v2). The native session tree is the transcript; use
  `/export` (or `pipy --export`). The removed flag prints guidance.
- The pipy-only `/template` wrapper command has been removed. Prompt templates
  are now invokable as their own `/<template-name>` slash commands (matching Pi,
  which has no literal `/template`).
- The pipy-only `/clear`, `/status`, `/help`, and `/theme` slash commands have
  been removed outright, with no deprecation aliases or notices. Pi has none of
  them; use the Pi equivalents `/new`, `/session`, and `/hotkeys`, and select a
  theme from the `/settings` dialog. This follows pipy's no-deprecation policy
  (no users yet, private until Pi parity — see `AGENTS.md`); the brief
  `/clear`→`/new`/`/status`→`/session` deprecated aliases and the `/help`→
  `/hotkeys` alias introduced earlier in this cycle are gone. The
  `--theme`/`--no-themes` load flags and `PIPY_THEME` are unchanged.

### Changed

- The `read` tool now follows Pi's `read` (`4df157433`). It takes optional
  1-indexed `offset` and `limit` parameters, returns up to 2000 lines or
  50 KB, and ends a cut result with Pi's notice, such as
  `[Showing lines 1-512 of 901 (50.0KB limit). Use offset=513 to continue.]`
  or `[4 more lines in file. Use offset=7 to continue.]`. Before, it
  stopped at 200 lines / 8 KB without saying so: on a 901-line CSV the model
  took row 198 for the last row, and automatic compaction kept that wrong
  fact (DF1). A first line over 50 KB points at a `sed | head -c` command,
  and the tool-row header shows the requested range (`read a.csv:513`).
  - Files over 256 KB, non-UTF-8 and binary files, and files with
    secret-shaped content now read like any other file (invalid bytes become
    U+FFFD), as in Pi. `@file` references read through the same tool, so
    they change the same way.
  - Not ported: images (jpg, png, gif, webp, bmp) return an error rather
    than an attachment (READ-IMG), and paths still follow pipy's
    workspace/read-root policy with `.git`, `.gitignore` and generated-file
    refusals (READ2). `offset`/`limit` are integers (Pi: numbers).
- Switching the model (`/model`, Ctrl+P, RPC `set_model`, extension
  `setModel`) now keeps the conversation, as Pi `setModel` does. The next
  request replays the same history to the new model. Before, the switch
  cleared provider-visible history, so the new model answered without any
  earlier context while a later resume of the same session still had it.
  Usage totals still restart per model. Found by the DF1 live dogfooding run.
- OpenAI Responses requests with a thinking level now send Pi's
  `reasoning.summary: "auto"` and ask for the encrypted reasoning item
  (`include: ["reasoning.encrypted_content"]`). This applies to every
  `openai-responses` row (OpenAI, xAI, Copilot, custom); Azure and Codex are
  unchanged. Chat Completions rows whose `compat.supportsReasoningEffort`
  resolves false no longer send `reasoning_effort`, as in Pi.
- `just catalog-drift` also compares each row's static request headers.
- Built-in rows the first catalog drift check flagged now carry Pi's values
  (Pi `4df157433`):
  - Mistral Large, Devstral Medium and Mistral Small: cost, context window and
    max tokens;
  - Gemini 2.5 Pro on Google and Vertex: cost and context window;
  - OpenRouter GPT-5.1 Codex: cost and thinking map;
  - `openai-completions/gpt-4.1`: context window.

  Mistral Small is now a reasoning model. It offers `high`, and off sends
  `reasoning_effort: "none"`. Mistral `reasoning_effort` now follows Pi: a
  level is clamped to the model's levels before it is mapped. OpenRouter GPT-5.1
  Codex offers low, medium and high, and thinking cannot be switched off.
- Session cost now comes from the selected model's catalog row, priced with
  Pi's `calculateCost` (`4df157433`), so Claude and every other priced model
  show a cost. Before, only Codex `gpt-5*` had a hard-coded price. `models.json`
  `cost` overrides apply, and `cost.tiers` (Pi's request-wide long-context
  rates) is accepted on custom models and overrides; built-in rows get tier
  data with the catalog sync. Cached input is no longer charged at the full
  input rate, and reasoning tokens are no longer charged on top of output.
  Gemini thinking tokens now count as output, and cache reads are read from
  Gemini, Chat Completions (OpenAI-compatible, OpenRouter, Cloudflare), Mistral
  and Responses cache-write counters the way Pi reads them.
- The footer cost follows Pi: `$0.123` when there is a cost, nothing at zero,
  and `$0.123 (sub)` on a subscription login (OpenAI Codex, Anthropic or
  GitHub Copilot OAuth, or an extension OAuth provider declaring
  `is_subscription=True`). The `(api)` label is gone. RPC `get_session_stats`
  reports the same token totals and cost instead of zeros.
- Skills, prompt templates, custom commands, global extensions and
  `models.json` now use the same global config root as settings and context
  files: `PIPY_CONFIG_HOME`, `${XDG_CONFIG_HOME}/pipy`, then `~/.pipy` when it
  exists, then `~/.config/pipy`. Before, they skipped `~/.pipy`, so skills in
  `~/.pipy/skills/` never loaded.
- Skill roots follow symlinks like Pi. A skill directory or file symlinked into
  `~/.pipy/skills/`, `~/.agents/skills/`, a `--skill` path, or a trusted
  project's `.pipy/skills/` or `.agents/skills/` now loads from wherever it
  points, and a symlinked skills root is followed too. Template and command
  stores keep their containment guard.
- Context files follow symlinks like Pi, including a link to a file outside
  its directory. Context files load without project trust, so treat them as
  untrusted input (Pi `docs/security.md`).
- Provider defaults now mirror Pi's `defaultModelPerProvider`:

  | Provider | Default |
  |---|---|
  | anthropic | `claude-opus-4-8` |
  | google and google-vertex | `gemini-3.1-pro-preview` |
  | amazon-bedrock | `us.anthropic.claude-opus-4-6-v1` |
  | azure-openai | `gpt-5.4` |
  | openrouter | `moonshotai/kimi-k2.6` |
  | cloudflare | `@cf/moonshotai/kimi-k2.6` |
  | mistral | `devstral-medium-latest` |
  | openai-completions | `gpt-5.5` |

- Thinking now follows Pi's model rules:
  - A new session starts at the settings `defaultThinkingLevel`, or `medium`
    when that is unset, clamped to the startup model. Before, it started
    unset. A model switch clamps the carried level to the new model.
  - A level mapped to `null` is not offered. That includes `off` on models
    that cannot switch thinking off.
  - OpenAI Responses, Azure and Codex clamp an on-state level. An explicit
    `off` sends `reasoning.effort: "none"` (or the row's own off value).
  - Gemini clamps a level before mapping it.
  - Anthropic uses adaptive thinking only for rows with
    `compat.forceAdaptiveThinking: true`. Rows that cannot switch thinking off
    never get `thinking:{type:"disabled"}`.
- Existing catalog rows that Pi still ships now carry Pi's costs, context
  windows and thinking maps. The retired Codex GPT-5.4 row is removed.

- Custom commands, prompt templates, and extension commands can no longer be
  advertised in slash discovery or registered by an extension when their name
  collides with any built-in command. The reserved-name set now covers every
  built-in (`reload`, `tree`, `new`, `fork`, `session`, `compact`, `export`,
  `import`, `clone`, `resume`, `name`, `share`, `trust`, `scoped-models`,
  `hotkeys`, `changelog`, and the previously covered names) rather than a
  hand-maintained subset, closing an advertising gap where such a colliding
  name was still shown even though the built-in always ran instead. Runtime
  command dispatch is unchanged.
- Provider requests now carry an exact request-local advertised-tool snapshot.
  Serial `before_provider_request` tool transforms can only narrow the current
  detached definitions in their prior order, while `ctx.set_active_tools(...)`
  changes later provider iterations. Request construction and extension
  transforms route through a focused product adapter; public request formats
  and callback ordering are unchanged.
- Model-driven tool definition lookup, execution, and policy-error creation now
  flow through the synchronous canonical `AgentToolCapabilities` port. Product
  registry composition, CLI/run filters, active-tool changes, extension reload,
  workspace context, and executor construction live behind
  `NativeToolCapabilities`; scheduling remains sequential and public formats,
  extension ordering, persistence, and archive privacy are unchanged.
- Canonical agent-history compaction now lives in the dependency-neutral
  `native.agent.history` layer. The obsolete mixed
  `native.session_compaction` module and its unused no-tool compaction path are
  removed; product trigger policy, exact summary text, extension ordering, and
  durable session-tree writes remain owned by the native tool-loop session.
- Extension custom footers now receive live product-TUI `FooterData.onBranchChange(...)` callbacks that rebuild/repaint the footer on git branch changes; headless snapshots keep the safe no-op disposer.
- The native `google-generative-ai` provider now injects Pi's per-model
  `generationConfig.thinkingConfig`: a `thinkingLevel` enum (Gemini 3 Pro/Flash,
  Gemma 4) or a `thinkingBudget` token count (Gemini 2.5 family) with
  `includeThoughts: true` when thinking is on, and a per-model disabled config
  (no `includeThoughts`) when a reasoning-capable model runs with thinking
  off/unset — matching Pi's `google.ts` `streamSimpleGoogle`/`buildParams`.
  Non-reasoning models still omit `thinkingConfig` entirely.
- The native `google-vertex` provider now injects Pi's per-model
  `generationConfig.thinkingConfig` from `google-vertex.ts` (its
  `THINKING_LEVEL_MAP` variant) in both Express (api-key) and ADC (bearer) modes:
  a `thinkingLevel` enum for Gemini 3 Pro/Flash, a `thinkingBudget` token count
  otherwise (`includeThoughts: true` when on), and a per-model disabled config
  (no `includeThoughts`) when a reasoning-capable model runs with thinking
  off/unset. Catalog construction forwards the resolved
  `reasoning_effort`/`thinking_disabled`. It deliberately diverges from
  `google-generative-ai`: **no** `2.5-flash-lite` budget table (flash-lite falls
  into the `2.5-flash` branch → minimal `128`, not `512`) and **no** Gemma 4
  special-casing (Gemma is not a Vertex Gemini model).
- The native `azure-openai` provider now resolves its endpoint and deployment
  from Pi's config-source env vars: `AZURE_OPENAI_BASE_URL` (the base URL),
  `AZURE_OPENAI_RESOURCE_NAME` (used to build a default
  `https://{name}.openai.azure.com/openai/v1` base when no base URL is set),
  `AZURE_OPENAI_DEPLOYMENT_NAME_MAP` (a `modelId=deployment,...` map overriding
  the deployment name per model id), and the existing `AZURE_OPENAI_API_VERSION`
  — matching Pi's `resolveAzureConfig`/`resolveDeploymentName` precedence. The
  pipy-only `AZURE_OPENAI_ENDPOINT` env name was dropped for parity; provider
  availability now requires `AZURE_OPENAI_API_KEY` plus one of
  `AZURE_OPENAI_BASE_URL`/`AZURE_OPENAI_RESOURCE_NAME`.
- The native `anthropic-messages` provider now emits Pi's explicit
  `thinking: {type: "disabled"}` when a reasoning-capable Claude model runs with
  thinking off/unset, instead of omitting the `thinking` key — matching Pi's
  product path (`streamSimpleAnthropic` → `buildParams` `thinkingEnabled ===
  false`). Non-reasoning models still omit `thinking` entirely, and the
  `amazon-bedrock` adapter is unchanged (Pi omits thinking fields there rather
  than sending a disabled shape).
- Bare `pipy` and `pipy "<prompt>"` now launch the interactive product session
  like Pi (a bare positional prompt seeds the first message), while
  `auth`/`run`/`repl`/`config`/`install`/... stay reachable as subcommands. A
  bare token equal to a subcommand name dispatches that subcommand; quote it via
  `pipy repl "<word>"` or `pipy -p "<word>"` to send it as a prompt instead.

### Fixed

- An installed copy (`uv tool install .`) now ships `CHANGELOG.md` in the
  wheel, so `/changelog` and the startup "What's New" notice work outside a
  checkout. Before, both found no entries.
- Found by the DF1 live dogfooding run (`docs/acceptance/2026-09-29-df1-dogfooding.md`):
  - The `edit` and `write` diffs no longer corrupt the interactive screen. They
    were written raw to the terminal mid-turn, in raw mode, so the diff
    staircased across the editor and footer rows. They now render as a
    transcript row under the tool call.
  - Tool-call rows show `$ <command> (timeout Ns)` for `bash`,
    `$ edit <path>` and `$ write <path>` instead of an argument dump such as
    `$ bash(command="…", timeout=120)`. A `!` shell command no longer shows
    as `$ $ <command>`.
  - Escape and Ctrl-C on an OpenAI Codex WebSocket stream no longer freeze
    the frame for about three seconds. Cancellation now shuts the socket down
    before the WebSocket close handshake, so `Operation aborted` shows at once.
  - A refused `/model` switch (for example to the non-tool `fake` provider)
    no longer changes the footer's thinking level. It had published the
    refused target's level (`/model fake` showed `thinking off`) while
    requests kept the old one.
- `pipy repl --native-provider fake --native-model fake-native-bootstrap` no
  longer ends the first turn with `ProviderResult.model_id must match the
  request`. The REPL runs any `fake` selection on `fake-tools`, but it stamped
  requests with the raw `--native-model` flag. The REPL now takes the provider
  and model only from its resolved selection.
- The Pi comparison gates (`scripts/parity_checks/automation_pi_comparison.py`
  and `session_tree_pi_comparison.py`) run again. pi-mono dropped `tsx`, so
  both drivers now run from source with `node` and pi-mono's source resolver,
  as pi-mono's own scripts do (Node 22.19 or later). A missing Pi reference
  now reports `passed: false` and exits 2 instead of passing.
- Shift+Tab and the `/settings` "cycle thinking level" row now rebuild the
  provider with the new level. Before, the footer showed the new level while
  requests kept the effort the provider was built with.
- The footer's thinking segment follows Pi. A reasoning model shows its live
  level, or `thinking off`; a non-reasoning model shows none. The hard-coded
  `high` (Codex GPT-5) and `default` labels are gone.
- Thinking-level changes reach the session file in the order they took effect,
  including a level clamped by an RPC model switch.

- Seven conformance gates in `scripts/parity_checks/` had gone stale against
  refactors and crashed or failed before checking the product:
  `extension_package`, `project_trust`, `extension_chrome_widgets`,
  `session_tree`, `automation_rpc`, `settings_config` and `tui_workflow`.
  They run and pass again.
- Codex GPT-5.6 Sol now reports Pi's 272K context window, in the catalog
  and in the footer meter. It previously reported 372K. The footer meter now
  reads every built-in row's context window, formatted like Pi's footer.
  Claude Sonnet 4.5/5.x show `1.0M`; the old hardcoded value was `200k`.
- Shift+Tab no longer fails on a model that does not offer `off`.

- Cross-provider product-session reopens now project each durable tool-call and
  result pair to matching target-safe provider wire IDs while retaining raw
  canonical history and JSONL.

- The legacy score test now uses its installed interpreter directly under its
  isolated home, avoiding nested `uv run` calls that could change the shared
  development environment. Explicit `PIPY_PARITY_PYTHON` mode preflights Python,
  Pytest and both installed console scripts before running the real score checks.

- Compaction now generates a semantic summary of older whole-user groups and the
  prior summary using the current provider. Retained tool exchanges remain
  verbatim. Failed, cancelled or stale generation cannot publish a summary;
  automatic cancellation stops before the ordinary request, while stale automatic
  context closes the session safely. Summary output stays private and off the
  transcript. Even label/name/custom-message writes during automatic summary
  generation invalidate it and close the stale session. Summary tokens and cost
  are not yet included in session totals. Live-provider summary quality remains
  unverified. Branch summaries also retain one coherent provider binding when
  header callbacks change the active model. Manual `/compact` now settles queued
  input when summary work ends: steering and follow-up remain deliverable, while
  Escape/Ctrl-C restores pending text to the editor for explicit submission.
- Retained extension model controls can no longer let an older coding run restore
  discarded history, append messages to the replacement context, or charge its
  usage accumulator. A context change stops the stale session lifetime through
  existing exceptional cleanup. Accepted automatic compaction remains visible
  to the same request and subsequent tool-loop continuation.
- Repeated compaction now persists the exact retained boundary even after only
  one additional user group. Resume and branch replacement restore the destination
  summary separately from conversation groups, including duplicate custom/branch
  messages, while preserving current-run counters. Unmapped durable cuts refuse
  before mutation; accepted writes retain state-first failure behavior. Summary
  generation now uses the canonical semantic-summary path.
- `--mode rpc` `get_state` and `set_model` responses no longer report a
  fabricated `fake`/`fake-tools` provider selection when the session adapter is
  built without a provider. The underlying `ValueError` now reaches the RPC
  client as a real command error response, so a misconfigured session is no
  longer indistinguishable from a working one over the protocol.
- Package-manager git ref validation now rejects a malformed percent-escape
  outright instead of silently decoding it to replacement characters and
  validating the mangled result.

- Concurrent extension coding-session controls now serialize complete provider,
  durable session-tree, custom-render, and queued-input effects. Native tree
  id/parent selection, in-memory publication, labels/names, RPC snapshots, and
  JSONL append order now share one guard, as do all coding-input check/use paths.
  Terminal shutdown waits for effects accepted before close, then detaches the
  live generation and closes its outboxes/chrome exactly once; later completion,
  custom-entry, name, label, and custom-message calls raise
  `ExtensionCapabilityError` without changing provider/session/input state,
  while read-only final-tree views remain available.
- A live extension message can no longer be erased when
  `api.send_user_message(...)` or `api.send_message(...)` races the session's
  outbox drain. Live append and detach now serialize; rejected or retired queue
  handles silently keep the existing `None` return and accumulate nothing.
- Extension reload no longer clears live retained TUI chrome before activation
  and dynamic-flag validation succeed. Invalid flags or another rejected
  candidate now preserve the prior title, header/footer/widgets, indicator,
  terminal-input listeners, autocomplete providers, editor component, and
  hidden-thinking label; rejected candidate chrome never paints and no longer
  re-fires the retained generation's `session_start`, which previously appended
  duplicate registrations and rebuilt its editor. A reload now invokes exactly
  one replacement-generation `session_start` against the candidate before
  acceptance and before accepted staged custom messages become visible, then
  publishes one coherent flags/tools/renderers/hooks/providers/menu/chrome
  generation. The publication gate remains active through
  accepted staged delivery, two-phase route release, and gate drain—even while
  those paths invoke extension-visible sinks after the session commit unlocks—
  before chrome reconciliation. Retained active-tool and thinking controls now
  stay bound to their creating generation: stale, publication-pending, and
  post-run calls return `False` without changing tool visibility, thinking
  state, the session tree, persisted JSONL, or the footer. Thinking commits and
  durable entries also preserve one order under concurrent callers. Retained
  model controls now use the same generation, publication-gate, and terminal
  refusal boundary: provider/catalog construction is prepared outside the
  session mutex, the selection plus coding provider/history/usage commit is
  atomic under it, and footer/default persistence follows after unlock. A stale,
  gated, terminal, failed-construction, or superseded prepared model returns
  `False` without rebinding or persisting; a callable first released after
  teardown cannot construct a provider. If lifecycle, provider/catalog, or
  final chrome preparation then refuses the reload,
  non-staged, non-chrome lifecycle effects such as `notify` may already have
  occurred; candidate chrome is discarded and all candidate staged messages
  that the earlier reload path could expose are suppressed. This is part of the
  `session_start`-before-acceptance ordering change, not another behavior delta.
  Registrations it does not rebuild
  (including autocomplete, a custom editor, and the hidden-thinking label) are
  cleared, while custom-editor text returns to the built-in editor. Late
  retained writes to a rejected candidate's closed sink are ignored with their
  prior `None` return shape, while `on_terminal_input()` returns an inert
  disposer. Concurrent owner rotation during candidate-host publication now
  refuses before any generation/provider/tool/renderer write, preserving the
  newer live state while retiring the terminal candidate route and closing its
  chrome. Concurrent retained writes still cross accepted chrome reconciliation
  through a short effect-free ownership handoff; post-publication interrupts
  propagate after that reconciliation leaves the replacement explicitly live,
  without double close. Ordinary retained UI contexts now stay bound to their
  originating chrome handle, so writes through a retired handle are ignored
  instead of retargeting the replacement; terminal teardown invocation remains
  a separate follow-on.
- Extension activation APIs retained after successful activation or candidate
  rejection can no longer appear to register commands, shortcuts, hooks, tools,
  providers, flags, or renderers into dead candidate state. Late contribution
  registration now raises `ExtensionCapabilityError` at the call boundary,
  `(str, Enum)` names and hostile `str.__str__` overrides retain their exact
  underlying registration and `get_flag(...)` lookup value, and messages racing
  after the activation seal
  can no longer leak into (or mutate) the accepted frozen activation snapshot.
  Unexpected extension-controlled normalization/copy exceptions now fail closed
  to the registration family's bounded invalid reason and type-only diagnostic;
  the first failure remains recorded even when extension code catches the raised
  validation error, so this hostile case does not preserve exact pre-R1 reason
  behavior. Provider-only catalog scans now terminally finalize accepted hosts:
  staging, sends, and publication are inert, while guarded registration-time
  default flag reads stay available to detached provider factories that captured
  `api`; the catalog helper does not parse/apply CLI tokens, while live session
  activation still exposes parsed overrides. Rejected or abandoned activation
  disposal still clears flag values.
- Calling `unregister_provider(...)` with a non-string, empty/whitespace-only,
  or slash-containing provider name now records an `invalid_provider` activation
  failure and disables that extension, instead of silently staging or ignoring
  the malformed unregistration request.
- Escaped adapter exceptions no longer copy raw exception messages into the
  durable metadata archive or its Markdown summary. Those records retain only
  the bounded exception type and fixed lifecycle metadata, while the in-memory
  `RunResult` keeps its existing caller-facing failure detail.
- Registered tools omitted from the exact provider request can no longer reach
  extension tool hooks or execution when a provider returns them anyway. They
  now produce the normal balanced, budget-consuming `unknown tool` result with
  provider correlation and pipy request identities intact, without incrementing
  malformed-call or real-invocation counters or invoking extension custom
  renderers. A tool activated by an earlier call is not authorized later in the
  same provider response.
- Extension `deliverAs=nextTurn` custom context is now an identity-anchored,
  request-only overlay for exactly the next accepted run. It remains visible
  through every provider/tool iteration without entering canonical history or
  run results, hook prompt transforms cannot confuse it with equal text, and
  automatic compaction continues safely on durable history during the run.
- Valid model-selected tool executions that return error observations (for
  example read failures or timed-out shell commands) no longer count as
  malformed provider tool calls, so the product tool loop keeps feeding those
  observations back to the model instead of aborting after three such errors.
- Terminal color detection now uses truecolor RGB styling only when the
  terminal explicitly advertises it (`COLORTERM=truecolor`/`24bit` or a
  direct-color `TERM`), so ordinary `*-256color` sessions use Pi's 256-color
  fallback palette instead of displaying wrong chrome colors.
- Slash/local commands such as `/quit` now remain editable and submittable while
  a `!` shell shortcut or model-driven bash tool is streaming output, so
  long-running tests no longer trap the user in the product TUI.
- Raw terminal input now preserves UTF-8 prompt text in the product TUI and
  slash-menu editor, so non-ASCII characters such as `ö` no longer render as
  replacement characters or reach the provider corrupted.
- The interactive TUI now paints edge-to-edge at the true terminal width,
  matching Pi, removing the blank right-hand column. Full-row elements — the
  user-message and tool/bash background bands, the input-frame separators, and
  the bottom status line — now reach the final column instead of stopping one
  short. The input line keeps its one-column cursor-safety margin internally, so
  the hardware cursor still never lands in the last column.

### Changed

- Product TUI long editable prompts now soft-wrap inside the input frame instead
  of horizontally scrolling in one row. Cursor movement maps across wrapped
  rows, footer/status rows stay pinned, and long typed/pasted input plus resize
  are covered by real-PTY tests at 80x24 and 100x40.
- The pipy-only metadata-only `--resume RECORD` / `--branch LABEL` repl flags
  are retired: the native session tree is the product session source. The
  separate `pipy-session resume-info` archive utility is unchanged.

### Added

- Provider/model user documentation now covers listing models, provider/model
  selection, credentials, `models.json`, ds4, thinking/images metadata, and
  current provider follow-ons.
- Settings and keybindings user documentation now covers pipy's global/project
  settings files, reload workflow, common Pi-shaped fields and pipy
  divergences, key syntax, namespaced action ids, defaults, and customization
  examples.
- Session and compaction user documentation now covers the native product
  session tree, startup flags, `/session`/`/resume`/`/tree`/`/fork`/`/clone`,
  durable compaction, export/share pointers, and the separate `pipy-session`
  metadata/catalog utility.
- feat(extension-api): editor text helpers for command/shortcut contexts.
  Extensions can read the core prompt buffer, replace it, or paste literal text
  at the current cursor via `ctx.ui.get_editor_text()`,
  `ctx.ui.set_editor_text(text)`, and `ctx.ui.paste_to_editor(text)`. Headless
  reads return `""`; headless writes and pastes no-op deterministically.
- feat(extension-api): theme controls for command/shortcut contexts (rich-UI
  item E). Extensions can read and switch the chrome theme via `ctx.ui.theme`
  (the active `ChromePalette`), `ctx.ui.get_all_themes()` (`{"name", "path"}`
  per available theme, default first), `ctx.ui.get_theme(name)` (load a palette
  without switching; `None` when unknown), and
  `ctx.ui.set_theme(name_or_palette)` (`{"success", "error"}`), mirroring Pi's
  `theme`/`getAllThemes`/`getTheme`/`setTheme`. Reads are ambient (the global
  package theme registry plus `PIPY_THEME`/the chrome store) and work
  deterministically even headless; `set_theme` requires a live UI and returns
  `{"success": False, "error": "UI not available"}` headless without mutating
  process state, while a live call reuses the `/settings` `select_theme`
  mechanism so the next frame repaints. `get_all_themes()` keeps Pi's
  `{name, path}` shape but `path` is always `None` (the session theme registry
  retains only `name -> palette`; package theme file paths are not exposed to
  extension code).
- feat(extension-api): rich message renderers — slice C. A
  `register_message_renderer` renderer may now return a themed component via a
  required second `(data, ctx)` parameter (a `MessageRenderContext` with
  `custom_type`/`data`/`expanded`/`width`/`theme`); the component is committed
  SGR-preserving with no forced `[custom_type]` label, render-once at the
  append-time width, and fail-soft to the plain path. A 1-arg `renderer(data)`
  (including the capture-default idiom) keeps its existing plain-text behavior;
  the context parameter must be required (`def render(data, ctx)`, not
  `ctx=None`). The rendered body is live-only and never archived. Active-branch
  custom entries now replay into startup-opened TUI sessions, including
  `--session`/`--continue`/`--resume-session` opens, without mutating the
  session file. `ctx.send_message` / persisted `CustomMessageEntry` values
  now render through registered message renderers when present, receiving a
  Pi-shaped payload with `customType`, `content`, `display`, and `details`,
  while no-renderer cases continue to display stored content. Deferred:
  streaming `deliverAs`/`triggerTurn` follow-ons beyond the shipped idle and
  queued paths.
- feat(extension-api): persistent chrome widgets (set_widget/set_header/
  set_footer/set_title/set_working_indicator) — slice B. Extensions can pin an
  above/below-editor widget, an exclusive custom header and footer (with git
  branch via `FooterData`), the terminal title, and a custom working indicator;
  chrome re-renders width-reactively, falls back fail-soft, disposes components
  on replace/clear/reload, and renders live from `session_start` in an
  interactive TTY.
- Extensions can render their own tool call/result rows (`render_call`/
  `render_result`) with themed color.
- Discovered skills are now advertised in the tool-loop system prompt when the
  `read` tool is available, matching Pi's skill model: each skill contributes an
  `<available_skills>` entry (name, description, and absolute location), and the
  model loads a skill's body on demand via the `read` tool. Each skill's parent
  directory is added to the read-only reference roots so the model can read skill
  bodies (including global skills outside the workspace). The `/skill` command is
  kept, and the archive-safe skill metadata (path label, sha256, byte length,
  truncated, name) is unchanged.
- Theme selection now lives in the `/settings` dialog as a theme row + picker
  (matching Pi, which has no `/theme` command); the chosen theme persists through
  settings and re-colors the chrome on the next frame.
- Python extensions can now register custom session-entry renderers with
  `api.register_message_renderer(...)`; command and shortcut handlers can call
  `ctx.append_entry(...)` to persist JSON-safe `custom` entries in the native
  product session tree and render them in the product TUI or captured-stream
  diagnostics without starting a provider turn.
- Python extension command/shortcut contexts now expose simple Pi-shaped UI
  primitives: `ctx.ui.select`, `ctx.ui.input`, `ctx.ui.confirm`,
  `ctx.ui.set_status`, `ctx.ui.set_working_message`, and
  `ctx.ui.set_working_visible`. Interactive product-TUI runs use simple
  overlays, live status rows, and sticky provider-turn working controls;
  headless runs return cancel/default values without blocking.
- Python extensions can now register dynamic `pipy repl` tool-loop CLI flags
  with `ExtensionFlag`; parsed values are available to extension commands,
  shortcuts, hooks, and tools through `ctx.flags`.
- Python extensions can now participate in live product-session operations:
  `user_bash` hooks may block, rewrite, exclude, or synthesize `!`/`!!` shell
  shortcut results; `before_provider_request` hooks may transform bounded
  provider request fields and narrow model-visible tools for the current
  request; `session_before_switch`, `session_before_fork`,
  `session_before_compact`, and `session_before_tree` hooks may gate stateful
  session operations; and safe command/shortcut/pre-turn contexts expose
  `ctx.set_active_tools(...)`, `ctx.set_model(...)`, and
  `ctx.set_thinking_level(...)` through the native provider/session/tool
  boundaries. The new live-session parity gate is
  `scripts/parity_checks/extension_live_session_conformance.py --json`.
- Pi-shaped per-run source-loading flags for `pipy repl`: `--extension`/`-e`,
  `--no-extensions`/`-ne`, `--skill`, `--no-skills`/`-ns`,
  `--prompt-template`, `--no-prompt-templates`/`-np`, `--theme`, and
  `--no-themes`. Explicit CLI paths are temporary session sources that load
  before workspace/global/package defaults, survive matching `--no-*`
  discovery cutoffs, and override persisted `+/-pattern` resource filters while
  keeping `enable_skill_commands=false` as a hard skill-command disable.
- Native product export/import/share and self-update planning:
  - `/export` writes a self-contained HTML export of the full native session
    tree; `/export <path.jsonl>` writes the active branch as a linearly
    re-chained portable JSONL file.
  - `/import <path.jsonl>` copies a portable JSONL file into the native session
    store and resumes it after confirmation; `--yes` is accepted for
    noninteractive command scripts.
  - `pipy --export <session.jsonl> [output.html]` exports an existing native
    session file to HTML and exits.
  - `/share` uploads the HTML export as a secret GitHub gist through a stdlib
    GitHub API boundary using `GITHUB_TOKEN`/`GH_TOKEN` or `gh auth token`.
  - `pipy update self|pipy [--force] [--dry-run]` plans install-method-aware
    self-update commands for `uv tool`, `pipx`, `pip`, and user `pip`, while
    unknown/development installs and unconfigured package names fail safe with
    manual instructions.
  - New gate:
    `scripts/parity_checks/export_distribution_conformance.py --json`.
- Pi-style extension **package manager CLI** for local-path and managed git
  package sources ([docs/extension-api.md](docs/extension-api.md)): `pipy
  install <source> [-l]`, `pipy remove`/`pipy uninstall <source> [-l]`, and
  `pipy list` record and report package sources in a `packages` array in user
  `<config>/settings.json` or project `<cwd>/.pipy/settings.json` (with `-l`),
  preserving object-form `{source, ...}` entries. Supported git sources clone
  into pipy's managed package cache (`<config>/git` for user scope,
  `<cwd>/.pipy/git` for project scope), `pipy update --extensions`, `pipy
  update --extension <source>`, `pipy update <source>`, and bare `pipy update`
  refresh managed git packages through bounded fetch/reset, and local-path
  package updates are skipped as no-ops. `pipy config <enable|disable>
  <skill|prompt|theme|extension> <name>` writes Pi-shaped `+pattern`/`-pattern`
  resource filters without deleting discovered resources. PyPI/npm,
  `git+...`, credentialed URL userinfo, and ambiguous unsupported remote
  schemes fail closed; a missing path fails closed, removing an unconfigured
  source exits non-zero, a corrupt settings file is never overwritten, and no
  package lifecycle scripts run.
- Pi-style extension **package runtime composition**: installed local-path and
  managed git packages now contribute skills, prompts, themes, and Python
  extensions to a session through discovery
  ([docs/extension-api.md](docs/extension-api.md)). A package declares its
  resources in an optional `pipy-package.toml [resources]` table (mapping Pi's
  `pi.{extensions,skills,prompts,themes}`) or via convention subdirectories.
  Contributed resources are discovered at lowest precedence (a workspace/global
  resource wins a name collision), are name-deduped first-wins, and honor both
  the global `pipy config` `+pattern`/`-pattern` filters and a package's own
  object-form `{source, skills, prompts, themes}` filters. Runtime startup never
  clones or fetches git sources; it only reads already installed cache paths and
  preserves user/project cache scope when resolving configured git packages.
  This adds file-based chrome themes: a package theme `.toml` becomes
  selectable with `/theme <name>` and re-colors the chrome. `pipy config` lists
  package-contributed resources, and `/reload` re-discovers them. Package source
  paths and resource bodies never enter the default metadata archive. Remote
  PyPI/npm package installation remains deferred pending a broader supply-chain
  policy. See the example package `docs/examples/packages/demo-pack/`.
- Pi-style session startup flags and an interactive session picker for the
  native product session tree ([docs/session-tree.md](docs/session-tree.md)):
  - new startup flags `--session-id <id>` (open the native session with this
    exact id, or create one carrying it), `--session-dir <dir>` (native session
    store root override — the separate `$PIPY_SESSION_DIR` metadata-archive root
    is never reused for it), and `-n`/`--name <name>` (name the session at
    startup), alongside the existing `-c`/`-r`/`--session`/`--fork`/
    `--no-session`.
  - Pi mutual-exclusion errors: `--fork` and `--session-id` each conflict with
    `--session`/`--continue`/`--resume-session`/`--no-session`.
  - cross-project `--session <partial-id>`: a partial id that matches only a
    session in a different project prompts to fork it into the current
    workspace, aborting cleanly if declined.
  - `/resume` opens an interactive picker overlay on a TTY — type to search,
    `Tab` toggles current-project/all-projects scope, `Ctrl+P` the path column,
    `Ctrl+S` the sort, `Ctrl+N` named-only, `Ctrl+R` renames, `Ctrl+X` deletes
    after a `[y/N]` confirmation (the active session is protected), Enter opens,
    `Esc`/`Ctrl+C`/`Ctrl+D` cancel. It renders inline (no alternate screen),
    repaints on resize, runs no provider turn, and sanitizes user-controlled
    names/paths against terminal escape injection. `-r` opens the same picker at
    startup on a TTY; a non-TTY stream keeps the deterministic listing plus the
    `named`/`rename`/`delete --yes` subcommands and continues the most recent
    session.
  - a Pi comparison gate (`scripts/parity_checks/session_tree_pi_comparison.py
    --json`) runs the canonical tree workflow against Pi's real `SessionManager`
    and asserts matching name, branch/leaf chains, fork semantics, and durable
    reconstruction; the extended `session_tree_conformance.py` proves the new
    flags and picker rows/actions through the product paths.
- Pi-style headless automation surfaces for the product tool loop, through
  pipy-owned stdlib boundaries with no new runtime dependency
  ([docs/automation-rpc.md](docs/automation-rpc.md)):
  - `pipy repl --mode json "<prompt>"` runs one non-interactive turn and emits
    the native session header line followed by the full Pi-shaped session event
    stream (`agent_start`/`turn_start`/`message_start`/`message_update` with a
    `text_delta` `assistantMessageEvent`/`message_end`/`turn_end`/`agent_end`
    and `tool_execution_*`) as strict LF-only JSONL on stdout; diagnostics stay
    on stderr. Full assistant/tool/bash content is emitted like Pi; auth
    secrets/tokens are never emitted.
  - `pipy repl --print`/`-p "<prompt>"` prints only the final assistant text to
    stdout (Pi `-p`); failures go to stderr with a non-zero exit.
  - `pipy repl --mode rpc` starts a long-lived stdin/stdout JSONL protocol with
    Pi's command names: async `prompt` (correlated success then streamed
    events); `steer`/`follow_up` (queued during an active run and delivered as
    the next run after it settles, one message per turn boundary
    steering-then-follow-up, each observable via `queue_update` and counted in
    `pendingMessageCount`) and `abort` (cancels the active
    run; queued steering for that run is discarded) — a documented pipy boundary
    over Pi's in-turn injection; `bash` (on a worker thread; `abort_bash` errors
    while a sandboxed bash is in flight rather than falsely claiming a cancel);
    `get_state`/`get_messages`/`get_session_stats`/
    `get_last_assistant_text`, `set_session_name`, and queue-mode commands;
    model/thinking commands are accepted and reflected in `get_state`/events but
    do not yet switch the live provider or thread the thinking level into the
    running provider request (a documented follow-on); and well-formed error
    responses for unimplemented commands. All 29 Pi RPC command types are
    accepted; unknown commands and unparseable lines return well-formed error
    responses, never a crash. The native session
    tree is the introspection source; events derive from the real tool-loop
    run, not a parallel model.
  - The legacy metadata-only `--native-output json` on `pipy run` is deprecated
    in favor of `--mode json`; its `--help` now points there.
  - The session event grammar matches Pi's: after `turn_start` the user
    message emits its own `message_start`/`message_end` pair before the
    assistant message begins.
  - Gated by `scripts/parity_checks/automation_rpc_conformance.py --json` and
    `tests/test_native_automation_*.py`, plus a deterministic Pi-vs-pipy
    comparison (`scripts/parity_checks/automation_pi_comparison.py --json` with
    `scripts/parity_checks/pi_faux_event_driver.mts`) that drives the real local
    Pi and pipy with offline providers and asserts matching normalized event
    order/discriminators, assistant text + delta concatenation, `agent_end`
    semantics, and durable session-tree reconstruction.
- Pi-style interactive TUI/editor workflow depth for the product tool-loop
  terminal (`pipy repl --agent pipy-native --repl-mode tool-loop`), all through
  pipy-owned stdlib boundaries with no new runtime dependency and the inline
  (no-alternate-screen) contract preserved:
  - `@` file picker with Pi exact/prefix/substring ranking (not fuzzy) over a
    bounded, `.git`/ignored-aware workspace walk, and general Tab path
    completion (prefix-match, dirs-first, `~/` expansion, space-quoting) that is
    a no-op in prose.
  - Local `!`/`!!` shell shortcuts reusing the real bash execution boundary,
    with a bash-mode input affordance, context (`!`) vs no-context (`!!`)
    recording, and Escape cancellation of a running command.
  - `Shift+Tab` thinking-level cycling (off→minimal→low→medium→high, clamped to
    model reasoning support, recorded as a `thinking_level_change` native-tree
    entry) and `Ctrl+P`/`Shift+Ctrl+P` model cycling over the scoped/available
    set.
  - `Ctrl+O` tool-output expansion and `Ctrl+T` thinking-block fold as renderer
    view flags (the thinking fold persisted to `hideThinkingBlock`).
  - Queued steering / follow-up during active turns (`Alt+Enter` follow-up,
    `Alt+Up` restore-to-editor), a pending-messages region, steering-then-
    follow-up drain order, and steering interruption via the existing cancel
    token.
  - Clipboard image paste (`Ctrl+V`, owner-only temp file under an image
    reference root) and terminal drag-drop file references; image bytes never
    reach the metadata archive.
  - A `/scoped-models` multi-select overlay defining the Ctrl+P cycle set, new
    `/settings` actionable rows (tool-output/thinking folds, thinking-level
    cycle, scoped models), and startup hints + `/hotkeys` advertising every
    binding.
  - The terminal-native mouse-selection invariant: the renderer never enables
    xterm mouse tracking, so click-drag selection over scrollback keeps working.
  - New gate `scripts/parity_checks/tui_workflow_conformance.py --json` drives
    the real product PTY path and proves all of the above (plus non-TTY
    fallbacks and archive privacy) deterministically.
- User-facing terminal setup and tmux setup docs now cover pipy's inline TUI,
  modified-key expectations, bracketed paste, file/image drops, clipboard
  behavior, scrollback, and common platform caveats.

### Fixed

- A `/…` slash command or `!…` bash shortcut submitted with Enter mid-turn now
  runs locally (matching Pi's editor `onSubmit`): it interrupts the turn and
  dispatches through the normal local-command path instead of being steered to
  the model. Only ordinary prose becomes a steering message, so the queue lanes
  hold prompt text exclusively.
- Queued steering/follow-up messages that begin with `/` or `!` (including an
  `Alt+Enter` follow-up or an RPC `steer`/`follow_up`) now reach the model
  verbatim when the queue drains.
  Previously a queued line starting with a slash-command or `!`-shell prefix was
  re-interpreted as a local command on delivery and silently dropped from the
  conversation; drained messages are provider-visible prompt text and bypass
  local-command dispatch (they still resolve any `@file`/`@image` references).
- Moving the caret (`←`/`→`/`Home`/`End`) now dismisses the `@`/path completion
  popup. Previously the popup stayed anchored to the caret offset where it
  opened, so accepting after a move spliced the candidate at a stale offset and
  duplicated/corrupted the active token; it reopens on the next edit.
- Aborting (Escape/Ctrl-C) or restoring (`Alt+Up`) while a queued turn is
  draining now brings the remaining queued prompts back to the editor. Once a
  turn settled (or steering promoted), the queue moved into an internal drain
  that the restore path ignored, so the not-yet-delivered prompts stayed hidden
  and kept auto-submitting to the model after the cancellation; they are now
  restored along with the steering/follow-up lanes.
- `Ctrl+V` clipboard-image reads are bounded and isolated: the helper's stdin is
  `/dev/null` and the read enforces a wall-clock deadline, so a misbehaving
  clipboard tool (one that hangs or never closes its output) can no longer
  freeze the editor or consume terminal keystrokes.
- Tab path completion no longer offers ignored/generated entries (e.g.
  `node_modules/`) or symlinks escaping the workspace for workspace-relative
  directories, matching the `@` picker and the read policy; explicit
  absolute/`~/` navigation the user points Tab at is still listed as-is.
- OpenAI-Codex streams now use a configurable 300-second header/body idle
  timeout by default instead of the former hard-coded 60-second socket timeout.
  Recognized connection, timeout, reset, and truncated-stream failures become
  sanitized provider failures, while deliberate cancellation remains immediate
  and non-retryable.
- OpenAI-Codex now retries bounded transient HTTP and transport failures across
  the complete request-plus-stream attempt, with cancellation-aware exponential
  backoff and capped `Retry-After` support. Retries stop before replay once any
  provider event is parsed, preventing duplicate visible text, reasoning, tool
  calls, or tool effects.
- OpenAI-Codex now honors `transport: auto|sse|websocket` with a real Responses
  WebSocket path, Pi-shaped pre-event SSE fallback, WebSocket connect timeout
  settings, auto-mode fallback memory, and no post-event fallback or replay.
- The parity runner now records child-attempt start/finish events, distinguishes
  runner timeouts from signal exits, and narrowly retries a legacy raw
  `pipy: The read operation timed out` child tail only when no branch, HEAD,
  ref, or worktree progress occurred.

## [0.1.0] - 2026-06-03

### Added

- Pi-style settings/config/keybindings system for the native runtime:
  - Layered `settings.json` (global `<config>/settings.json` on the
    `PIPY_CONFIG_HOME` → `${XDG_CONFIG_HOME}/pipy` → `~/.config/pipy` chain, plus
    project `.pipy/settings.json`) with Pi migrations, one-level deep merge with
    project precedence, CLI/env overrides, parse-error isolation, and
    field-scoped lock-guarded writes that preserve unknown keys.
  - `keybindings.json` with the default editor/app binding table (single key
    spec or array of alternatives), legacy-name migration, malformed-file
    fallback to defaults, and `/hotkeys` rendered from the resolved manager.
  - Settings drive `defaultProvider`/`defaultModel`, `theme`, `quietStartup`,
    `promptHistory.enabled`, and `autocompleteMaxVisible` at startup; `/settings`
    reports the resolved configuration.
  - System-prompt inputs: `--system-prompt`, repeatable `--append-system-prompt`,
    `SYSTEM.md` / `APPEND_SYSTEM.md` auto-discovery, and `--no-context-files`/
    `-nc`.
  - `retry.*` feeds the provider HTTP retry policy and `compaction.enabled`
    gates auto-compaction.
  - Scoped models: `enabledModels` + `/scoped-models` (view/set/clear/cycle) and
    Ctrl+P forward cycling.
  - Resource enablement via `pipy config` (`-pattern`/`+pattern` over
    `skills`/`prompts`/`themes`/`extensions`) and `enableSkillCommands`.
  - `/reload` re-reads settings, keybindings, resources, and theme.
  - `/changelog` and the `--version` surface.
- Provider/model catalog closeout for the native runtime:
  - Catalog-backed provider construction now covers the OpenAI-compatible Chat
    Completions family, implemented catalog-constructed non-completions
    families, `pipy run` one-shot construction, and startup
    `--native-provider`/`--native-model` resolution through the shared resolver.
  - Extension-registered providers now contribute temporary per-run catalog
    rows: they appear in `--list-models`, resolve at startup when the extension
    is loaded, switch via `/model`, recompute on `/reload`, and construct
    through the extension `ProviderPort` factory without persisting package or
    catalog state.
  - The provider catalog conformance gate covers Verification-Plan items 1-25
    with deterministic fake HTTP/product-path checks and no network access.
- True active-turn provider-request cancellation for the native tool loop:
  Escape and Ctrl-C each thread a per-turn `CancelToken`
  (`pipy_harness.native.cancellation`) into `ProviderPort.complete(...)` that
  shuts the live `urllib`/SSE connection down — during the header wait or the
  body/stream read — so the worker's blocking read raises
  `ProviderCancelledError` instead of finishing the request; the worker is then
  best-effort joined and the loop renders Pi-style red `Operation aborted`
  without appending an assistant/tool observation. The socket-shutdown read
  path tolerates the `http.client` `_close_conn` shutdown race (a concurrent
  `fp = None` surfacing as `AttributeError`) by mapping it to cancellation only
  when the token is cancelled, so an aborted body read cannot leak a spurious
  provider error.
- Python SDK/headless embedding documentation for `pipy_harness.sdk`, including
  the current one-shot in-process surface, fake-provider default, current limits,
  and relationship to planned JSON/RPC automation.
