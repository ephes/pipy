# Extension UI tool expansion parity plan

Gap: Pi exposes tool-output expansion controls on the extension UI context:
`ctx.ui.getToolsExpanded()` returns the current interactive tool-output expansion
state, and `ctx.ui.setToolsExpanded(expanded)` sets that state and requests a
render. In Pi RPC/headless contexts the getter returns `false` and the setter is
a no-op (`packages/coding-agent/src/core/extensions/types.ts`,
`modes/interactive/interactive-mode.ts`, `modes/rpc/rpc-mode.ts`, and
`core/extensions/runner.ts`). Pipy already has the underlying product-TUI state
(`ToolLoopTerminalUi.tools_expanded`) and user-facing toggle, but extensions
cannot read or set it.

Scope for this slice:

1. Add Pythonic and Pi-shaped extension UI methods:
   `get_tools_expanded()` / `getToolsExpanded()` and
   `set_tools_expanded(expanded)` / `setToolsExpanded(expanded)`.
2. In live command/shortcut contexts, wire the methods through the UI driver to
   read and set `ToolLoopTerminalUi.tools_expanded`; setting should coerce the
   value to `bool` and request a repaint when the live TUI supports it.
3. In headless/no-UI contexts, match Pi RPC/no-op behavior: getters return
   `False`, setters do nothing.
4. Add focused tests covering live command dispatch and headless dispatch.
5. Update extension parity docs/backlog/audit to record the landed slice and keep
   the remaining follow-ons accurate.

Done when:

- A live extension command can notify both the initial expansion state and the
  state after `setToolsExpanded(True)` / `set_tools_expanded(False)`.
- Headless deterministic dispatch reports `False` before and after setting.
- `uv run python scripts/parity_checks/extension_package_conformance.py --json`
  and `just check` pass.
