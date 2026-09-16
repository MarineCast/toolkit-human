# Extraction validation — 2026-09-16

## Passed

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

## Graphify

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

## Unverified and blocked

- The broader OrcaCast `tests/benchmarks/test_effort_recovery.py` suite cannot collect:
  sightings aggregation still imports `orcacast.domains.environment.seascape...`, from the
  separately removed environmental domain. This pre-existing migration boundary is outside
  the human extraction. Benchmark external-source snapshot paths were updated, but a full
  benchmark run is not validated. Full application CLI/production integration is not claimed.
- No regional rebuild, live acquisition, OSRM/container provisioning, full terrain run,
  scientific equivalence with toolkit-viewshed, remote push or package publication was run.
- Generated regional data and Git history were preserved. Historical audit evidence may retain
  artifact paths. The source extraction does not delete datasets or rewrite previous commits.
