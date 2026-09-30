# DF1-F7b (selectors): fuzzy search, `/model` matching, argument completion

Status: implemented on `feat/f7b-selectors` (the selectors bullet of DF1-F7b
in `docs/backlog.md` lists what is left; the other F7b bullets stay open).
Implementation notes: the `/model` selector and exact match also drop
available models whose provider cannot run pipy's tool loop (the `fake`
bootstrap); the per-edit argument completion does not (it constructs no
provider). No `Warning:` line is added (see 7).
Pi reference: `~/src/pi-mono` at `1b347794e`:
`packages/tui/src/fuzzy.ts` (`fuzzyMatch`, `fuzzyFilter`),
`packages/tui/src/components/{select-list,input}.ts`,
`packages/tui/src/autocomplete.ts` (`CombinedAutocompleteProvider`
`getSuggestions` 312-405, `applyCompletion` 413-500),
`packages/tui/src/components/editor.ts` (autocomplete triggers 1227-1256,
Enter/Tab on the popup 756-812, `getBestAutocompleteMatchIndex` 2211),
`packages/coding-agent/src/modes/interactive/components/{model-selector,thinking-selector,session-selector-search,dynamic-border,keybinding-hints}.ts`,
`packages/coding-agent/src/modes/interactive/model-search.ts`,
`packages/coding-agent/src/core/model-resolver.ts` (`findExactModelReferenceMatch` 88),
`packages/coding-agent/src/modes/interactive/interactive-mode.ts`
(`createFuzzyAutocompleteItems` 349, argument completers 689-737, submit
handler 3096-3118, `showError`/`showWarning` 4492-4502, `handleThinkingCommand`
5023, `handleModelCommand` 5072, `findExactModelMatch` 5096,
`showModelSelector` 5221).

## Scope

In:

1. `fuzzy.py`: `fuzzy_match` / `fuzzy_filter`, an exact port of `fuzzy.ts`.
2. `/model` in the TUI: Pi's exact-reference match, else Pi's
   `ModelSelectorComponent` with the text as its search.
3. `/thinking` selector: Pi's search input and fuzzy filter.
4. Slash menu: Pi's fuzzy command-name filter; argument completion for
   `/model` and `/thinking`.
5. `/resume` picker search: Pi's `parseSearchQuery` + `matchSession`.
6. Slash commands are not drawn as user messages.
7. `Error: …` lines (Pi `showError`) for the paths this slice touches.

Out (recorded in the F7b backlog entry): `/login` argument completion (Pi's
login provider options and auth-type descriptions), extension-command
`getArgumentCompletions` (no pipy extension API field), the selector's
background catalog refresh and its status line (`refreshModelCatalogs`), the
rest of Pi's `Input` editing keys (undo, yank, word motion, forward Delete)
and its grapheme-cluster editing, the session
picker's `relevance`/`threaded` sorts and `allMessagesText` search text,
merging back-to-back statuses, the remaining pipy notices that Pi shows as
warnings/errors, and the plain (non-TUI) REPL `/model <ref>`, which keeps
pipy's resolver because Pi has no such mode.

## 1. Fuzzy matching (`src/pipy_harness/native/fuzzy.py`)

Port `fuzzyMatch(query, text)` literally:

- `query.toLowerCase()` / `text.toLowerCase()` → Python `str.lower()`. Both
  apply the locale-independent full Unicode mapping including `İ` → `i̇` (two
  code points) and the final-sigma rule; the differential test pins these
  cases against Node.
- Indices are UTF-16 code units (`indexOf`, `i * 0.1`, `textLower[i - 1]`).
  pipy works on the UTF-16 code-unit sequence of the lowered strings (a list
  of units, surrogates kept as separate units), so astral characters score
  exactly as in Pi.
- Word boundary: `i == 0` or the previous unit is in `/[\s\-_./:]/`. JS `\s`
  and `trim()` use the ECMAScript whitespace + line-terminator set (which
  includes U+FEFF and excludes U+001C-U+001F and U+0085, unlike Python's
  `str.isspace`), so pipy uses that explicit set.
- Scoring in the same order of float operations: consecutive
  `score -= consecutive * 5`; gap `score += (i - last - 1) * 2`; boundary
  `score -= 10`; always `score += i * 0.1`; exact equality `score -= 100`.
- `query.length > text.length` (UTF-16 lengths) → no match; empty query →
  `(True, 0)`.
- Swap fallback: `^[a-z]+[0-9]+$` / `^[0-9]+[a-z]+$` on the lowered query
  (ASCII classes), swapped match score `+ 5`, returned only when the primary
  match failed and the swapped one matched.

`fuzzy_filter(items, query, get_text)`: blank query returns `items`
unchanged; tokens are `query.strip().split(/[\s/]+/)` without empties; every
token must match; the result is sorted by the summed score, stable (JS
`Array.prototype.sort` is stable). `trim()` uses the JS whitespace set.

## 2. `/model` (TUI)

Pi `handleModelCommand(searchTerm)`:

- No term → selector without search.
- Term → `findExactModelReferenceMatch(term, models)` where `models` is the
  scoped models when any, else the available models. pipy already ports it as
  `model_resolver.find_exact_model_reference`. A match →
  `setModel(model, {persist: false})`, status `Model: <id>`; an error →
  `Error: <message>`. No match → the selector with the term as its search.
  (Pi's catalog refresh before the second lookup is out of scope.)
- "Available" = `catalog.get_available()` (rows whose provider has auth and
  that `model_availability_reason` accepts: Pi `filterModels`).
- "Scoped" = `resolve_model_scope(enabledModels, available)` when
  `enabledModels` is non-empty (Pi resolves the patterns against the
  available models at startup and on `/scoped-models` changes; pipy re-reads
  the setting when the selector opens).

`ModelSelectorComponent` port (`ui/components/model_selector.py`, replacing
the generic list for `/model`; the settings dialog's "change provider/model"
and the default-trust list keep the old generic list):

- Rows: the "all" list is the available models sorted current first, then
  the default, then `provider.localeCompare` (stable sort, so catalog order
  within a provider). The "scoped" list is not sorted: it keeps
  `resolve_model_scope` order (the `enabledModels` pattern order), as Pi's
  `scopedModelItems` keep the session's scope order. A test uses
  deliberately reordered patterns.
  "Current" is `modelsAreEqual` (provider + id). "Default" is pipy's saved
  default (`NativeDefaultsStore`, pipy's counterpart of Pi's
  `defaultProvider`/`defaultModel`).
- Scope: `"scoped"` when scoped models exist, else `"all"`; Tab toggles only
  when scoped models exist; on toggle the selection moves to the current model
  in the new list, else 0, then the filter reruns.
- Filter: empty query → active list, selection clamped. Non-empty →
  `fuzzy_filter(active, query, text)` where `text` is Pi's
  `getModelSelectorSearchText`, `"{provider} {provider}/{id} {provider} {id}"`
  plus `" {name}"` when the model has a name, plus `" default"` for the default
  model; when the stripped, lowered query is a non-empty prefix of `"default"`
  the default rows (from the active list) come first, then the other filtered
  rows. Selection resets to 0.
- Keys (Pi order): `tui.input.tab` scope; `tui.select.up`/`down` wrap
  (no-op on an empty list); `tui.select.confirm` selects (persist false);
  `tui.select.cancel` cancels; `app.models.save` selects with persist true;
  everything else goes to the search input, then the filter reruns. The input
  submit (Enter) is the confirm path.
- Layout, top to bottom (each Pi `Text` wraps at the width):
  `DynamicBorder` (`─` × width in `border`), blank, either
  `Scope: all | scoped` (muted, the active scope in accent) plus
  `tab` (dim) + ` scope (all/scoped)` (muted), or the warning-coloured
  `Only showing models from configured providers. Use /login to add
  providers.`, blank, the search input, blank, the list, blank, the dim hint
  `  Enter to select · Ctrl+S to set as default · Escape/Ctrl+C to cancel`
  (from `keyDisplayText`), `DynamicBorder`. Then pipy's two footer rows.
- List: a 10-row window centred on the selection
  (`max(0, min(sel - 5, n - 10))`); row
  `→ ` (accent) or two spaces, `✓ ` (accent) for the current model or two
  spaces, the id (accent when selected), a space, `[provider]` (muted),
  ` · default` (muted) for the default; `  (i/n)` (muted) when the window
  does not show all rows; then `  No matching models` (muted) when empty, else
  a blank and `  Model Name: <name>` (muted).
- Selection outcome: Enter → `setModel(persist=false)`, status
  `Model: <id>`; save key → `persist=true`, status
  `Default model: <provider>/<id>`; failure → `Error: <message>`.

Search input (Pi `Input`, `ui/components/search_input.py`): prompt `> `,
insert printable text, paste (newlines removed), Backspace, Left/Right,
Home/End (Ctrl+A/Ctrl+E), Ctrl+U (delete to start), Ctrl+K (delete to end),
Ctrl+W (delete word backward). Render: prompt, then the visible text with a
reverse-video cursor cell, padded to the width; when the text does not fit,
Pi's horizontal scroll (half-width window around the cursor, one column
reserved when the cursor is at the end). Deferred (backlog): Pi's `Input`
moves, deletes and draws the cursor by grapheme cluster and measures East
Asian width; pipy's editor and renderer have no grapheme segmentation and
count one column per code point, so the search input edits by code point like
pipy's main editor. Forward Delete is also deferred: pipy's key decoder has no
`Delete` key (`ESC [ 3 ~` decodes as `esc`).

Model switching with `persist`: `ProviderMutationEffects` gains a
`persist_default` flag that reaches `prepare_model_mutation(persist_default=)`
(already present). `/model` in the TUI no longer saves the default except
through the save key (Pi). Other callers keep their behaviour.

## 3. `/thinking` selector

Pi `ThinkingSelectorComponent`: border, blank, `Thinking Level`, blank,
`<Shift+Tab> cycles thinking levels in-session` (`keyDisplayText
("app.thinking.cycle")`), blank, search input, blank, `SelectList` (maxVisible
= item count, primary column min 12 / max 32), blank, dim hint
`  Enter to select · Ctrl+S to set as default · Escape/Ctrl+C to cancel`,
border. Items: label `✓ level` / `  level`, description from
`LEVEL_DESCRIPTIONS` plus ` · default`. Filter: `fuzzy_filter(all, query,
item => value + " " + description)`; after each filter the list is rebuilt
with the previously selected value preselected when still present (else 0).
Keys: `app.thinking.save` first (select with persist when an item is
selected); up/down/confirm/cancel go to the list; everything else to the
input. `SelectList` rendering (`selectedText` accent over the whole row,
`description` muted, `  No matching commands` muted when empty — Pi's literal
text — and the `(i/n)` line) is ported as `ui/components/select_list.py`.

`/thinking <level>` keeps pipy's exact case-insensitive match; an unknown
level becomes `Error: Unknown thinking level "x". Available levels: ….`
(Pi `showError`).

## 4. Slash menu and argument completion

- Command-name filter: Pi `fuzzyFilter(commands, prefix, name without a
  "skill:" prefix)` followed by the `skill:` full-name matches, where
  `prefix` is the text after `/`. An empty prefix keeps the list order. The
  editor state's prefix filter becomes this call (the names are pipy's
  published `/name` list, compared without the slash).
- Accepting a command (Tab/Enter) inserts `/<name> ` with a trailing space
  (Pi `applyCompletion`); Enter then submits (the command runs with the
  trimmed text).
- Argument completion (Pi `getSuggestions` second branch): when the text
  before the cursor starts with `/`, contains a space, and is not a forced Tab
  request, the command name is the text between `/` and the first space and
  the argument text is everything after it. `/model` → `fuzzy_filter(models,
  arg, getModelSearchText)` over the scoped models when any, else the
  available ones, items `value = provider/id`, `label = id`, `description =
  provider`; `/thinking` → `fuzzy_filter(levels, arg, level)`, items
  `value = label = level`. No match → no popup. The popup prefix is the
  argument text; accepting replaces it with the value (no trailing space)
  and, for Enter, does not submit (Pi returns before submit for a non-`/`
  prefix). The best-match index (exact value, else the first value starting
  with the prefix) is preselected (Pi `getBestAutocompleteMatchIndex`).
- The popup row shows the item's description when it has one (Pi
  `SelectList` default layout: primary column 32); `CompletionItem` gains an
  optional `description`.

## 5. `/resume` search

Port `parseSearchQuery` (tokens with quoted phrases, `re:` regex with `i`,
unbalanced quote fallback) and `matchSession` over pipy's search text
`"{id} {name}  {cwd}"` (Pi's text with an empty `allMessagesText`). An invalid
regex matches nothing. pipy keeps its `recent`/`name` sorts (filter only;
Pi's `recent` mode is filter-only too).

## 6. Slash commands are not drawn

Pi draws a user message only for a prompt that reaches the agent. pipy draws
`user_input` before command dispatch. Change: draw after dispatch, only for
`PROCEED_TO_RUN`: the typed text for a plain prompt, the expanded provider
text for a prompt-template/skill run (what the session stores and what a
restored session draws). Built-ins, resource list/reject, extension commands
and unhandled `/…` lines draw nothing.

## 7. Error lines

Pi `showError` maps to the existing `add_error` with `Error: text` (the
`error` block, ` Error: text` in `error`); a leading `pipy: ` is dropped. Used
for the `/model` and `/thinking` failures above. The plain REPL keeps the
notice. Implementation note: none of this slice's paths has a Pi
`showWarning` (the `/model` warnings belong to the catalog refresh, which is
not ported), so no warning line is added here; the other `showWarning` sites
stay in the backlog notice bullet.

## Tests

- Differential: `tests/test_native_fuzzy.py` pins `fuzzy_match` and
  `fuzzy_filter` results computed by Pi's `fuzzy.ts` under Node for a corpus
  (ASCII, separators, swap fallback, exact match, astral and combining
  characters, final sigma, uppercase), and runs a random corpus against Node
  when `node` and `PI_MONO_DIR` exist (skipped otherwise).
- Model selector: rows/sort/filter/default search/scope toggle/keys/layout at
  width 60, save key persists, Enter does not.
- `/model <term>`: exact match switches without saving; a non-match opens the
  selector with the term; scoped models restrict the match.
- Thinking selector filter, preselection and layout.
- Slash menu fuzzy order; argument completion items, Enter/Tab acceptance.
- Session search parse/match.
- No user bubble for built-ins; the expanded text for templates.
- tmux PTY evidence against real Pi: `/model gpt`, `/model` + typing, Tab
  scope, `/thinking` + typing, `/model g` argument popup.

Done when: all of the above pass, `just check`, the parity checks and
`parity_score.sh` are green, and a different-family review is CLEAN.
