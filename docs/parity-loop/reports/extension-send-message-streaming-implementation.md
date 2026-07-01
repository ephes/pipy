# Implementation plan: extension `send_message` streaming delivery modes

1. Add focused product-path coverage for `ctx.send_message(..., {deliverAs: "steer"})` and `{deliverAs: "followUp"}`.
   - Acceptance: queued content becomes provider-visible prompts in steering-before-follow-up order.
   - Acceptance: queued content beginning with `/` is not parsed as a local slash command.

2. Thread separate in-memory extension steering/follow-up queues through `NativeToolReplSession.run`.
   - Acceptance: custom-message append/render behavior remains unchanged before delivery routing.
   - Acceptance: `nextTurn`, idle `triggerTurn`, and default no-turn behavior remain unchanged.
   - Acceptance: session replacement clears all extension delivery queues.

3. Update the extension/package conformance gate and docs.
   - Acceptance: `extension_package_conformance.py --json` includes a `send_message_steer_follow_up_delivery` marker.
   - Acceptance: extension docs/backlog/audit remove the streaming-delivery deferral while leaving adjacent custom-message redraw/rendering follow-ons deferred.

4. Verify and review.
   - Acceptance: focused tests, extension conformance, `just check`, and the different-family review gate pass on the complete diff.
