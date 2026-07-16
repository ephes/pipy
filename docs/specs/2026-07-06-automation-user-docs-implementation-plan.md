# Automation User Docs Implementation Plan

Reviewed design: `docs/specs/2026-07-06-automation-user-docs-plan.md` (plan review CLEAN via opus-review-loop).

1. Add `docs/json.md`.
   - Acceptance: page explains user-facing `--mode json`, JSONL framing, example invocation/reader, content/privacy expectations, first header line, common event families, and where to find the full maintainer contract.
2. Add `docs/rpc.md`.
   - Acceptance: page explains user-facing `--mode rpc`, stdin/stdout JSONL requests/responses, async session events, command-family overview, examples, limits, and privacy expectations.
3. Wire documentation navigation and cross-links.
   - Acceptance: `docs/index.md`, `docs/sdk.md`, and `docs/user-documentation.md` reference the new pages and distinguish user pages from `automation-rpc.md`.
4. Update parity planning docs.
   - Acceptance: `docs/backlog.md` and `docs/pi-mono-gap-audit.md` mark automation JSON/RPC user docs as shipped rather than open; no runtime behavior is claimed beyond shipped automation modes.
5. Verify docs and project gates.
   - Acceptance: run `just docs-build` and `just check` before final review.
