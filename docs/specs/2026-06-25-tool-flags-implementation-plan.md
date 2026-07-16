# Tool Flags Implementation Plan

1. CLI surface and parsing
   - Add `--tools/-t`, `--exclude-tools/-xt`, `--no-tools/-nt`, and `--no-builtin-tools/-nbt` to the shared `run`/`repl` parser flags.
   - Parse comma-separated tool lists with trim/drop-empty behavior matching Pi.
   - Acceptance: `uv run pipy --help` and `uv run pipy repl --help` show the Pi-shaped lines; parsed args expose the expected lists/booleans.

2. Runtime option plumbing
   - Introduce a small immutable native tool-filter options object.
   - Thread it from CLI args through `_tool_repl_adapter_for` / `PipyNativeToolReplAdapter` into `ToolLoopSession`.
   - Acceptance: no behavior changes when no tool flags are passed.

3. Builtin and extension filtering
   - Filter provider-visible tool definitions immediately before each provider request, preserving extension hook transforms.
   - `--no-tools` yields an empty provider-visible tool set and suppresses extension/custom tools too.
   - `--no-builtin-tools` removes only builtin tools, keeping extension/custom tools.
   - `--tools` and `--exclude-tools` apply by name to the active builtin/extension/custom set.
   - Acceptance: focused unit tests observe available tool names in fake provider requests.

4. Validation and docs
   - Fail early on unknown names with a clear message listing the unknown name(s) and known active tool names.
   - Update parity docs to remove tool allow/deny flags from the open-gap list.
   - Acceptance: focused tests cover unknown allow/exclude names and docs mention the shipped flags.
