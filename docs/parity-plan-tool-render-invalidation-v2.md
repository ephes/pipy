# Parity Plan: ToolRenderContext.invalidate Surface Stub

Gap: extension component-library parity, limited to exposing Pi's tool-renderer `ctx.invalidate` field without retained-row re-render semantics.

Pi reference to verify before implementation:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`: `ToolRenderContext.invalidate` is a required zero-argument function.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/components/tool-execution.ts`: Pi's live TUI wires the callback to invalidate and synchronously render that tool component.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/export-html/tool-renderer.ts`: non-live export rendering supplies a no-op callback.

Current pipy state:

- `ToolRenderContext` has `tool_name`, args/result metadata, `expanded`, `width`, `theme`, and mutable `state`, but no `invalidate` attribute.
- Pipy does not retain extension tool-render components or re-run a renderer for one existing tool row; `render_tool_phase` formats lines once and falls back on errors.
- Therefore this slice will not claim live state-driven row re-render parity. That remains deferred with `lastComponent`/retained component lifecycle.

Implementation plan:

1. Verify Pi references and pipy's dispatch capability first. Grep every `ToolRenderContext(` construction in `src/`, `tests/`, `scripts/`, and `docs/examples/extensions/`; check whether product-TUI dispatch can bust/re-render materialized tool-row output or otherwise has a real retained-row repaint hook, and check the public typing re-export in `src/pipy_harness/extensions.py` / `__all__` remains correct. If a real invalidation path exists, stop and re-plan a real invalidation slice; otherwise continue with this explicit partial stub.
2. Add public helper `noop_tool_render_invalidate()` in the extension runtime module and type it as `Callable[[], None]`. Add `invalidate` to the dataclass as a keyword-only field (`field(default=noop_tool_render_invalidate, repr=False, compare=False, kw_only=True)`) after the existing fields; this repo targets Python 3.10+, so `kw_only=True` is available and avoids default/non-default ordering issues. Production dispatch sites still pass it explicitly. Document in the follow-on note that the compatibility default must be removed or replaced before real live invalidation ships, so missed call sites cannot silently degrade later.
3. Pass `noop_tool_render_invalidate` explicitly in both captured and product-TUI dispatch paths and document why: pipy formats tool-render output once and does not retain per-row components yet.
4. Do not call a returned component object's own `invalidate()` lifecycle method; Pi's `ctx.invalidate` is a context-supplied repaint request, not that component lifecycle hook. Test this with a fake component whose `invalidate` method records calls.
5. Update `docs/extension-api.md`, `docs/backlog.md`, `docs/pi-mono-gap-audit.md`, `docs/parity-plan.md`, root `CHANGELOG.md`, and in-code docs prominently: `ctx.invalidate` now exists and is safe to call, but currently no-ops in pipy and does not re-render tool output from changed `ctx.state`; live Pi-style row invalidation is the follow-on. `docs/pi-mono-gap-audit.md` must mark this PARTIAL, not closed/done. Check bundled examples for any existing `invalidate()` usage and update wording if needed.
6. Extend focused tests and the extension conformance gate so each dispatch path supplies callable zero-argument `ctx.invalidate()` (the explicit `noop_tool_render_invalidate` in this partial slice), calling it leaves rendered output byte-identical and does not fail, repr/equality/hash snapshots do not change due to the callable field, and the no-live golden conformance path records the callable surface as a regression tripwire only.

Done when:

- Focused tests pass.
- `scripts/parity_checks/extension_conformance_gate.py --json` passes.
- `just check` passes.
- Public typing/re-export surfaces expose `invalidate: Callable[[], None]`.
- `docs/extension-api.md`, `docs/backlog.md`, `docs/pi-mono-gap-audit.md`, `docs/parity-plan.md`, and root `CHANGELOG.md` record both the Pi field-shape cross-check, the shipped callable surface, and the deferred live row re-render behavior, with the gap marked PARTIAL rather than closed.
