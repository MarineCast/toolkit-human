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
