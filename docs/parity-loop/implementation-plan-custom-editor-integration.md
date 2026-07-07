# Implementation Plan: Extension custom editor component integration

1. Add a focused duck-typed custom editor runtime to `ToolLoopTerminalUi`.
   - Acceptance: `set_editor_component(callable)` builds and activates a component with current input text; `set_editor_component(None)` restores built-in input with preserved component text.
2. Route rendering and input through the active custom editor inside `read_line`.
   - Acceptance: `render_lines()` shows custom editor rows; decoded keys call `handle_input`; callback-driven submit returns the custom text as the user message.
3. Wire Pi-shaped callback/autocomplete compatibility.
   - Acceptance: component receives `on_submit`/`onSubmit`, `on_change`/`onChange`, fallback app handler attributes where applicable, and active autocomplete providers through `set_autocomplete_provider` when present.
4. Add focused unit tests and conformance coverage where appropriate.
   - Acceptance: tests cover activation, key routing/submission, text preservation on clear, factory exceptions failing closed, and existing headless no-op store behavior.
5. Update docs/parity tracking.
   - Acceptance: `docs/backlog.md`, `docs/pi-mono-gap-audit.md`, and `docs/parity-plan.md` describe the bounded live custom editor integration as shipped and keep broader Pi component/RPC work deferred.
