# Package Install/Update User Docs Implementation Plan

1. Add the package user page.
   - Acceptance: `docs/packages.md` documents trusted-package security, local and
     managed-git source syntax, install/remove/list/config/update workflows,
     settings scopes, package layout, dependencies, filtering, runtime loading,
     and current limitations without implying npm/PyPI or TypeScript extension
     support.
2. Wire the page into user navigation.
   - Acceptance: `zensical.toml` includes the page, `docs/index.md` lists it in
     the outside-in reading order, and quickstart/usage link users to the new
     deep dive.
3. Close the parity-doc gap.
   - Acceptance: `docs/user-documentation.md`, `docs/pi-mono-gap-audit.md`, and
     `docs/backlog.md` describe install/update user docs as shipped while leaving
     broader extension/package runtime follow-ons intact.
4. Verify and review.
   - Acceptance: `just check` is green and the final different-family review is
     CLEAN over this exact docs-only diff.
