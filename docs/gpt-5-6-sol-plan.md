# GPT-5.6 Sol Codex parity plan

Status: shipped 2026-07-14. Design reviewed CLEAN by a different-family reviewer
(Pi / GPT-5.6 Sol) before implementation; the full code+docs diff also passed a
different-family review. Generalized cross-provider thinking clamping is the one
named follow-on (see the resolutions and deferrals below).

## Gap

Pi 0.80.6 exposes `openai-codex/gpt-5.6-sol` and a seventh thinking level,
`max`. Pipy stops at GPT-5.5 and validates/cycles only through `xhigh`, while
its legacy Codex provider always sends `reasoning.summary = "auto"` without
the selected effort. This slice adds usable GPT-5.6 Sol support to pipy's
preferred hosted subscription path without broadening into every GPT-5.6
distribution surface.

## Pi reference

- `~/src/pi-mono/packages/ai/scripts/generate-models.ts`: the explicit Codex
  catalog list and `CODEX_GPT_56_CONTEXT = 372000`; no bare `gpt-5.6` Codex
  alias.
- `~/src/pi-mono/packages/ai/src/providers/openai-codex.models.ts`: generated
  Sol row.
- `~/src/pi-mono/packages/ai/src/models.ts`: ordered levels and model-aware
  support/clamping.
- `~/src/pi-mono/packages/ai/src/api/openai-codex-responses.ts`: Codex request
  `reasoning: { effort, summary: "auto" }`.
- `~/src/pi-mono/packages/ai/test/max-thinking.test.ts` and
  `packages/coding-agent/test/max-thinking.test.ts`: focused reference tests.

The model row fields changed by this slice are pinned as follows:

| Field | Pi value | Pipy treatment |
| --- | --- | --- |
| `id` | `gpt-5.6-sol` | required, exact; no alias |
| `name` | `GPT-5.6 Sol` | display as `GPT-5.6 Sol (Codex/ChatGPT)` following existing pipy rows |
| `api` | `openai-codex-responses` | required, legacy Codex adapter family |
| `provider` | `openai-codex` | required |
| `baseUrl` | `https://chatgpt.com/backend-api` (catalog value; the request URL is `…/backend-api/codex/responses`, appended by `resolveCodexUrl`, `openai-codex-responses.ts:575-577`) | reuse pipy's existing Codex provider base `https://chatgpt.com/backend-api/codex` (`catalog_data.py` `_PROVIDER_BASE["openai-codex"]`), to which pipy appends `/responses`; the final request URL is byte-identical to Pi's. No base/endpoint change — assert the constructed URL in tests |
| `reasoning` | `true` | required |
| `thinkingLevelMap` | Pi ships `{"xhigh":"xhigh","max":"max","minimal":"low"}`; ordinary `low`/`medium`/`high` are absent from the map but `getSupportedThinkingLevels` (`models.ts:410-419`) treats an *unmapped, non-`null`* ordinary level as identity-supported | pipy's `supported_thinking_levels` derives support from map keys, so pipy's hand-authored row must SPELL OUT the identity ordinary levels: `{off: None, minimal: "low", low: "low", medium: "medium", high: "high", xhigh: "xhigh", max: "max"}`. Note `minimal → "low"` is a non-identity mapping (Pi), distinct from pipy's shared `_REASONING_LEVELS` (`minimal → "minimal"`), so Sol gets a dedicated map constant, not `_REASONING_LEVELS_XHIGH` |
| `input` | text + image | required |
| `contextWindow` | `372000` on the Codex subscription catalog | required |
| `maxTokens` | `128000` | required |
| `cost` | API-equivalent rates plus long-context tiers in Pi | preserve pipy's existing zero-cost subscription convention; tiered API costing is outside this Codex-only slice |

`reasoning.effort` is optional. For the Codex path it is omitted **only** when
the runtime has no selected thinking level (`None`) or the level is `off`; any
other stored level is first clamped to the nearest level the model supports and
then mapped, so an unsupported level is NOT dropped but emitted as its clamped
effort (for example, a stored `max` on `gpt-5.5` becomes `reasoning.effort:
"xhigh"`, matching Pi). When Sol selects `max`, the value is supported and maps
to the exact string `"max"`. The `None`/`off` guard is applied BEFORE
`clamp_thinking_level` is called, so the clamp helper never receives an empty
level. `reasoning.summary` remains the Pi-forced `"auto"` default already sent
by pipy, independent of effort. This changes no auth header, endpoint, OAuth,
retry, tool, storage, or streaming fields.

## Pipy design

1. Extend the canonical thinking vocabulary to
   `off|minimal|low|medium|high|xhigh|max`. Reuse that vocabulary in CLI model
   suffix parsing, settings validation, `models.json`, extension controls, and
   RPC rather than introducing a Sol-only exception.
2. Add only the built-in `openai-codex/gpt-5.6-sol` row with the pinned
   metadata. Keep `openai-codex/gpt-5.5` as the default and do not add the bare
   `gpt-5.6` alias.
3. Keep Codex on the legacy provider factory so settings-derived retry policy
   and OAuth behavior remain intact. At the catalog/REPL boundary, resolve the
   current model's selected thinking level with a Codex-scoped
   `map_thinking_level(model, clamp_thinking_level(model, level))` (the clamp
   mirrors Pi's per-request `clampThinkingLevel` in `openai-codex-responses.ts`)
   and apply the result to the legacy Codex provider instance. This must be
   evaluated each time a provider is built so Shift+Tab, extension, or model-
   switch changes affect the next turn.
4. Add `clamp_thinking_level(model, level)` to `thinking.py`, a faithful port of
   Pi's `models.ts:clampThinkingLevel` (forward-then-backward walk over the
   extended level order, `off` passthrough). It is used only at the Codex
   effort-resolution boundary in this slice; other providers are untouched. The
   boundary guards `None`/`off` before invoking it, so the helper is only called
   with a concrete requested level.
5. Give `OpenAICodexResponsesProvider` an optional `reasoning_effort`. Build
   `reasoning` with the existing `summary: "auto"` and add `effort` only when a
   clamped-and-mapped value exists. No other request-body fields change.
6. Retain the five ordinary Shift+Tab levels for all reasoning models and append
   `xhigh` and/or `max` only when the active catalog row maps them. Sol therefore
   cycles through both; unrelated models do not gain unsupported levels. Record
   changes in the native session tree and run no provider turn, as today.
7. Render Sol's Codex context budget as `372k` instead of the existing generic
   GPT-5 `272k` status denominator.

Existing `defaultThinkingLevel` startup precedence and generalized unsupported-
level clamping are known adjacent gaps and stay out of scope. This slice only
makes `max` a valid stored value and faithfully maps an explicitly selected
level. RPC continues its documented state/event behavior without claiming live
provider propagation.

## Plan-review resolutions (omit-vs-clamp, URL, RPC)

The first different-family plan review raised three warnings plus one
suggestion. Their pinned resolutions:

1. **Targeted Codex-scoped request-time clamping lands here; generalized
   cross-provider clamping stays deferred.** Pi resolves the per-request effort
   through `clampThinkingLevel(model, options.reasoning)`
   (`openai-codex-responses.ts:468`; helper at `models.ts:421-440`), which walks
   `EXTENDED_THINKING_LEVELS` forward then backward to the nearest supported
   level — this clamp lives INSIDE the Codex responses request path. pipy today
   OMITS an unsupported level (`thinking.py:map_thinking_level` returns `None`).
   Because introducing `max` newly lets a user cycle Sol to `max` and then switch
   to a Codex model that lacks it (e.g. `gpt-5.5`), leaving a stored-but-
   unsupported `max`, this slice would *worsen reachability* if it only omitted.
   Resolution: port Pi's `clampThinkingLevel` as a pipy helper
   (`thinking.py:clamp_thinking_level`) and apply it **at the Codex effort-
   resolution boundary only** — the exact place Pi clamps. This is a faithful,
   bounded match of Pi's Codex request path, NOT the "generalized thinking
   clamping" (clamp uniformly across every provider and every effort-label
   surface) that the gap audit (`pi-mono-gap-audit.md`, Active-next-gap note)
   **explicitly excludes**. Concretely, the Codex effort becomes
   `map_thinking_level(model, clamp_thinking_level(model, stored_level))`,
   evaluated each turn (so Shift+Tab / extension / model-switch all take effect
   on the next turn). Consequences, all matching Pi and tested: stored `max` on
   Sol → `max`; stored `max` switched to `gpt-5.5` Codex → clamps to `xhigh`;
   stored `xhigh` on a Codex model mapping neither → clamps to `high`; `off`/
   unset → omit. Non-Codex providers keep their existing omit behavior; unifying
   clamp across them, plus the non-Codex effort-label surfaces, remains the named
   follow-on **"port `clampThinkingLevel` so unsupported stored levels clamp
   (not omit) uniformly across providers and label surfaces."** The
   Shift+Tab cycle still only offers the model's supported levels (via
   `getSupportedThinkingLevels`); clamping only governs a level that survived a
   model switch or explicit CLI/settings/extension store.

2. **`baseUrl` is unchanged; the finding conflated catalog value with request
   URL.** Pi's Sol catalog `baseUrl` is `https://chatgpt.com/backend-api`
   (`openai-codex.models.ts:109`); the `/codex/responses` suffix is appended at
   request time by `resolveCodexUrl` (`openai-codex-responses.ts:575-577`). pipy
   already stores `_PROVIDER_BASE["openai-codex"] = "https://chatgpt.com/backend-api/codex"`
   and appends `/responses`, producing the identical request URL. This slice
   changes no base/endpoint field; a test asserts the constructed Sol request URL
   equals the existing Codex URL.

3. **RPC `max` acceptance is consistent with pipy's state-only RPC surface.**
   pipy's RPC thinking level (`automation/rpc.py:_thinking_level`,
   `_THINKING_LEVELS`) is a stored/reported state value for ALL levels today; it
   already does not drive a live provider turn on change. Adding `max` to the
   accepted set keeps parity with that documented state/event behavior. Live RPC
   provider propagation stays deferred exactly as it is for `off…xhigh` — this is
   not a `max`-specific divergence.

4. **Suggestion accepted:** add a Codex request-body test proving the
   non-identity `minimal → "low"` mapping emits `reasoning.effort: "low"`.

## Tests and gates

- Catalog/list-model tests pin Sol's exact provider/id, 372K/128K, image, and
  `max` metadata while pinning GPT-5.5 as default and absence of a bare alias.
- Thinking/model-resolver/models.json/settings/CLI/RPC tests accept `max` and
  continue rejecting unknown levels.
- Codex provider tests capture the request body and prove `{summary: "auto",
  effort: "max"}`, the non-identity `minimal → {effort: "low"}` mapping, and
  effort omission when the level is unset/`off`.
- Codex clamp regression tests (mirroring Pi's `clampThinkingLevel` in the Codex
  request path): stored `max` switched to a Codex model lacking `max` clamps the
  emitted `reasoning.effort` to `xhigh`; stored `xhigh` on a Codex model mapping
  neither `xhigh` nor `max` clamps to `high`; a `clamp_thinking_level` unit test
  covers forward-then-backward walking and the `off` passthrough.
- A test asserts the constructed Sol request URL matches the existing Codex
  `…/backend-api/codex/responses` endpoint.
- REPL-state tests prove the legacy Codex provider retains its injected retry
  policy while receiving the current mapped effort.
- TUI/session tests prove Sol's model-aware cycle reaches `xhigh` then `max`,
  persists the change, invokes no provider, and uses a 372K status budget.
- Update `CHANGELOG.md`, provider/user/parity docs, and refresh the parity-plan
  and gap-audit Pi reference from 0.78.0 to 0.80.6.
- Run focused tests, provider-catalog conformance, `just check`, and a direct
  fresh-context Claude Opus review until CLEAN.

## Explicitly deferred

- `openai/gpt-5.6-sol`, Azure, OpenRouter, Vercel, Terra, and Luna rows.
- Long-context pricing-tier schema and API billing calculations.
- Changing any provider default from GPT-5.5.
- Generalized Pi clamping (porting `clampThinkingLevel` so unsupported stored
  levels clamp instead of omit), default-thinking precedence, live RPC execution,
  theme-schema changes, and unrelated July Pi extension hooks.

## Done when

`openai-codex/gpt-5.6-sol` is listed, selectable, and constructible; an explicit
`max` selection reaches the next Codex request as
`reasoning: {summary: "auto", effort: "max"}`; Sol uses the 372K status budget;
all affected input/state surfaces accept and preserve `max`; GPT-5.5 remains
the default; docs and changelog match; all gates pass; and the exact final diff
has a direct different-family CLEAN review.
