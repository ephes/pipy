# Extension session metadata actions plan

Gap: Extension/package platform follow-on — expose Pi-shaped command/shortcut session metadata actions (`setSessionName`/`getSessionName` and `setLabel`) through pipy's Python extension command context.

Pi reference: `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts` exposes `appendEntry`, `setSessionName`, `getSessionName`, and `setLabel` on the extension API/actions surface. Pipy already ships `ctx.append_entry(...)` and read-only `ctx.session_manager` / `ctx.sessionManager`; this slice adds the smallest mutation side of that same Pi session-metadata surface without broad session navigation or raw transcript access.

Design:

- Add command-context methods `ctx.set_session_name(name)`, Pi-shaped `ctx.setSessionName(name)`, `ctx.get_session_name()` / `ctx.getSessionName()`, `ctx.set_label(entry_id, label)`, and `ctx.setLabel(entry_id, label)`.
- Implement them through injected callable boundaries rather than exposing `NativeSessionTree` mutation methods directly.
- In product sessions, wire the callables to `NativeSessionTree.append_session_info(...)` and `append_label_change(...)`, so changes persist as ordinary native session entries and existing session picker/tree labels keep working.
- In headless/unit dispatch with no session tree, raise `ExtensionCapabilityError` for mutations and return `None` for `get_session_name()`, matching the existing deterministic capability pattern.
- Keep privacy unchanged: names/labels are already user-visible native product session metadata; nothing is written to the metadata-first `pipy-session` archive.

Done when:

1. Focused unit coverage proves the new command-context methods call only the injected boundaries, expose camelCase aliases, and fail predictably without capabilities.
2. Product-path coverage proves an extension command persists a session name and label into the active native session tree.
3. `docs/extension-api.md`, `docs/backlog.md`, and `docs/pi-mono-gap-audit.md` mark this narrow session-metadata action slice as shipped while leaving broader session navigation/state APIs deferred.
4. `uv run pytest` for the focused tests, `uv run python scripts/parity_checks/extension_package_conformance.py --json`, and `just check` pass.

## Implementation plan

1. Extend `extension_runtime.py` protocols/context wiring with `SetSessionNameFn`, `GetSessionNameFn`, and `SetLabelFn` callables plus snake_case and Pi-shaped camelCase command-context methods. Acceptance: methods delegate to injected callables, aliases behave identically, missing mutation capabilities raise `ExtensionCapabilityError`, and missing name getter returns `None`.
2. Thread the callables through `make_extension_context`, `dispatch_extension_command`, `dispatch_extension_shortcut`, and `_run_extension_handler`. Acceptance: existing callers keep working with default `None` values.
3. Wire product sessions in `tool_loop_session.py` to `NativeSessionTree.append_session_info(...)`, `NativeSessionTree.name`, and `NativeSessionTree.append_label_change(...)`. Acceptance: extension commands can persist a session name and entry label into the active native session file without exposing the tree object.
4. Add focused tests in `tests/test_native_extension_dispatch.py` and product-path coverage in `tests/test_native_tool_loop_session.py`. Acceptance: tests prove aliases, capability errors, and persisted session metadata.
5. Update docs/parity notes and run the focused tests, extension package conformance gate, and `just check` before the final different-family review.
