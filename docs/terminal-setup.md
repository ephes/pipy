# Terminal Setup

Pipy's product TUI is an inline terminal interface: finalized output is printed
once into the terminal's normal scrollback, while the editor, status, menus, and
pending turn state stay in a small live region at the bottom. It does not use
the alternate screen and it does not enable terminal mouse tracking, so native
scrollback and mouse text selection keep working.

Use a modern terminal emulator for the best interactive experience. Ghostty,
Kitty, iTerm2, WezTerm, Alacritty builds with extended-key support, VS Code's
integrated terminal, and Windows Terminal all work well when they forward the
modified keys you want to use.

## Multiline Input

`Enter` submits the current prompt. `Shift+Enter` inserts a newline when the
terminal reports a distinct Shift+Enter sequence. Pipy decodes the common
xterm `modifyOtherKeys` and Kitty/CSI-u encodings for this.

Some terminals and multiplexers collapse Shift+Enter to plain Enter. In those
environments, use the portable fallback shown by pipy's editor overlays:
`Alt+Enter` inserts a newline where the focused overlay owns the editor. During
an active model turn in the main product TUI, `Alt+Enter` has Pi's follow-up
meaning: it queues the current text to run after the active turn.

Run `/hotkeys` inside pipy to see the bindings resolved for the current session.

## Paste And Attachments

Bracketed paste is enabled in the product TUI. Multi-line pasted text is
inserted literally into the editor and does not submit accidentally.

Typed or pasted references use the same bounded attachment rules as normal
prompts:

```text
@path/to/file.py
@image:path/to/screenshot.png
@image:"path with spaces/photo.webp"
```

Text files are loaded as bounded workspace excerpts. Image references are
accepted for PNG, JPEG, GIF, and WebP and are sent only to multimodal-capable
providers. Missing, ignored, oversized, unsupported, secret-shaped, or
out-of-workspace paths fail closed with a local diagnostic.

Dropping a single file onto the terminal usually arrives as bracketed paste. If
the path points at an image, pipy inserts an `@image:` reference; otherwise it
inserts an `@path` reference. Ordinary text paste remains ordinary text.

`Ctrl+V` pastes an image from the OS clipboard when a clipboard-image reader is
available for the platform. Pipy writes the image to an owner-only temp file and
inserts an `@image:` reference. Raw image bytes are not written to the
metadata-first archive.

## Clipboard Copy

Use `/copy` to copy the last assistant answer. Pipy tries fixed OS clipboard
commands first and falls back to OSC 52 when no command is available. In tmux or
another multiplexer, OSC 52 may require multiplexer-level clipboard passthrough
configuration.

## Terminal Title And Themes

Extensions can set the terminal title for a live TTY session. Pipy saves and
restores the prior title where the terminal supports the standard title stack.

Theme selection lives in `/settings`. The `--theme` and `--no-themes` flags
control theme resource loading, but switching the active terminal palette is a
settings action, not a `/theme` command.

## Platform Notes

### Ghostty, Kitty, iTerm2, WezTerm

These terminals are the easiest path for pipy's TUI. If modified Enter keys do
not work through tmux, configure tmux as described in [tmux Setup](/tmux/).

### VS Code Integrated Terminal

VS Code may reserve some key chords before the terminal sees them. If
Shift+Enter does not reach pipy, add a VS Code terminal keybinding that sends
CSI-u Shift+Enter:

```json
{
  "key": "shift+enter",
  "command": "workbench.action.terminal.sendSequence",
  "args": { "text": "\u001b[13;2u" },
  "when": "terminalFocus"
}
```

### Windows Terminal

Forward modified Enter keys if you want them inside pipy:

```json
{
  "actions": [
    {
      "command": { "action": "sendInput", "input": "\u001b[13;2u" },
      "keys": "shift+enter"
    },
    {
      "command": { "action": "sendInput", "input": "\u001b[13;3u" },
      "keys": "alt+enter"
    }
  ]
}
```

If the old fullscreen behavior for Alt+Enter persists, fully close and reopen
Windows Terminal.

### Limited Terminals

Terminals that cannot distinguish modified Enter from plain Enter can still run
pipy, but some ergonomic bindings will be unavailable. Use `pipy -p "<prompt>"`
or `pipy repl --mode json "<prompt>"` when you need a noninteractive path that
does not depend on terminal key decoding.

## Verification

Useful local checks:

```sh
uv run pipy --help
uv run pipy repl --help
uv run python scripts/parity_checks/tui_workflow_conformance.py --json
```

For interactive checks, start `uv run pipy repl --native-provider fake` in a
real terminal and try Shift+Enter, bracketed paste, `/hotkeys`, `/settings`,
`/copy`, and a dropped workspace file.
