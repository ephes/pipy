# Extension UI tool expansion implementation plan

1. Extend the public extension UI protocols and concrete context with `get_tools_expanded` / `getToolsExpanded` and `set_tools_expanded` / `setToolsExpanded`.
   - Acceptance: headless contexts return `False` and ignore setters; method names are available in both snake_case and Pi-shaped camelCase.
2. Extend the live TUI driver in `tool_loop_session.py` to read/write `ToolLoopTerminalUi.tools_expanded` and request a render after changes.
   - Acceptance: command/shortcut handlers operating with a live UI can change the same expansion state used by the built-in toggle.
3. Add focused tests in the native extension dispatch suite for live and headless behavior.
   - Acceptance: tests verify initial state, setter coercion, camelCase aliases, and headless no-op parity.
4. Update extension parity docs and gap trackers to mark the narrow slice shipped and remove it from remaining follow-ons.
   - Acceptance: `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` describe the landed methods and keep broader deferred items intact.
5. Run the extension conformance gate and full project gate before final review.
   - Acceptance: `uv run python scripts/parity_checks/extension_package_conformance.py --json` and `just check` pass.
