# Extension UI hidden-thinking label implementation plan

1. Extend the extension UI protocol and collector.
   - Add `set_hidden_thinking_label(label: str | None = None)` and `setHiddenThinkingLabel(label: str | None = None)` to `ExtensionUi`.
   - Add `set_hidden_thinking_label(label: str | None = None)` to `ExtensionUiDriver`.
   - `_CollectingUi` no-ops headlessly and delegates live calls fail-soft to the driver.
   - Acceptance: focused dispatch tests prove live delegation and headless no-op behavior for snake_case and camelCase.

2. Add product-TUI state and rendering.
   - Add default/current hidden-thinking label state to `ToolLoopTerminalUi`.
   - Add `set_extension_hidden_thinking_label(label: str | None = None)` that restores `"Thinking..."` on `None`, stringifies custom labels, and repaints.
   - When `thinking_hidden` is true and `reasoning_text` is non-empty, render a reasoning-style block containing the current label instead of rendering nothing. Keep `_settle_reasoning` deferral/reveal behavior unchanged.
   - Acceptance: focused TUI tests cover default label, custom label, restoring default, and no leaked reasoning body while folded.

3. Wire the live driver.
   - `_ExtensionUiDriver.set_hidden_thinking_label` delegates to `ToolLoopTerminalUi.set_extension_hidden_thinking_label`.
   - Acceptance: live driver unit test proves label changes and reset affect rendered lines.

4. Update docs and parity tracking.
   - Mark `setHiddenThinkingLabel` as shipped in `docs/extension-api.md` and the current extension/package follow-on docs.
   - Update backlog/gap audit wording to remove the hidden-thinking label from deferred/status prose.
   - Acceptance: docs consistently describe live-only shipped behavior and no-op headless behavior.

5. Run gates and final review.
   - Run `uv run python scripts/parity_checks/extension_package_conformance.py --json` and `just check`.
   - Run a different-family review over the complete code+docs diff and require CLEAN before commit.
