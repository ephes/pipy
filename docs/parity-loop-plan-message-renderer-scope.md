# Parity plan: correct message-renderer follow-on scope

Gap: `docs/backlog.md` names "multi-widget message components" as a candidate extension-platform follow-on and `docs/extension-api.md` repeats it as deferred after rich message renderers. The local Pi reference does not expose a multi-widget message-renderer API: `packages/coding-agent/src/core/extensions/types.ts` defines `MessageRenderer<T> = (message, options, theme) => Component | undefined`, and `packages/coding-agent/src/modes/interactive/components/custom-message.ts` installs at most one returned custom `Component` per custom message, replacing the default box. Therefore the current pipy rich message-renderer slice (one component per custom entry, snapshot-rendered) is correctly scoped for this Pi API except for explicitly deferred live invalidation; the "multi-widget" candidate is not a real Pi parity gap.

Plan:

1. Update the gap-source docs only: remove "multi-widget message components" from the selected follow-on queue in `docs/backlog.md` and from the deferred rich-message-renderer notes in `docs/extension-api.md`.
2. Replace that wording with the verified remaining Pi-shaped follow-on: live custom message component invalidation/re-render behavior, while preserving the existing deferred full custom editor integration and provider/auth helper candidates.
3. Add a short note in the rich-message-renderer closeout that Pi renderers return a single `Component | undefined`, so pipy should not invent a multi-widget message-renderer surface.

Done when:

- The selected next-slice queue no longer advertises a nonexistent multi-widget message-renderer gap.
- The extension API spec clearly separates shipped single-component renderers from the remaining live invalidation/re-render gap.
- No runtime behavior changes are made.
