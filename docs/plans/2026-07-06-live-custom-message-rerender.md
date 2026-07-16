# Live custom message renderer re-render implementation plan

1. Represent retained styled custom-message blocks in the TUI.
   - Acceptance: the TUI can distinguish a render-once styled custom-message block with no metadata from a re-renderable block that carries custom type, JSON-safe data, renderer map, width/theme inputs, and the last expanded value.
2. Thread re-render metadata from session append/replay paths.
   - Acceptance: command/shortcut `ctx.append_entry`, startup replay, and `/resume` redraw still render the same visible rows as before, while styled entries have enough metadata to refresh in place later.
3. Implement in-place refresh on expansion changes.
   - Acceptance: toggling Ctrl+O or extension-driven `set_tools_expanded(True/False)` re-renders retained styled custom-message rows with the new expanded value and replaces existing blocks without duplicating rows. Renderer absence/failure/plain fallback stays bounded and safe.
4. Add focused tests.
   - Acceptance: tests cover collapsed-to-expanded refresh, no duplicate blocks, and fail-soft no-leak behavior. Existing session persistence tests continue proving rendered rows are not archived.
5. Update docs and gap trackers.
   - Acceptance: extension docs, gap audit, backlog, and changelog mention live expanded-flag custom message re-rendering and keep resize/multi-widget follow-ons deferred.
