# toolkit-human agent guidance

## Supported pipeline as of 2026-10-17

`human.get_human_data()` and `human get-human-data` are the only supported collection/delivery entry points. They produce exactly the three current Human Parquets, retain original inputs and immutable receipts outside output, and clean run-owned intermediates. Read docs/get-human-data.md. Canonical implementations live under src/human/pipelines/{delivery,population,native,calendar}; root/domain compatibility modules are aliases. Shared utilities live under src/human/utils. Preserve scientific schema identifiers when changing Python module paths. The active CLI/catalog no longer register family, ferry, routing, legacy calendar-effort, matrix/snapshot or modeling products. Direct legacy APIs/configuration remain compatibility only; earlier guidance below documents those modules rather than additional supported outcomes.

Pipeline checks: `PYTHONTZPATH='' python -m pytest -q tests/test_pipeline.py tests/test_static_delivery.py tests/test_native_features.py tests/test_calendar_lookup.py tests/test_packaging.py tests/human/test_feature_catalog.py`. Test a regular wheel outside the checkout with the pipeline tests and their fixture modules. `human init` installs config/human.yaml, config/census-sources.v1.json and .env.example. Keep that file and the resource copy synchronized. Never sweep pre-existing sources or caches as intermediate cleanup.

## Scope and current state

Data-only toolkit for human layers and effects: population, access, recreation,
infrastructure, human activity and source-supported pressure/exposure/effects data.
Acquisition, cleaning, harmonization, aggregation and documented data derivations
belong here. Ecological response models, fitted effects, predictions and
observation/reporting-process assumptions belong in consuming modeling work.

Active Human products expose documented human-domain data. Weather/viewability-weighted land,
water and AIS reporting-opportunity proxies belong to modeling. Exclude them from
active CLI families, new workspace defaults, feature catalogs and study exports;
atmospheric visibility is not a Human acquisition/completion gate. Retain explicit
legacy import paths, templates and historical artifacts for existing research.
See docs/product-scope.md; do not implement new modeling combinations here.

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
`git diff --check` from this repository. Provision the native runtime using `docs/legacy-workflows.md#native-validation-environments`.
Run `python scripts/check_native_runtime.py gdal` before the full suite and
`python scripts/check_native_runtime.py osrm` before the native road-profile fixture.
Install `python -m pip install -e '.[test]'` and run `python -m pytest -q`.
Set `PYTHONTZPATH=''` for the full suite to use packaged timezone rules consistently,
as documented in the native validation workflow.
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

Static matrix checks: `PYTHONPATH=src python -m pytest tests/test_static_matrix.py -q`;
`human export-static-matrix --manifest <native-build-manifest> --output <new-parquet>`.
The contract is documented in `docs/static-matrix.md`.

Native release checks: `python -m pytest -q tests/test_release.py tests/test_static_matrix.py`.
See `docs/release-readiness.md` for checksum auditing, immutable research snapshot publication,
and remaining source/scientific/profile gates. Snapshot integrity is not production eligibility.
Optional static record-count profile: `python -m pytest -q tests/test_static_profile.py`.
Ferry UTC normalization: `python -m pytest -q tests/human/activity_and_effort/test_ferry_utc.py`.
Retain native local service dates; never infer ambiguous DST folds or monthly daily observations.

UTC schedule subset pilot: `python -m pytest -q tests/human/activity_and_effort/test_ferry_utc_schedule.py`.
See `docs/utc-delivery-pilot.md`; retain unresolved-time records and never treat schedules as actual traffic.

UTC land research method: `python -m pytest -q tests/human/activity_and_effort/test_land_utc_context.py`.
See `docs/utc-scientific-methods.md`; regional qualification and external AIS input rights remain open.

Static population checks: `python -m pytest tests/test_population_static.py tests/test_static_matrix.py tests/test_study.py -q`.
Use `human get population --help` for standalone population queries. See `docs/population.md`
for bounded preparation, strict coverage, caches, and local study exports. No live acquisition
is needed for deterministic fixtures.

Product-scope checks: `python -m pytest -q tests/test_packaging.py tests/human/test_feature_catalog.py tests/human/test_structure.py tests/test_study.py`.
Regenerate/check the active feature catalog; run the full native suite and the
outside-checkout installed-wheel checks for executable scope changes.

Human-only current-registry delivery: see `docs/static-delivery.md` and run
`PYTHONTZPATH='' python -m pytest -q tests/test_static_delivery.py tests/test_calendar_lookup.py tests/test_population_static.py`.
`python -m human.static_delivery --help` is the standalone static/calendar delivery entry point.

Radius-aware source collection: `python -m human.population_collection --help`;
`PYTHONTZPATH='' python -m pytest -q tests/test_population_collection.py tests/test_population_static.py tests/test_static_delivery.py`.
See `docs/population-collection.md`. Collection plans never assert complete census
values or geographic support and never alter the shared reporting mask.

AIS acquisition and aggregation are owned by toolkit-ais. Human has no AIS adapter, CLI/default config, dataset/feature registration or study export. Keep retained raw/release evidence and legacy research readers intact; do not recreate the adapter here.

Final-only delivery: `Data/Human` contains final Parquets only. Read `docs/final-delivery.md`; keep processing workspaces and integrity receipts outside that folder. Regression checks: `python -m pytest -q tests/test_static_delivery.py tests/test_native_features.py tests/test_study.py tests/test_calendar_lookup.py`. The current three products retain distinct H3, jurisdiction/local-date and native-geometry grains.

Local settings follow argument > process environment > .env precedence. Keep private .env ignored and preserve all existing values/secrets. Only START_DATE, END_DATE, DATA_DIR and optional VERBOSITY are read; dates select local civil calendar rows, not census vintages. There is no REBUILD/append setting. Run human preflight without provider/study writes. For changed supported code, run Pyflakes and scripts/check_pipeline_docs.py as documented in WORKFLOWS, a final-tree full native suite with safe fixture workspace, and an outside-checkout regular-wheel run including acquisition/settings fixtures.
