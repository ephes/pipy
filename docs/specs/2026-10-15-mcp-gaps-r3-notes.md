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
