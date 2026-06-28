# Keybindings

Pipy loads keybindings from `<config>/keybindings.json`, where `<config>` is the
same global config root used for settings (`PIPY_CONFIG_HOME`, then
`${XDG_CONFIG_HOME}/pipy`, then `~/.config/pipy`; existing `~/.pipy` is also
honored). Run `/reload` after editing, or `/hotkeys` to view the resolved table.

The file maps an action id to either one key or a list of alternative keys:

```json
{
  "tui.editor.cursorUp": ["up", "ctrl+p"],
  "tui.editor.cursorDown": ["down", "ctrl+n"],
  "tui.input.newLine": ["shift+enter", "ctrl+j"]
}
```

Older flat action names such as `cursorUp` and `expandTools` are migrated to the
namespaced ids on load.

## Key format

Use `modifier+key`. Modifiers are `ctrl`, `shift`, and `alt`, and can be
combined (`ctrl+shift+x`, `alt+ctrl+x`). Common key names include letters,
digits, punctuation, `escape`/`esc`, `enter`/`return`, `tab`, `space`,
`backspace`, `delete`, `home`, `end`, `pageUp`, `pageDown`, arrow keys, and
`f1` through `f12`.

Terminal support varies. For example, Shift+Enter is available only when the
terminal reports it. During an active turn, Alt+Enter is the follow-up-message
shortcut (`app.message.followUp`), not the configurable newline action.

## Default actions

### TUI editor movement

| Action id | Default | Description |
| --- | --- | --- |
| `tui.editor.cursorUp` | `up` | Move cursor up |
| `tui.editor.cursorDown` | `down` | Move cursor down |
| `tui.editor.cursorLeft` | `left`, `ctrl+b` | Move cursor left |
| `tui.editor.cursorRight` | `right`, `ctrl+f` | Move cursor right |
| `tui.editor.cursorWordLeft` | `alt+left`, `ctrl+left`, `alt+b` | Move word left |
| `tui.editor.cursorWordRight` | `alt+right`, `ctrl+right`, `alt+f` | Move word right |
| `tui.editor.cursorLineStart` | `home`, `ctrl+a` | Move to line start |
| `tui.editor.cursorLineEnd` | `end`, `ctrl+e` | Move to line end |
| `tui.editor.jumpForward` | `ctrl+]` | Jump forward to a character |
| `tui.editor.jumpBackward` | `ctrl+alt+]` | Jump backward to a character |
| `tui.editor.pageUp` | `pageUp` | Scroll up by page |
| `tui.editor.pageDown` | `pageDown` | Scroll down by page |

### TUI editor editing

| Action id | Default | Description |
| --- | --- | --- |
| `tui.editor.deleteCharBackward` | `backspace` | Delete backward |
| `tui.editor.deleteCharForward` | `delete`, `ctrl+d` | Delete forward |
| `tui.editor.deleteWordBackward` | `ctrl+w`, `alt+backspace` | Delete word backward |
| `tui.editor.deleteWordForward` | `alt+d`, `alt+delete` | Delete word forward |
| `tui.editor.deleteToLineStart` | `ctrl+u` | Delete to line start |
| `tui.editor.deleteToLineEnd` | `ctrl+k` | Delete to line end |
| `tui.editor.yank` | `ctrl+y` | Paste most recently deleted text |
| `tui.editor.yankPop` | `alt+y` | Cycle deleted text after yank |
| `tui.editor.undo` | `ctrl+-` | Undo last edit |

### TUI input and selection

| Action id | Default | Description |
| --- | --- | --- |
| `tui.input.newLine` | `shift+enter` | Insert newline |
| `tui.input.submit` | `enter` | Submit input |
| `tui.input.tab` | `tab` | Tab/autocomplete |
| `tui.input.copy` | `ctrl+c` | Copy selection |
| `tui.select.up` | `up` | Move selection up |
| `tui.select.down` | `down` | Move selection down |
| `tui.select.pageUp` | `pageUp` | Page up in a list |
| `tui.select.pageDown` | `pageDown` | Page down in a list |
| `tui.select.confirm` | `enter` | Confirm selection |
| `tui.select.cancel` | `escape`, `ctrl+c` | Cancel selection |

### Application

| Action id | Default | Description |
| --- | --- | --- |
| `app.interrupt` | `escape` | Cancel or abort |
| `app.clear` | `ctrl+c` | Clear editor |
| `app.exit` | `ctrl+d` | Exit when editor is empty |
| `app.suspend` | `ctrl+z` | Suspend to background where supported |
| `app.editor.external` | `ctrl+g` | Open `$VISUAL` or `$EDITOR` |
| `app.clipboard.pasteImage` | `ctrl+v` | Paste image from clipboard where supported |
| `app.tools.expand` | `ctrl+o` | Toggle tool output |
| `app.thinking.toggle` | `ctrl+t` | Toggle thinking blocks |
| `app.thinking.cycle` | `shift+tab` | Cycle thinking level |
| `app.message.followUp` | `alt+enter` | Queue follow-up message |
| `app.message.dequeue` | `alt+up` | Restore queued messages |

### Sessions, models, and tree navigation

| Action id | Default | Description |
| --- | --- | --- |
| `app.session.new` | *(none)* | Start a new session |
| `app.session.tree` | *(none)* | Open session tree |
| `app.session.fork` | *(none)* | Fork current session |
| `app.session.resume` | *(none)* | Resume a session |
| `app.session.togglePath` | `ctrl+p` | Toggle session path display |
| `app.session.toggleSort` | `ctrl+s` | Toggle session sort mode |
| `app.session.toggleNamedFilter` | `ctrl+n` | Toggle named-session filter |
| `app.session.rename` | `ctrl+r` | Rename a session |
| `app.session.delete` | `ctrl+d` | Delete a session |
| `app.session.deleteNoninvasive` | `ctrl+backspace` | Delete when query is empty |
| `app.model.select` | `ctrl+l` | Open model selector |
| `app.model.cycleForward` | `ctrl+p` | Cycle to next model |
| `app.model.cycleBackward` | `shift+ctrl+p` | Cycle to previous model |
| `app.tree.foldOrUp` | `ctrl+left`, `alt+left` | Fold branch or move up |
| `app.tree.unfoldOrDown` | `ctrl+right`, `alt+right` | Unfold branch or move down |
| `app.tree.editLabel` | `shift+l` | Edit selected tree label |
| `app.tree.toggleLabelTimestamp` | `shift+t` | Toggle label timestamps |
| `app.tree.filter.default` | `ctrl+d` | Default tree filter |
| `app.tree.filter.noTools` | `ctrl+t` | Hide tool results |
| `app.tree.filter.userOnly` | `ctrl+u` | User messages only |
| `app.tree.filter.labeledOnly` | `ctrl+l` | Labeled entries only |
| `app.tree.filter.all` | `ctrl+a` | Show all entries |
| `app.tree.filter.cycleForward` | `ctrl+o` | Cycle tree filter forward |
| `app.tree.filter.cycleBackward` | `shift+ctrl+o` | Cycle tree filter backward |

### Scoped model selector

| Action id | Default | Description |
| --- | --- | --- |
| `app.models.save` | `ctrl+s` | Save model selection |
| `app.models.enableAll` | `ctrl+a` | Enable all models |
| `app.models.clearAll` | `ctrl+x` | Clear all models |
| `app.models.toggleProvider` | `ctrl+p` | Toggle all models for provider |
| `app.models.reorderUp` | `alt+up` | Move model up in cycle order |
| `app.models.reorderDown` | `alt+down` | Move model down in cycle order |

## More examples

Emacs-style movement:

```json
{
  "tui.editor.cursorUp": ["up", "ctrl+p"],
  "tui.editor.cursorDown": ["down", "ctrl+n"],
  "tui.editor.deleteCharBackward": ["backspace", "ctrl+h"]
}
```

Vim-style Alt movement:

```json
{
  "tui.editor.cursorUp": ["up", "alt+k"],
  "tui.editor.cursorDown": ["down", "alt+j"],
  "tui.editor.cursorLeft": ["left", "alt+h"],
  "tui.editor.cursorRight": ["right", "alt+l"]
}
```
