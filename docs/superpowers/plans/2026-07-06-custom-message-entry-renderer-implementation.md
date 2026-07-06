# CustomMessageEntry Renderer Implementation Plan

1. Add session-display helpers in `NativeToolReplSession`.
   - Acceptance: a displayable `CustomMessageEntry` can be converted to the Pi-shaped renderer payload `{customType, content, display, details}` without changing persistence.
   - Acceptance: rendering a custom-message entry can either use registered renderer output or fall back to stored `content` when no renderer is registered.

2. Wire all visible custom-message paths through the helper.
   - Acceptance: immediate `ctx.send_message` display, startup replay, captured diagnostics, and `/resume` redraw use the registered renderer when present.
   - Acceptance: `display=False` messages remain hidden.

3. Add focused regression tests.
   - Acceptance: tests cover rich styled rendering with `content` and `details`, no-renderer fallback to content, redraw row rendering, and hidden-message skip.

4. Update docs and gap trackers.
   - Acceptance: `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, `docs/backlog.md`, `docs/parity-plan.md`, and `CHANGELOG.md` no longer list this slice as deferred and describe the shipped behavior.

5. Validate and review.
   - Acceptance: focused tests, `just check`, and a final different-family review over the complete diff are clean before commit.
