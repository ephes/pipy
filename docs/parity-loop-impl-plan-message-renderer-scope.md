# Implementation plan: message-renderer follow-on scope docs

1. `docs/backlog.md`
   - Remove the `multi-widget message components` candidate from the selected extension platform follow-on queue.
   - Keep the remaining candidates unchanged and add `live custom message component invalidation/re-render` as the message-renderer follow-on actually supported by the Pi API.
   - Acceptance: the Next Slice section no longer points implementers at a non-Pi multi-widget message-renderer surface.

2. `docs/extension-api.md`
   - In the overview/deferred lists and rich-message-renderer closeout, replace `multi-widget message components` with live invalidation/re-render wording.
   - Add the Pi reference fact that `MessageRenderer` returns a single `Component | undefined`, so pipy intentionally keeps one component per custom message and does not introduce an extra widget collection API.
   - Acceptance: the spec distinguishes shipped single-component renderer behavior from deferred live re-render behavior.

3. Validation
   - Run docs/style checks through `just check` before final review.
   - Acceptance: gates pass and the final different-family review covers these docs plus the reviewed plan files.
