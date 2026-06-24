# Extension UI theme controls (rich-UI item E) — design

Status: design / plan for one parity slice. Owning spec:
[`docs/extension-api.md`](../../extension-api.md). Gap source:
[`docs/pi-mono-gap-audit.md`](../../pi-mono-gap-audit.md) §3 follow-on 1
("Theme controls") and the same list in `docs/extension-api.md` (lines ~505–508,
the `ctx.ui` "Theme controls" target bullet).

## What Pi does

`packages/coding-agent/src/core/extensions/types.ts` (the `ExtensionUIContext`)
exposes a theme surface to extension command/shortcut handlers via `ctx.ui`:

- `readonly theme: Theme` — the current theme object used for styling.
- `getAllThemes(): { name: string; path: string | undefined }[]` — every
  available theme with its file path (`undefined` for built-ins).
- `getTheme(name: string): Theme | undefined` — load a theme by name *without*
  switching to it; `undefined` when unknown.
- `setTheme(theme: string | Theme): { success: boolean; error?: string }` —
  switch the active theme by name or object.

Pi's headless runner (`core/extensions/runner.ts`) wires deterministic no-ops
for the non-interactive case: `getAllThemes: () => []`, `getTheme: () => undefined`,
`setTheme: () => ({ success: false, error: "UI not available" })`, and a default
`theme`.

This is theme *selection*, not theme *registration* — themes are contributed
through `resources_discover` / package theme files, already shipped in pipy.

## What pipy has today

- `pipy_harness.native.themes`: `ChromePalette` (pipy's `Theme` analog),
  `available_theme_names(registry=None)`, `is_known_theme`, `resolve_palette`,
  `resolve_active_theme_name(env, store)`, `select_theme(name, environ, store)`,
  `DEFAULT_PALETTE`/`DEFAULT_THEME_NAME`, the ambient package registry
  (`set_active_theme_registry` / `active_theme_registry`), and `NativeThemeStore`.
- The chrome resolves its palette **per render** from `PIPY_THEME` + the store
  (`chrome.py:chrome_style_for` → `resolve_active_theme_name`), so setting
  `PIPY_THEME` (what `select_theme` does) repaints the next frame. This is exactly
  how the `/settings` theme row already applies a live theme
  (`tool_loop_session.py:_run_settings_theme_action`).
- The extension `ctx.ui` surface (`extension_runtime.py`): the `ExtensionUi`
  protocol, the live `ExtensionUiDriver` protocol, the headless `_CollectingUi`
  implementation, and the live `_LiveExtensionUiDriver` in `tool_loop_session.py`.
  It already ships `select`/`input`/`editor`/`confirm`/`set_status`/
  `set_widget`/`set_header`/`set_footer`/`set_title`/`set_working_*`. **No theme
  methods exist yet.**

## Design

Add the four theme members to the extension `ctx.ui` surface, snake-cased to
match pipy's existing methods (`set_status`, `set_widget`):

- `ui.theme` (**property**) → `ChromePalette` — the current active palette.
- `ui.get_all_themes()` → `list[dict[str, str | None]]` — one
  `{"name": <str>, "path": None}` per available theme, default first then sorted
  (the order `available_theme_names()` already returns).
- `ui.get_theme(name)` → `ChromePalette | None` — the palette for `name`, or
  `None` when unknown (does **not** switch).
- `ui.set_theme(theme)` → `dict` `{"success": bool, "error": str | None}` —
  accepts a name (`str`) or a `ChromePalette`; switches the live theme.

### Reads are ambient and deterministic (work headless)

`theme`, `get_all_themes`, and `get_theme` are pure reads of the **ambient**
theme module: `available_theme_names()`, `resolve_palette()`, and
`resolve_active_theme_name()` all consult the globally-installed package theme
registry (`_ACTIVE_REGISTRY`) plus `PIPY_THEME`/the store, with no live TUI
needed. So `_CollectingUi` computes them directly from
`pipy_harness.native.themes` regardless of `has_ui`. This is strictly more
capable than Pi's headless `[]`/`undefined` no-ops while remaining fully
deterministic (the spec's "return a safe default" requirement) — and pipy's
theme registry is genuinely globally resolvable, unlike Pi's TUI-bound one.

- Headless `theme` returns the ambient active palette (env/store/default), never
  `None`, so an extension can always read styling values.
- `get_theme(unknown)` → `None` (fail-soft, matches Pi).

### `path` is intentionally `None` (name-only boundary)

`get_all_themes()` keeps Pi's `{name, path}` dict **shape** so Pi extensions
translate, but `path` is always `None`. The session `ThemeRegistry`
(`theme_files.py`) stores only `name → ChromePalette` and does not retain source
file paths, and `theme_files.py` already states the boundary: "Only the palette
name reaches any persisted state." Leaking absolute package theme file paths to
extension code would widen that boundary, so pipy returns `path: None`
uniformly (built-ins have no path anyway). Documented as a bounded, deliberate
divergence.

### `set_theme` is the only live-mutating member → needs a UI

Switching the live theme mutates process state (`PIPY_THEME`) and only has a
visible effect when a frame can repaint, so it is gated on a live driver like
the other mutating `ctx.ui` methods:

- Live (driver present **and** `has_ui`): delegate to a new driver method
  `apply_theme(name)` → `(ok, error)`. `_LiveExtensionUiDriver.apply_theme`
  calls `select_theme(name, environ=os.environ, store=NativeThemeStore())`
  (sets `PIPY_THEME` so the next render repaints, persists the non-secret name to
  the chrome store) — the exact mechanism the `/settings` theme row uses. Returns
  `{"success": True, "error": None}` on success, `{"success": False,
  "error": <message>}` on an unknown name (`select_theme` fails closed).
- Headless (no driver or not `has_ui`): return
  `{"success": False, "error": "UI not available"}` **without** mutating env —
  exactly Pi's headless `setTheme` contract. A deterministic, side-effect-free
  no-op.
- `set_theme(ChromePalette)`: use its `.name`, then the same path. A palette not
  in the registry fails closed via `select_theme` → `{"success": False, ...}`.
- Fail-soft: any driver exception is caught and mapped to
  `{"success": False, "error": "theme switch failed"}`, matching the
  `try/except` posture of every other `_CollectingUi` driver call.

### Why driver-gated set but ambient reads (not all-driver like other methods)

Other live methods (`select`, `set_widget`, …) have no meaningful headless
behavior, so they delegate to the driver and degrade to a safe default. Theme
*reads* are different: pipy already resolves the active theme globally for the
chrome on every render, so the data exists headless. Exposing it is both
correct and useful; only the *mutation* needs a live frame. This keeps
`extension_runtime.py` importing `pipy_harness.native.themes` (a one-directional
import — themes does not import extension_runtime, no cycle).

## Files to change

1. `src/pipy_harness/native/extension_runtime.py`
   - `ExtensionUiDriver` protocol: add `apply_theme(self, name: str)
     -> tuple[bool, str | None]`.
   - `ExtensionUi` protocol: add `theme` (property) + `get_all_themes`,
     `get_theme`, `set_theme`.
   - `_CollectingUi`: implement the four members (ambient reads via the themes
     module; `set_theme` driver-gated + fail-soft). Import `ChromePalette`,
     `available_theme_names`, `resolve_palette`, `resolve_active_theme_name`,
     `DEFAULT_PALETTE`, `NativeThemeStore` from `pipy_harness.native.themes`.
2. `src/pipy_harness/native/tool_loop_session.py`
   - `_LiveExtensionUiDriver.apply_theme(name)` → `select_theme(...)` and return
     `(ok, None if ok else message)`.
3. `tests/` — focused unit tests (see acceptance criteria).
4. Docs: `docs/extension-api.md` (mark theme controls shipped; record the
   `path: None` boundary), `docs/pi-mono-gap-audit.md` and `docs/backlog.md`
   (strike "theme controls" from the rich-UI follow-on list), and the
   extension-api changelog/release-notes row if present.

## Constraints (from `AGENTS.md` / track invariants)

- No new runtime dependencies; stdlib + manual validation only.
- Metadata-first archive privacy preserved: theme reads/switches touch no
  session JSONL, Markdown, or sidecar. (`select_theme` only writes the
  non-secret theme *name* to the chrome store, as `/settings` already does.)
- Fail-soft: a misbehaving extension or driver must never crash the handler;
  every driver touch is wrapped, mirroring the existing `ctx.ui` methods.
- Reuse existing boundaries (`themes` module, `_CollectingUi`,
  `_LiveExtensionUiDriver`); do not add a parallel theme path.

## Acceptance criteria / done-when

1. `ui.theme` returns the ambient active `ChromePalette` both with a live driver
   and headless (env override honored), never `None`.
2. `ui.get_all_themes()` returns one `{"name", "path": None}` per
   `available_theme_names()` entry, default-first ordering, including any
   package-registered theme (proven by installing a `ThemeRegistry` via
   `set_active_theme_registry`).
3. `ui.get_theme(name)` returns the matching palette and `None` for an unknown
   name, without changing the active theme.
4. `ui.set_theme("high-contrast")` with a live fake driver returns
   `{"success": True, "error": None}` and the driver applied it (via
   `select_theme`, `PIPY_THEME` updated); an unknown name returns
   `{"success": False, "error": <msg>}`; a `ChromePalette` argument works by
   name; a driver that raises yields `{"success": False, "error": ...}`.
5. Headless `ui.set_theme(...)` returns
   `{"success": False, "error": "UI not available"}` and does **not** mutate
   `PIPY_THEME`.
6. `just check` green; the extension conformance gate
   (`scripts/parity_checks/...` if one covers `ctx.ui`) still passes.
7. Docs updated; the gap is struck from the gap sources.
8. Different-family review CLEAN over the full diff.
