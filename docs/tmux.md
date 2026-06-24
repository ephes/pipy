# tmux Setup

Pipy works inside tmux. Configure tmux to forward extended keys if you want
modified Enter chords such as Shift+Enter, Ctrl+Enter, and Alt+Enter to reach
the product TUI distinctly.

## Recommended Configuration

For tmux 3.4 or newer, add this to `~/.tmux.conf`:

```tmux
set -as terminal-features ',*:extkeys'
set -g extended-keys always
set -g extended-keys-format csi-u
```

Then restart tmux fully:

```sh
tmux kill-server
tmux
```

`csi-u` is the most reliable format for pipy's stdlib key decoder. The
`terminal-features` line tells tmux that the outer terminal supports extended
keys when terminfo does not advertise it, and `extended-keys always` makes tmux
request extended keys itself.

If your tmux rejects `extended-keys-format`, you are likely on tmux 3.2 or 3.3.
Keep `extended-keys on`; pipy also decodes the xterm `modifyOtherKeys` format,
but CSI-u is preferred when your tmux version supports it.

Without extended keys, tmux often collapses Shift+Enter and Ctrl+Enter to plain
Enter.

## What This Fixes

With no extended-key forwarding, modified Enter keys usually become legacy byte
sequences:

| Key | Without extended keys | With `csi-u` |
| --- | --- | --- |
| Enter | `\r` | `\r` |
| Shift+Enter | `\r` | `\x1b[13;2u` |
| Ctrl+Enter | `\r` | `\x1b[13;5u` |
| Alt/Option+Enter | `\x1b\r` | `\x1b[13;3u` |

This matters for multiline editing, follow-up queueing during active turns, and
any custom keybindings that depend on modified Enter.

## Scrollback

Pipy's product TUI is inline and does not use the alternate screen. Finalized
assistant messages, tool output, and shell shortcut output are committed to the
terminal's normal scrollback once, above the live editor frame. That means tmux
copy-mode can review prior output normally.

Only the live region at the bottom is repainted: the input frame, status/footer,
menus, pending messages, and streaming tail.

## Clipboard Copy In tmux

`/copy` uses OS clipboard helpers when available and falls back to OSC 52. If
copying from inside tmux does not reach the system clipboard, enable OSC 52
passthrough in your tmux and terminal configuration. Pipy treats clipboard
failure as a local notice; it does not call a provider or write clipboard
contents to session metadata.

## Troubleshooting

Check your tmux version:

```sh
tmux -V
```

Use tmux 3.4 or newer for the recommended CSI-u configuration above. tmux 3.2
and 3.3 can still forward extended keys in xterm format, but the config is less
portable across terminals.

Reload tmux config after editing it:

```sh
tmux source-file ~/.tmux.conf
```

If Shift+Enter still submits instead of inserting a newline, restart the tmux
server rather than only reloading the config:

```sh
tmux kill-server
tmux
```

Then run pipy in a fresh pane:

```sh
uv run pipy repl --native-provider fake
```

Inside pipy, run `/hotkeys` to inspect the active keybindings and try
Shift+Enter in the editor. If your terminal or remote SSH path still cannot
forward modified Enter distinctly, use a terminal with extended-key support or a
noninteractive mode such as `pipy -p` / `pipy repl --mode json`.
