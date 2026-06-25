# Provider/model user documentation parity plan

## Gap

The ranked gap audit lists **User documentation parity** as the next broad area
once the shipped extension slices are accounted for. `docs/user-documentation.md`
identifies provider/model docs as implementation slice 3: pipy has the
Pi-shaped provider/model catalog and `/model` workflows, but no outside-in user
page explaining provider setup, `models.json`, auth sources, `--list-models`,
`/model`, thinking, image support, or current limits.

Reference paths:

- Pi: `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/model-registry.ts`
- Pi: `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/model-resolver.ts`
- Pi: `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/auth-storage.ts`
- Pi: `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/provider-display-names.ts`
- Pipy: `docs/provider-catalog.md`, `docs/user-documentation.md`, `src/pipy_harness/native/catalog_data.py`, `src/pipy_harness/native/models_json.py`, `src/pipy_harness/native/auth_store.py`

## Design

The local Pi reference checkout has no committed user-docs directory, so the
parity anchor for this slice is Pi's provider/model product surface in code and
help rather than a page to copy. Pipy also has no existing provider/model user
page (`docs/providers.md`, `docs/models.md`, and `docs/custom-provider.md` are
absent); the existing material is maintainer-facing (`docs/provider-catalog.md`)
or broad usage docs (`docs/usage.md`).

Add `docs/providers.md` as a user-facing guide and wire it into the site nav and
index. The page should describe shipped behavior first, not the maintainer spec:

1. How to inspect available models with `pipy --list-models` and
   `pipy --list-models <search>`.
2. How pipy resolves startup provider/model choices (`--native-provider`,
   `--native-model`) and interactive choices (`/model`, `/scoped-models`).
3. Built-in provider families and their credential sources at a high level,
   avoiding secret values.
4. `models.json` custom provider/model configuration, including the ds4 local
   provider example and where the detailed schema lives.
5. `--thinking`, image-capability metadata, and current adapter limits.
6. Current follow-ons (live Anthropic/Copilot login UX, Vertex API-key auth,
   Anthropic adaptive-thinking request shape, Azure URL/api-version polish,
   local-provider maturity) clearly labeled as not yet complete.

Update `docs/user-documentation.md`, `docs/pi-mono-gap-audit.md`, and
`docs/backlog.md` so the slice is marked shipped and the remaining user-doc gap
moves to sessions/settings/customization/automation/install deep dives. Update
`zensical.toml` so `just docs-build` includes the page.

## Done when

- `docs/providers.md` exists and is linked from `docs/index.md` and
  `zensical.toml`.
- Provider/model docs match current command help and the provider catalog gate.
- Planning docs no longer list provider/model user docs as open.
- `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`,
  `uv run pipy --list-models`, `just docs-build`, and `just check` pass.
