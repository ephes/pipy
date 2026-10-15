# MCP parity gaps, goal check r3: notes for the shared files

Slices of `handoff/triage-r3.md` run on a parallel track that does not edit
`CHANGELOG.md`, `docs/backlog.md`, the plan
(`docs/specs/2026-10-12-mcp-parity-plan.md`), the acceptance note
(`docs/acceptance/2026-10-13-mcp-parity.md`) or
`tests/test_mcp_parity_audit.py`. This file holds the text meant for them; a
later merge folds it in.

## T3: Small parity edges (D2, F1, F3, Q4, U2, D8)

### D2: `read` resizes and converts images with Pillow

**Changelog (Unreleased, Changed):**

- The built-in `read` tool processes an image file as Pi's `read` does when
  Pillow (the `images` extra) is installed: a BMP is converted to PNG and an
  image over 2000 x 2000 pixels or 4.5 MB of base64 is resized, with Pi's
  hint lines (`[Image converted from image/bmp to image/png.]`, `[Image:
  original WxH, displayed at wxh. ...]`) after `Read image file [<type>]`, in
  direct use and for `image(tools.read(...))` in a script. Before, such an
  image was omitted with `[Image omitted: ...]` even with Pillow. `read`
  follows `images.autoResize`: off, a PNG, JPEG, GIF or WebP file is attached
  as it is and a BMP is still converted. Without Pillow nothing changes: a
  fitting PNG, JPEG, GIF or WebP is attached and a larger image or a BMP gets
  Pi's omission text.

**Plan:**

- Section 2.11 text near l.4108-4128 (S39 part 3, "Pi's `processImage` needs
  Photon; pipy has no image codec, so `tools/read_image.py` keeps what Pi does
  without changing pixels ... A larger image, one that is cut short or
  malformed, and a BMP get Pi's `[Image omitted: ...]` ..."): add after it
  "Since goal check r3 (D2), with Pillow `read` goes through
  `image_process.process_image_or_omission` (the TLS-20 processor): a BMP is
  converted, a larger image resized, with Pi's hint lines in the note, and
  `images.autoResize` is passed to `read` (`tools/registry.py`
  `with_image_auto_resize`, as Pi's `agent-session.ts` passes
  `autoResizeImages`). The structure checks above are what `read` does
  without Pillow."
- DEV-68: add "`read` uses the same processor for image files since goal
  check r3 (D2); without Pillow it attaches a fitting PNG, JPEG, GIF or WebP
  and omits a larger image or a BMP with Pi's text."

**Backlog:** READ-IMG item ("pipy does not resize an image over 2000 x 2000
pixels or 4.5 MB of base64, convert a BMP to PNG or apply EXIF orientation;
such an image is omitted with Pi's text"): replace by "Without Pillow (the
`images` extra) `read` does not resize an image over 2000 x 2000 pixels or
4.5 MB of base64 or convert a BMP; such an image is omitted with Pi's text.
With Pillow it does (goal check r3, D2). The `Current model does not support
images` note is not added."

**Acceptance (TLS-20 row, add tests):**
`test_native_tools_read.py`: `test_read_tool_resizes_a_large_image_with_pillow`,
`test_read_tool_converts_a_bmp_to_png_with_pillow`,
`test_read_tool_keeps_the_size_when_auto_resize_is_off`,
`test_read_tool_omits_an_image_pillow_cannot_decode`,
`test_without_pillow_auto_resize_off_attaches_a_fitting_type_as_it_is`;
`test_native_tool_result_image_normalize.py`:
`test_the_read_tool_follows_the_auto_resize_setting`. Row note: add "`read`
processes image files the same way (r3 D2)."

**Audit test references:** none changed.

### F1: `!pattern` exclusions for `builtin:` extensions

**Changelog (Unreleased, Fixed):**

- A built-in extension's `extensions` entries follow Pi's override rules
  (`isEnabledByOverrides`, `applyAutoloadDisabledPatterns`): a `!pattern`
  glob such as `!builtin:mcp` or `!builtin:*` turns it off, which was ignored
  before, so MCP stayed on and its servers launched. `+builtin:<name>` and
  `-builtin:<name>` now name one exactly (before they were globs, so
  `-builtin:*` turned all three off; in Pi it matches none). In the user
  array a `-` entry wins over a `+` entry, which wins over a `!` entry,
  whatever their order (before, the last matching entry won). A matching
  `!`, `+` or `-` entry in a trusted project's array decides, the last one
  winning (before only `+` and `-` entries counted there).
  `pipy config enable|disable builtin <name>` replaces every `+` or `-`
  entry that names the built-in exactly (also `./builtin:<name>`), so an
  earlier `-` entry cannot keep it off.

**Plan:**

- DEV-32: replace "A `builtin:` directive follows pipy's pattern rules for
  the `extensions` array (last matching directive wins, `fnmatch`)." with "A
  `builtin:` entry follows Pi's override rules since goal check r3 (F1); a
  `!` glob is matched with `fnmatch` against `builtin:<name>`, where Pi's
  minimatch also tries the path relative to the settings directory (so
  `**/builtin:mcp` matches in Pi and not in pipy)."
- EXP-12 note: the settings entries include Pi's `!pattern` exclusions.

**Acceptance (EXP-12 row, add test):**
`test_native_mcp_discovery.py`: `test_builtin_extension_enablement` (the r3
F1 cases), `test_settings_read_the_builtin_entry_per_scope` (now also `!`
user-level and project entries) and
`test_config_command_replaces_every_exact_entry_of_the_builtin`.

**Audit test references:** none changed.

### F3: `!command` output trimmed like JavaScript `trim()`

**Changelog (Unreleased, Fixed):**

- A `!command` value (auth `api_key`, `models.json` keys, MCP `env` and
  `headers` values) is read as Pi reads it: the output is decoded with
  invalid UTF-8 replaced by U+FFFD (before, it made the value unavailable)
  and trimmed with JavaScript's `trim()` whitespace set, so a byte order mark
  or U+3000 around the value is dropped while U+001C to U+001F and U+0085,
  which Python's `strip()` removed, are kept.

**Plan:** CFG-25 note, if it describes the trim: "trimmed as JavaScript's
`trim()` (goal check r3, F3)". No DEV row changes.

**Acceptance (CFG-25 row, add tests):** `test_native_auth_store.py`:
`test_resolve_config_value_command_runs_and_strips`,
`test_command_unicode_success_and_nonzero` (the r3 F3 cases),
`test_command_invalid_utf8_is_replaced` (renamed from
`test_command_invalid_utf8_is_unavailable`, which no shared file names).

**Audit test references:** none changed.

### Q4: `auth.json` with a UTF-8 byte order mark

**Changelog (Unreleased, Fixed):**

- An `auth.json` that starts with a UTF-8 byte order mark is read (Pi
  `stripBom`), by the auth store and by the provider token an
  `auth.provider` MCP server sends. Before, such a file read as empty, so a
  stored login was ignored and the server asked for sign-in.

**Plan:** OAU-36 note: "a leading byte order mark is skipped (r3 Q4)". No DEV
row changes.

**Acceptance (OAU-36 row, add test):** `test_native_mcp_provider_auth.py`:
`test_an_auth_file_with_a_byte_order_mark_is_read`.

**Audit test references:** none changed.

### U2: `set_active_tools` drops names the allowlist does not activate

**Changelog (Unreleased, Fixed):**

- With a `--tools` allowlist, `set_active_tools` (and adding tools to the
  selection) drops an MCP-named tool the allowlist does not let be
  activated, as Pi's `_applyToolLoadout` does, instead of keeping it in the
  selection. Before, a later re-registration of such a tool as `deferred`
  declared it without any activation or `tool_search` load.

**Plan:** EXP-06 note: "the selection drops a name `_isActivatable` refuses
when it is set (r3 U2); it is still narrowed when read, for reloads". No DEV
row changes.

**Acceptance (EXP-06 row, add test):** `test_native_tool_capabilities_mcp.py`:
`test_a_selection_drops_an_mcp_tool_the_allowlist_does_not_activate`.

**Audit test references:** none changed.

### D8: `search_tools()` accepts a whole float `limit`

**Changelog (Unreleased, Changed):**

- `search_tools(query, limit=8.0)` in a codemode script is accepted as
  `limit=8`, as Pi's `Number.isInteger` accepts it; `2.5`, `inf`, `nan`,
  `True` and strings still fail with `search_tools() limit must be a positive
  integer`.

**Plan:**

- DEV-27: drop "`limit` must be an `int` (`8.0` is refused)."
- Section 2.11 text near l.3343 ("`limit` must be an `int` (not `bool`, not
  a float such as `8.0`, which JavaScript cannot tell from `8`)"): replace
  with "`limit` must be an `int` or a whole `float` (Pi `Number.isInteger`;
  not `bool`); since goal check r3 (D8) `8.0` counts as `8`".

**Acceptance (CMD-07 row):** the tests named there
(`test_search_tools_ranks_callable_tools_and_returns_all_tools_entries`,
`test_discovery_argument_errors_use_pis_messages`) now cover it; row note:
drop any "8.0 is refused".

**Audit test references:** none changed.

## T4: Tool rows: wrapped built-ins and the nested live window (S11, D6)

### S11: a resolver wrapping a built-in row keeps its live parts

**Changelog (Unreleased, Fixed):**

- A tool renderer resolver that wraps the renderers `next()` gives for a
  built-in tool keeps the row's live parts, as a wrapper of Pi's built-in
  renderers does: a wrapped `edit` row shows the diff preview while the
  call runs, and a wrapped `bash` row shows `Elapsed` from the start and
  ticks it every second until the result. Before, both were missing until
  the result arrived. A resolver that leaves the row to the generic
  renderers (returns `None`, or renderers without callables) still gets
  neither, as in Pi.

**Plan:**

- DEV-45: drop "but a wrapped `edit` row has no live preview and a wrapped
  `bash` row no ticking time while it runs"; the row then reads "A
  resolver's `next()` gives `None` for an extension tool without renderers.
  pipy's own rows are callable renderers a resolver can wrap (since G9),
  with the live `edit` preview and the ticking `bash` time (since goal check
  r3, S11). `renderShell` is not ported. A resolver that raises, or returns
  something that is not renderers, is left out; Pi lets the exception
  through."

**Acceptance:**

- Section "Accepted deviations" bullet "Nested calls have no session-wide
  sequential mode ... and a wrapped built-in row has no live preview or
  ticking time (DEV-45; EXT-15)": drop ", and a wrapped built-in row has no
  live preview or ticking time (DEV-45; EXT-15)".
- EXT-15 row: add tests
  `test_native_extension_tool_renderer_resolver.py`:
  `test_a_wrapped_edit_row_shows_the_live_preview`;
  `test_native_ui_coding_session_renderer.py`:
  `test_a_wrapped_bash_row_ticks_elapsed_until_the_result`,
  `test_a_bash_row_left_to_the_generic_renderers_does_not_tick` (r3 S11).
  Limits column: replace "A wrapped `edit` row has no live preview, a
  wrapped `bash` row no ticking time; `renderShell` is not ported." with
  "`renderShell` is not ported."

**Audit test references:** none changed.

### D6: the nested live window keeps the latest calls

**Changelog (Unreleased, Fixed):**

- A codemode row no longer freezes after 256 nested calls: the live row
  keeps the latest 256 (past that the oldest finished call goes for each
  new one; a running call stays), so the latest calls and failures stay
  visible, as Pi's renderer shows the latest of every call. The collapsed
  `... (N earlier calls, ctrl+o to expand)` counts every call left out, and
  an expanded row starts with `... (N earlier calls, not kept)` when calls
  were let go. Before, later calls were not shown at all. The `nestedCalls`
  record in the result is unchanged (the first 256 calls, `complete: false`
  past that).

**Plan:** CMD-26 note: "the bound is the durable record's; the live row
keeps the latest 256 calls, where Pi keeps every call (r3 D6)". No DEV row
records the live cap, so none changes.

**Acceptance (CMD-26 row, add test):** `test_native_nested_lifecycle.py`:
`test_the_live_row_keeps_the_latest_calls_past_the_record_bound`;
`test_live_status_args_bounds_sanitize_and_parent_mismatch` now asserts the
rolling window (r3 D6).

**Audit test references:** none changed.
