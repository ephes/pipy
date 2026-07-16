# Extension Editor Component Slice Plan

Gap: Extension/package platform follow-on — Pi's `ExtensionUIContext.setEditorComponent` / `getEditorComponent` surface.

Pi reference paths:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/rpc/rpc-mode.ts`

## Reference behavior

Pi exposes `ctx.ui.setEditorComponent(factory | undefined): void` and `ctx.ui.getEditorComponent(): EditorFactory | undefined`. In interactive mode the setter stores a custom editor factory on the live interactive mode and the getter returns that factory. Passing `undefined` restores the default editor. In RPC mode custom editor components are unsupported: `setEditorComponent()` is a no-op and `getEditorComponent()` returns `undefined`.

## Pipy slice scope

Pipy will add the same narrow Python/Pi-shaped UI surface without porting Pi's TypeScript TUI component system:

1. Add snake_case and camelCase methods on extension UI contexts:
   - `ctx.ui.set_editor_component(factory)` / `ctx.ui.setEditorComponent(factory)`
   - `ctx.ui.get_editor_component()` / `ctx.ui.getEditorComponent()`
2. Headless contexts behave like Pi RPC: setter is a deterministic no-op and getter returns `None`.
3. Live command/shortcut contexts store the opaque factory object on the UI driver and return it from the getter; passing `None` clears it. This provides API compatibility and state round-tripping while deferring actual custom editor rendering/input integration to a later slice.
4. The live store must not persist factories to session files or safe metadata, must not call extension-provided factories, and must not affect provider turns.
5. Update extension docs and parity docs to mark this API-compatibility/store slice as shipped while keeping full custom editor rendering deferred.

## Done when

- Focused tests cover live store/clear/get behavior and headless no-op behavior through extension command dispatch.
- `uv run python scripts/parity_checks/extension_package_conformance.py --json` passes.
- `just check` passes.
- A different-family/fresh-context review returns CLEAN over the complete diff before commit.
