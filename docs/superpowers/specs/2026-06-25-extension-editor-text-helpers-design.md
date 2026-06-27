# Extension editor text helpers - design

Status: design for one parity slice. Owning spec:
[`docs/extension-api.md`](../../extension-api.md). Gap source:
[`docs/pi-mono-gap-audit.md`](../../pi-mono-gap-audit.md) follow-on 1 and the
`ExtensionUIContext` editor target in `docs/extension-api.md`.

## Pi Behavior

Pi exposes editor text helpers on extension command/shortcut UI contexts:

- `ctx.ui.getEditorText()` returns the current core input editor text.
- `ctx.ui.setEditorText(text)` replaces the current core input editor text.
- `ctx.ui.pasteToEditor(text)` sends text through the editor paste path.
- In non-interactive extension contexts, Pi's runner uses deterministic UI no-ops.

This slice covers only the editor text helpers. It does not implement
`setEditorComponent`, autocomplete provider wrapping, `send_message`,
`deliverAs`, or `triggerTurn`.

## Pipy Design

Expose the helpers in pipy's existing snake_case extension UI surface:

- `ctx.ui.get_editor_text() -> str`
- `ctx.ui.set_editor_text(text) -> None`
- `ctx.ui.paste_to_editor(text) -> None`

`_CollectingUi` keeps the non-interactive contract deterministic:
`get_editor_text()` returns `""`, and mutating helpers no-op without a live UI
driver. Live driver failures are fail-soft like the existing UI methods.

The live driver delegates through `ToolLoopTerminalUi`. `set_input_text(...)`
already pre-fills the next prompt; this slice extends that boundary with a
current text getter and a paste wrapper. The stdlib TUI `paste_to_editor` path
now inserts literal pasted text at the current cursor while preserving
surrounding draft text and pasted newlines, matching Pi's bracketed-paste
semantics.

## Done

- Unit tests prove headless no-op/get-empty behavior and live driver delegation
  for all three helpers.
- Public docs and parity status mark editor read/write/paste helpers shipped and
  keep `setEditorComponent` and autocomplete providers deferred.
- `uv run python scripts/parity_checks/extension_package_conformance.py --json`
  and `just check` pass.
