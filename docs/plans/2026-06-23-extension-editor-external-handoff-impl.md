# Extension Editor External Handoff Implementation Plan

1. Add an external-editor launch path to the extension editor overlay.
   Acceptance: Ctrl+G calls the path only when `$VISUAL` or `$EDITOR` is set,
   successful edits replace the buffer, and failed exits leave the buffer alone.

2. Keep terminal state coherent while the external editor owns stdio.
   Acceptance: the live TUI restores cooked mode before spawning, re-enters raw
   mode after the editor exits when the overlay is still active, deletes the temp
   file best-effort, and repaints the overlay.

3. Cover the behavior with focused tests.
   Acceptance: tests prove success and failure exits, with no regression to
   existing submit/newline/cancel editor behavior.

4. Update parity docs.
   Acceptance: extension docs, the gap audit, parity plan, and backlog mark this
   exact external-editor handoff as shipped while leaving remaining editor
   follow-ons deferred.
