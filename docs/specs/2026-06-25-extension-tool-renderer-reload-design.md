# Extension tool renderer reload design

Gap: Extension custom tool renderers are still bound once at session startup, so a `/reload` that adds, removes, or changes an extension tool's `render_call`/`render_result` functions refreshes the tool implementation but not the renderer map used by the product TUI/captured-stream renderer.

Pi reference: `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/*` and `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts` treat extension reload as refreshing the active extension contribution set before later UI/tool rendering.

Plan: keep pipy's existing renderer objects and the per-session details sink, but add a small renderer refresh boundary so the same renderer instance used for the session receives the reloaded `ExtensionTool` renderer map immediately after `_ext_runtime.tools` is rebuilt. The map must include only reloaded tools with `render_call` or `render_result`, remove stale renderers for removed/disabled extensions, and preserve fail-soft rendering. Add a focused test that constructs a renderer, observes default rendering, refreshes the map, and observes custom rendering without restarting the renderer/session. Update extension docs/backlog/audit to mark the follow-on shipped.

Done when:
1. `_ToolLoopRenderer` and `_TuiToolLoopRenderer` can refresh their extension tool-renderer maps after `/reload`.
2. `/reload` rebuilds and applies the map from `_ext_runtime.tools` alongside the tool registry.
3. Focused tests prove stale renderers are removed and new renderers are used after refresh.
4. `uv run python scripts/parity_checks/extension_tool_renderer_conformance.py --json` and `just check` pass.
