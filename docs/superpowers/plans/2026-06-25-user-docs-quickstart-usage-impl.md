# User docs quickstart + usage implementation plan

1. Add user-facing pages.
   - Acceptance: `docs/quickstart.md` and `docs/usage.md` exist, follow the Pi quickstart/usage shape, use pipy command names/state paths, and label limitations instead of overclaiming parity.
2. Wire the pages into user entry points.
   - Acceptance: README links to the two pages, `docs/index.md` lists them before maintainer specs, and `zensical.toml` includes both pages in navigation.
3. Update parity/planning docs.
   - Acceptance: `docs/user-documentation.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` mark the quickstart + usage documentation slice as shipped while leaving remaining user-doc slices open.
4. Verify docs and repo gates.
   - Acceptance: `just docs-build` and `just check` pass; if `.pre-commit-config.yaml` exists, `prek run --all-files` passes.
5. Review and commit.
   - Acceptance: different-family review returns CLEAN over the complete diff after gates pass, then commit the reviewed diff on `main` without pushing.
