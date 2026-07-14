# GPT-5.6 Sol Codex parity plan

Status: selected next parity slice; proposed design awaiting different-family
review before implementation.

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
| `baseUrl` | `https://chatgpt.com/backend-api` | keep pipy's existing Codex catalog base/legacy endpoint ownership |
| `reasoning` | `true` | required |
| `thinkingLevelMap` | `{minimal: "low", xhigh: "xhigh", max: "max"}` with ordinary levels available | pipy's hand-authored row spells out ordinary levels and adds `max: "max"` |
| `input` | text + image | required |
| `contextWindow` | `372000` on the Codex subscription catalog | required |
| `maxTokens` | `128000` | required |
| `cost` | API-equivalent rates plus long-context tiers in Pi | preserve pipy's existing zero-cost subscription convention; tiered API costing is outside this Codex-only slice |

`reasoning.effort` is optional: omit it when the runtime has no selected
thinking level or the model does not map the level. When Sol selects `max`,
send the exact string `"max"`. `reasoning.summary` remains the Pi-forced
`"auto"` default already sent by pipy, independent of effort. This changes no
auth header, endpoint, OAuth, retry, tool, storage, or streaming fields.

## Pipy design

1. Extend the canonical thinking vocabulary to
   `off|minimal|low|medium|high|xhigh|max`. Reuse that vocabulary in CLI model
   suffix parsing, settings validation, `models.json`, extension controls, and
   RPC rather than introducing a Sol-only exception.
2. Add only the built-in `openai-codex/gpt-5.6-sol` row with the pinned
   metadata. Keep `openai-codex/gpt-5.5` as the default and do not add the bare
   `gpt-5.6` alias.
3. Keep Codex on the legacy provider factory so settings-derived retry policy
   and OAuth behavior remain intact. At the catalog/REPL boundary, map the
   current model's selected thinking level and apply it to the legacy Codex
   provider instance. This must be evaluated each time a provider is built so
   Shift+Tab or extension changes affect the next turn.
4. Give `OpenAICodexResponsesProvider` an optional `reasoning_effort`. Build
   `reasoning` with the existing `summary: "auto"` and add `effort` only when
   mapped. No other request-body fields change.
5. Retain the five ordinary Shift+Tab levels for all reasoning models and append
   `xhigh` and/or `max` only when the active catalog row maps them. Sol therefore
   cycles through both; unrelated models do not gain unsupported levels. Record
   changes in the native session tree and run no provider turn, as today.
6. Render Sol's Codex context budget as `372k` instead of the existing generic
   GPT-5 `272k` status denominator.

Existing `defaultThinkingLevel` startup precedence and generalized unsupported-
level clamping are known adjacent gaps and stay out of scope. This slice only
makes `max` a valid stored value and faithfully maps an explicitly selected
level. RPC continues its documented state/event behavior without claiming live
provider propagation.

## Tests and gates

- Catalog/list-model tests pin Sol's exact provider/id, 372K/128K, image, and
  `max` metadata while pinning GPT-5.5 as default and absence of a bare alias.
- Thinking/model-resolver/models.json/settings/CLI/RPC tests accept `max` and
  continue rejecting unknown levels.
- Codex provider tests capture the request body and prove both
  `{summary: "auto", effort: "max"}` and effort omission when unset.
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
- Generalized Pi clamping, default-thinking precedence, live RPC execution,
  theme-schema changes, and unrelated July Pi extension hooks.

## Done when

`openai-codex/gpt-5.6-sol` is listed, selectable, and constructible; an explicit
`max` selection reaches the next Codex request as
`reasoning: {summary: "auto", effort: "max"}`; Sol uses the 372K status budget;
all affected input/state surfaces accept and preserve `max`; GPT-5.5 remains
the default; docs and changelog match; all gates pass; and the exact final diff
has a direct different-family CLEAN review.
