# Tool Flags Parity Plan

Gap: Pi-shaped top-level tool-selection flags (`--tools`/`-t`, `--exclude-tools`/`-xt`, `--no-tools`/`-nt`, and `--no-builtin-tools`/`-nbt`).

Reference: `/Users/jochen/src/pi-mono/packages/coding-agent/src/cli/args.ts` parses comma-separated allow/exclude lists, `--no-tools`, and `--no-builtin-tools` into runtime args and advertises them in help.

Scope for this single slice:

- Add the Pi flag names and short aliases to pipy's product `repl` surface (including implicit top-level routing), and the captured `run --agent pipy-native` surface where catalog flags already apply. This slice deliberately does not include any future `--list-tools` inventory surface; it only filters the provider-visible tool set for a run.
- Implement builtin-tool filtering at the native tool-loop boundary:
  - `--tools/-t a,b` is an allowlist of active builtin, extension, and custom tool names.
  - `--exclude-tools/-xt a,b` removes listed active builtin, extension, and custom tools.
  - `--no-tools/-nt` disables all tools, including extension tools.
  - `--no-builtin-tools/-nbt` disables builtin tools while leaving extension tools available.
- Validate unknown builtin names fail early with a clear message.
- Preserve privacy and session behavior: the selected tool set changes provider-visible tool definitions only; no secret or prompt content is written.
- Update parity docs and tests.

Done when:

1. Focused tests prove CLI parsing/help plus tool-definition filtering for allowlist, exclude, all-disabled, builtin-disabled, and unknown names.
2. `just check` passes.
3. A different-family review returns CLEAN over the complete diff.
