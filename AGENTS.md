# toolkit-human agent guidance

## Scope and current state

Population, access, recreation, infrastructure, and human-presence indicators.

This repository contains the installable `human` package extracted on 2026-09-16.
Read docs/ARCHITECTURE.md for ownership/import changes, docs/CONTRACTS.md for scientific
changes, and docs/WORKFLOWS.md before pipeline execution. See docs/MIGRATION.md for scope.
Preserve unrelated changes and read deeper instructions before editing a subdirectory.

## Shared MarineCast context

Before changing repository boundaries, dependencies, shared schemas, provenance, or application
integration, read the MarineCast [infrastructure guide](https://github.com/MarineCast/.github/blob/HEAD/INFRASTRUCTURE.md).
Resolve local paths from this toolkit's checkout root, not the agent's working directory.
For `MarineCast/Toolkits/toolkit-*`, use `../../.github/INFRASTRUCTURE.md`;
for a flat `MarineCast/toolkit-*` layout, use `../.github/INFRASTRUCTURE.md`.
Prefer that local copy when present; in an independent checkout, read the linked document. If it
cannot be retrieved, report that limitation and use the local contracts below; do not invent a
shared standard. These instructions explicitly request that reading; a sibling repository's
`AGENTS.md` is not automatically inherited.

The infrastructure guide owns cross-repository context. This repository owns its implementation
and scientific contracts. Surface conflicts before changing an interface; do not silently replace
an existing local contract with a proposed ecosystem convention.

## Domain contracts

Keep population, accessibility, recreation proxies, observed activity, and reporting effort
separate. Preserve source dates, spatial support, coverage, and proxy limitations. Physical access
does not establish that people were present or reported sightings. Avoid committing individual
location records or restricted source data.

## Implementation boundaries

- Keep this toolkit species-neutral and independently installable; do not require an OrcaCast
  checkout or import through sibling filesystem paths.
- Before adding a pipeline, define source rights, input/output grain, units, spatial and temporal
  support, missingness, provenance, and validation in the repository documentation.
- Add dependency declarations and runnable setup/validation commands with the implementation;
  do not copy viewshed's GDAL stack or commands unless this toolkit actually needs them.
- Prefer deterministic calculations and small synthetic/offline fixtures. Keep credentials,
  downloads, and large generated products out of tracked source.
- Keep unknown and unavailable values distinct from observed zero. Validate uniqueness and join
  cardinality rather than silently dropping conflicting records.

## Validation and completion

For documentation-only work, inspect `git status --short` and the diff, verify references, and run
`git diff --check` from this repository. Install `python -m pip install -e '.[test]'` and run `python -m pytest -q`.
Validate a regular wheel from outside the checkout. Keep config/ and
src/human/resources/config synchronized; workspace paths use HUMAN_WORKSPACE or cwd.
When adding executable behavior, add appropriate checks and document their exact commands here.
Report tests actually run, unverified source acquisition, and any unrun integration paths.

## Codebase navigation

Graphify is optional developer tooling, not a package dependency. Use the checkout's local
`graphify-out/graph.json` for structural questions; use targeted `rg` when missing or stale.
Source/tests outrank contracts, architecture docs and graph output. Start with `explain` for
a known symbol, then direct callers/callees. Use ast-grep for syntax patterns and rg for literals.

```bash
graphify explain "build_access_kernel"
graphify affected "build_access_kernel" --relation calls --depth 1
graphify query "reporting opportunity" --context call --budget 1500
# Run from this checkout to create or refresh its graph:
graphify extract . --code-only --no-cluster
```

Use isolated `graphifyy==0.9.62`; this workspace's installation is in
`~/.local/share/graphify-venv`. Do not generate graphs at a workspace/grouping root.
Graphs and caches are disposable local-only files, excluded from Git; never publish them.
Code-only extraction does not semantically index prose. Review intentional deletions before
using `--force` to bypass shrink protection. Do not install hooks or overwrite this guidance.

Keep human activity, access, physical viewability, reporting and disturbance distinct.
`human.viewshed` is the migrated legacy observation geometry, not proven interchangeable
with `viewshed_toolkit`. Preserve metadata schema keys and validate any scientific replacement.
Species-specific modeling belongs in the consuming application, never an OrcaCast import here.
