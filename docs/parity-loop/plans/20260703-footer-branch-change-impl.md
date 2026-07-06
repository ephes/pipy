# Implementation plan: reactive footer branch-change callbacks

1. Extend `FooterData` with optional branch-change registrar.
   - Acceptance: no-registrar snapshots preserve no-op disposer semantics; registrar-backed snapshots return the registrar's disposer for snake/camel APIs.
2. Add live TUI callback registration and simulated/real branch refresh boundary.
   - Acceptance: `ToolLoopTerminalUi` can register/unregister footer branch callbacks, rebuild the active custom footer with a fresh `FooterData`, repaint on branch changes, and clear callbacks on footer replacement/removal.
3. Wire `_LiveExtensionUiDriver.set_footer` to pass the registrar into `FooterData`.
   - Acceptance: extension footer factories receive `FooterData` whose `onBranchChange` is live in TTY contexts and remains no-op headlessly.
4. Add focused tests for registrar, disposal, rebuild, and stale callback cleanup.
   - Acceptance: tests cover callback invocation, idempotent disposer, and clearing/replacing footer suppressing old callbacks.
5. Update parity docs and release notes if applicable.
   - Acceptance: docs no longer list reactive footer `onBranchChange` as deferred and identify this slice as shipped.
6. Run gates and review.
   - Acceptance: extension package conformance, `just check`, and different-family review are clean before commit.
