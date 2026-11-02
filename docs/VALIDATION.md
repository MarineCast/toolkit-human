# Validation record

## Extraction validation (2026-09-16)

The extraction results below are historical. The later static-population section records
the 2026-10-14 implementation and regional study validation.

### Passed

- Toolkit suite: **326 passed** on Python 3.14, including scientific fixtures, missingness,
  generation/publication guards, package independence, workspace/resource contracts and
  release-input identity/checksum rejection. Command: `PYTHONPATH=src python -m pytest -q`.
- OrcaCast affected consumer suite: **38 passed**, using the installed wheel. Tests cover
  application land modeling/shadow evaluation, input identity, pilot geometry, schema dispatch,
  water reliability, viewability publishing/missingness and catalog contracts.
- Wheel built using `python -m pip wheel --no-deps --no-build-isolation .` and installed
  into a clean target directory using the existing scientific environment. No fresh dependency
  solve or second Python-version runtime was tested. All package source parses as Python 3.11.
- Outside the toolkit and application checkouts, wheel initialization preserved existing
  configuration, shipped the routing Lua profile and viewshed reference config, and exercised
  **35 supported family CLI help routes** with all `orcacast` imports explicitly blocked.
- Wheel SHA-256: `a51be852efeec664792075f468f647ed699b337be300d5fe2704ab6c717b578b`.
  The local wheel is under ignored `dist/`; source remains the reviewable deliverable.
- Inventory: **363 entries**, **334 migrated files** and **29 copied shared files**.
  Every destination exists and every move-marked source is absent. Species-specific experiment
  and publishing-test relocations within OrcaCast are additional to this toolkit inventory.
- OrcaCast's former human source directory is absent. No legacy human namespace/path remains
  in executable Python, Makefiles or package metadata. All remaining application source parses.
- Relative Markdown links in toolkit guidance/source docs resolve. Toolkit and organization
  remotes match the documented names; remote availability/publication was not checked.
- `git diff --check` passed in toolkit-human, organization docs and OrcaCast.

### Graphify

Executed `graphify extract . --code-only --no-cluster` in this checkout with Graphify 0.9.62.
The local graph contains **3,587 nodes / 12,355 edges**. `graphify explain build_access_kernel`
resolved to the correct package source and its callers. LLM extraction cost: **0 input / 0 output
 tokens**. Prose/notebooks are not semantically indexed; there is no clustered report or HTML
visualization in this code-only navigation workflow. Graph caches are ignored and local-only.

Read-only diagnostics report **1,542 dangling-endpoint edges**, **17 self-loops**, and **480
same-endpoint relationships that would collapse in an undirected simple-graph projection**.
There are no missing-endpoint edges. These are navigation limitations, not failed scientific
tests; consult source/tests rather than treating the graph as exhaustive dependency evidence.
Detailed diagnostics are in local `graphify-out/HEALTH.txt`. An empty source module may be
re-queued by the extractor because it has no AST nodes.

### Unverified and blocked

- The broader OrcaCast `tests/benchmarks/test_effort_recovery.py` suite cannot collect:
  sightings aggregation still imports `orcacast.domains.environment.seascape...`, from the
  separately removed environmental domain. This pre-existing migration boundary is outside
  the human extraction. Benchmark external-source snapshot paths were updated, but a full
  benchmark run is not validated. Full application CLI/production integration is not claimed.
- No regional rebuild, live acquisition, OSRM/container provisioning, full terrain run,
  scientific equivalence with toolkit-viewshed, remote push or package publication was run.
- Generated regional data and Git history were preserved. Historical audit evidence may retain
  artifact paths. The source extraction does not delete datasets or rewrite previous commits.

## Static population implementation (2026-10-14)

The implementation and tests live in toolkit-human. The owner checkout was clean before
this change; no sibling source checkout or immutable release was modified.

Executed commands from this checkout:

```bash
/Users/tylerstevenson/miniforge3/envs/orcacast314/bin/python -m venv --system-site-packages /private/tmp/human-population-314-env
/private/tmp/human-population-314-env/bin/python -m pip install --no-deps --no-build-isolation -e '.[test]'
/private/tmp/human-population-314-env/bin/python -m pip check
/private/tmp/human-population-314-env/bin/python -m pytest -q --tb=short
/private/tmp/human-population-314-env/bin/python scripts/update_human_feature_catalog.py --check
/private/tmp/human-population-314-env/bin/python -m pip wheel --no-deps --no-build-isolation . -w /private/tmp/human-population-wheel
```

Result: **367 passed**, Python 3.14.6 with GDAL 3.12.3; `pip check` and generated
catalog check passed. No test skipped in this environment. A regular wheel was installed
with `--no-deps` in the fresh `/private/tmp/human-population-final-env` environment.
From `/private/tmp/human-population-wheel-validation`, copied population, static-matrix,
and study-export tests passed **40/40** against that installed wheel (verified import
from its `site-packages`). Installed `human init`, population CLI dry-run, and the runnable
offline example succeeded. These checks do not require the viewshed executable for the
population path. `git diff --check` and changed local documentation links were checked.

An earlier Python 3.12 environment without `osgeo` passed the focused population/export
checks; its full suite had 352 passes, 4 skips and 2 existing native GDAL import failures.
The complete suite was subsequently run successfully in the GDAL-capable 3.14 environment.
No live provider acquisition was exercised or claimed.

Synthetic benchmark command:

```bash
/private/tmp/human-population-314-env/bin/python examples/benchmark_population.py --workspace /private/tmp/human-population-benchmark-v12-20261014
```

Fresh computations used 600 synthetic source polygons, 37 R7 targets, and radii
25/50/100/250 km on macOS 26.6.2 arm64, Python 3.14.6. Direct source intersections took
1.018 s with 153.33 MiB process peak RSS; contained-group optimization took 0.581 s with
153.70 MiB process peak RSS. Maximum absolute difference was `2.91e-11` persons. These
are measured small-fixture results, not a regional runtime prediction; process high-water
RSS includes prior allocations in each process.

The bounded real study query used the configured bbox `(-125.8, 46.85, -121.6, 50.0)`,
24,553 R7 centers and the default four radii, with bands and explicit partial mode.
It ran the installed wheel outside the checkout, offline, against 205,664 retained
original source polygons (WA 135,894; OR 62,891; BC 6,879). Population computation took
**3,135.09 seconds (52 min 15 s)** with **2,869.78 MiB process peak RSS**, using four
workers and 128-target batches on the environment above. This includes reading/checking
prepared sources and building indexes, with an existing source/projection coverage-union
cache; it excludes the earlier raw-source normalization and the subsequent native activity
export. No provider download occurred. Exact library versions and executed/validated wheel
fingerprints are retained with the local results.

The original BC geography retains 20 unavailable population values in the acquisition
window. Eighty-one cross-border geometry conflicts conservatively quarantine 86 whole
source geographies without clipping or renormalizing their support. Unverified offshore
support remains unknown. Complete cell counts/densities exist for 16,619 targets (including
3,084 supported zeros); all 250 km canonical totals remain null. Separately named
available-source subtotals are partial estimates and do not establish population coverage.
The default strict call rejects this study product, including on a cache hit.

Files were saved under the explicitly requested workspace `Data/Human/` destination:
24,553 static rows, 4,018 calendar rows (2020–2030), 211,672 R7 ferry rows and 4,095,670
R6 land-opportunity rows. Native spatial activity retains actual 2020-01-01 through
2026-08-31 support and America/Los_Angeles local days with original UTC interval bounds.
It was checksum-verified and exported from retained research releases, not rebuilt or
empirically recertified. No missing/future activity or daily population rows were created.

The portable equivalent is `examples/export_study.py --workspace WORKSPACE --output-dir NEW_OUTPUT
--source-manifest NORMALIZED_SOURCES.json --static-release STATIC_RELEASE --dynamic-release
DYNAMIC_RELEASE --allow-partial --workers 4`, from this checkout with configured common
and calendar files. The exact executed script, four-target real comparison (zero value/QC
difference), full cache reuse/strict-rejection check, test logs and generated validation
report are retained locally under `Data/Human/.workspace/provenance/` and `validation.json`.
Live acquisition, empirical census-allocation accuracy and new regional activity/terrain/
routing builds remain unverified. Generated data, raw sources and local run provenance
are excluded from the source commit. No package release or data redistribution was performed.

### Integration with current Git main (2026-10-14)

After the study run, the population branch was rebased onto `fd29579`, retaining the
newer native-runtime, immutable-release and UTC research interfaces. The population
method and the executed study calculations were unchanged. Validation was repeated
against the combined source:

```bash
/private/tmp/human-population-314-env/bin/python scripts/check_native_runtime.py gdal
/private/tmp/human-population-314-env/bin/python scripts/check_native_runtime.py osrm
PYTHONTZPATH='' /private/tmp/human-population-314-env/bin/python -m pytest -q --tb=short
/private/tmp/human-population-314-env/bin/python -m pip wheel --no-deps --no-build-isolation . -w /private/tmp/human-population-git-wheel
```

Result: **437 passed in 44.27 seconds**, with no skips, including the five synthetic
OSRM routing cases. GDAL preflight and OSRM 26.8.0 preflight passed. Routing tests
required permission to bind temporary loopback ports outside the filesystem sandbox.
`PYTHONTZPATH=''` aligned Python/pandas with packaged `tzdata` 2026.3; the older conda
system timezone database otherwise failed the existing post-2026 BC civil-time test.
CI and the native workflow now specify that setting.

A new regular wheel (`sha256:1a826d59176be6f18e48d88fb9ffc85ac6040239c8ee5f0305b2034ed10f2116`)
was installed with `--no-deps` in `/private/tmp/human-population-git-env`, a fresh
virtual environment using the existing scientific system packages. From
`/private/tmp/human-population-git-wheel-tests`, the installed-wheel script passed
resource/init checks, all 35 family help routes with application imports blocked,
and the population API/config/CLI checks. `pip check` passed; the copied population,
study, matrix, release, profile and UTC tests passed **88/88 in 10.03 seconds** with
`PYTHONTZPATH=''`. The study test reads its calendar fixture from installed package
resources. CI now includes the population and study tests in its outside-checkout
wheel run.

All 23 configuration files matched their packaged copies, the generated feature
catalog was current, all 26 local documentation links/anchors resolved, and
`git diff --check` passed. This is local Python 3.14/macOS evidence; Python 3.11 and
Linux CI execution are separate checks. No new regional acquisition, activity,
terrain, routing graph, empirical validation or package release accompanied Git
integration.

## Human source-product scope (2026-10-15)

Land/water/AIS observation-opportunity combinations were removed from active CLI
families, default workspace setup, generated feature collections and study exports.
Legacy imports, templates, native methods and artifacts remain for existing readers;
no replacement modeling implementation was activated. See [product scope](product-scope.md).

- Focused packaging/catalog/structure/study checks: 24 passed.
- Full native toolkit suite, after GDAL and OSRM preflights: 451 passed.
- Regular installed wheel outside the checkout: resource/init checks and all 27
  active CLI help routes passed with application imports blocked; 38 population,
  study and import tests plus 2 native legacy-compatibility smoke tests passed.
  The wheel environment reused the existing scientific dependencies; a fresh
  independent dependency resolution was not run.
- The 16 retained feature collections compare exactly with their previous
  definitions; only the 9 modeling-proxy collections were removed.
- Local Human study catalogs/date availability exclude the land proxy; current
  source artifacts and retained legacy Parquets passed checksum checks. Prior
  catalog/validation inventories were preserved before the scope update.

No new source pull, historical land/AIS rebuild, model fit, replacement consumer
integration, publication or deployment was performed for this scope change.

## Early WSF route source history (2026-10-15)

The local MarineCast Human history workspace now retains published route totals
from all 16 already-acquired WSF annual/quarterly PDFs for 2009-2016. The new
`Data/Human/2009-forward/raw/wsf-route-period-history.parquet` has 950 rows:
765 route records, 145 overlapping route-group subtotals and 40 system totals.
Support is eight calendar years and 32 calendar quarters in America/Los_Angeles;
dates are not relabeled UTC or expanded to daily/H3 observations.

The companion metadata/source contract defines printed rider, vehicle and
passenger counts, hierarchy, explicit route-label aliases, missingness, source
rights/attribution and checksums. Historical reporting names retain separate keys
where equivalence is unverified. Blank fields remain null: 124 vehicle cells and
145 passenger cells. All 555 available-value hierarchy comparisons match their
printed parents; missing child fields remain visible. All 40 system rider totals
agree with the independent system-total extraction, and 11 rendered-source spot
checks cover the 2009, 2014 and 2016 layouts.

The 570 annual-versus-four-quarter metric comparisons include 214 nonzero source
differences across routes, subtotals and system totals. Both publications are
retained; consumers need an explicit benchmark choice. No reconciliation, daily
reconstruction, vessel-hour inference, H3 allocation or model fit was performed.

Executed checks:

- Six source-extraction guard/regression tests passed, including rejection of
  duplicate identities, omitted printed-zero rows, changed counts and negatives.
- All 31 existing ferry source, PDF fallback, pipeline, UTC, schedule and weekly
  tests passed with `PYTHONTZPATH=''` in the existing Python 3.14 Human runtime.
- The companion `provenance/validate_wsf_route_history.ipynb` executed top to
  bottom using a temporary local Jupyter kernel; source/artifact/producer hashes,
  coverage, hierarchy, arithmetic and comparisons passed.
- All 2,850 exported count cells match the extraction evidence, preserving nulls.
  Ten pre-existing data artifacts retain their checksums; active study product
  descriptors and date availability are unchanged.

The source scripts, contract, detailed validation, test logs and executed notebook
are under the history workspace's `provenance/`. PDF extraction used pdfplumber
0.11.9; tabular validation/export used pandas 3.0.3 and PyArrow 24.0.0. This local
source-data addition changes no toolkit executable interface. The full native
suite and installed-wheel checks were not repeated for it. No new remote
acquisition, redistribution or application integration was performed. Early WSF
daily activity, early BC route history and AIS coverage remain separate gaps;
the full-period Human inventory remains partial.


## Physical port ownership migration (2026-10-19)

The current three-Parquet delivery now includes 84 NGA World Port Index native points, preserving all 84 former Governance R6/R8 source IDs and exact original point WKB. Seventy occupy the existing R7 reporting cells. All 64 prior static arrays, all 32,870 calendar rows and all 6,752 prior native rows match in Arrow values/types/nulls/order; 292 marina destinations remain separate. Static output is 13,461 × 67 and native output is 6,836 × 12. No source acquisition occurred. The full 2,951-row original and rights/provenance remain in the ignored Human source store.

Supported tests: 144 passed, one legacy test deselected, both from an isolated source tree and a regular installed wheel outside the checkout without PYTHONPATH. The wheel API/CLI/import-boundary check and pip check passed. Pyflakes and 11 supported documentation files/30 links plus configuration/resource parity passed. The clean source tree excluded pre-existing Finder `config/.DS_Store`; direct initial configuration parity checks detected that unrelated file. A first isolated-workspace structure check lacked the expected source path; the complete clean tree resolved it. The installed test copy emitted only an unregistered legacy-marker warning. The full legacy terrain/routing suite and live-provider acquisition were not rerun. No Safari or native UI control was taken, and no caffeinate was started.

[Port contract and reproduction](ports.md) records count/zero/partial-inventory semantics, source IDs and source rights. Exact before/after hashes, runtime versions and source selections are in ignored `data/provenance/ports-migration-20261019/validation.json` and `selection.json`; current external receipts are in `data/delivery/ports-migration-20261019`. Successful task-owned publication staging was cleaned after final readback. No commit, push or merge accompanied this migration.
