# Native release integrity and remaining production gates

The standalone package and native runtime CI passing does not qualify every Human
product for production. `human audit-manifests` checks supplied build manifests'
artifact and input existence, SHA-256 and declared Parquet row counts. It reports
all failures and exits nonzero when any fail. It rejects ambiguous JSON, and does
not fetch data, infer source completeness, or promote scientific eligibility.

```bash
human audit-manifests --manifest /workspace/product/manifest.json --output /new/audit.json
```

## Immutable static research snapshots

`human publish-static-snapshot` packages the four supported static families
(population, places, boat launch and public shore access) supplied by native build
manifests. It audits **all** their declared artifacts and inputs, copies exact native
artifacts and manifests into a new directory, and builds the existing static matrix
from those verified copies. No scientific formula changes or source refreshes occur.
The retained native manifests include resolved configuration, source rights,
attribution, dates, methods and limitations. Raw inputs stay external; retain them
for full rebuilds. Supplemental input inventories are verified when supplied.

```bash
human publish-static-snapshot \
  --manifest /workspace/population/manifest.json \
  --manifest /workspace/places/manifest.json \
  --manifest /workspace/boat_launch_access/manifest.json \
  --manifest /workspace/public_shore_access/manifest.json \
  --input-manifest /workspace/input-manifest.json \
  --code-sha FULL_COMMITTED_PRODUCER_SHA \
  --output /data/human/releases/NEW_RELEASE_ID
human verify-static-snapshot /data/human/releases/NEW_RELEASE_ID
```

Run the committed regular wheel, and supply its exact Git revision. The command
records software/package, publication-method, native scientific-method and generated
release identities separately. The caller is responsible for matching the revision
to the installed wheel; the CLI does not infer a checkout from an installed package.
Native calculation provenance remains in the checksum-pinned original manifests;
`native-static-union-v1` identifies the union, not a replacement census/access method.

The output is a **research-only native snapshot**, not a conforming application-profile
product or a full Human release. `release.json` inventories relative paths, hashes,
row/field counts and each field's null/zero counts. The reader verifies the inventory
without the originating checkout or raw-input paths. The matrix retains original
uppercase fields, vintages, source record semantics and native statuses. No metric
is silently renamed, resampled, assigned a new status or filled with zero.

A sibling exclusive lock prevents competing publishers for the same destination.
The complete verified directory is renamed into place on the same filesystem. Existing
releases are never replaced. Failed attempts remove only their own staging directory;
process termination may leave a hidden stage/lock for manual inspection. This is
atomic visibility, not a power-loss durability guarantee. Do not remove a lock while
a publisher may still be running. Consumers should pin the delivered release-manifest
hash independently; internal checksums do not authenticate a modified manifest.

## Scope and source readiness assessment (2026-10-04)

Configured main model bounds are west/south/east/north
`[-125.8, 46.85, -121.6, 50.0]`; extended bounds are `[-132, 44, -121, 52]`.
Opportunity defaults span 2020-01-01 through 2026-08-31, while calendar context spans
2020–2030. Static population native support spans WA/OR/BC; it must not be described
as clipped to the model area. At H3 R7 the model rectangle contains 24,553 centroid
cells (59,786,555 dense cell/day rows over 2,435 days). The extended rectangle has
166,915 cells (406,438,025 dense rows). Native occupied/support rows need no fabricated
zero-filled dense grid. Final requested domain and product-specific support must be
confirmed before a regional rebuild; the seven-cell integration pilot is not that scope.

A read-only audit of retained local products found:

| Family | Native evidence | Remaining production gate |
| --- | --- | --- |
| Population | Static R7, US 2020/Canada 2021; all declared files/checksums passed | 28 BC dissemination-area counts unavailable; preserve excluded support and mixed vintages, qualify per-metric coverage before application mapping |
| Places | 750 catalog records in 436 R7 cells; all files/checksums passed | Records are not unique physical sites or visits; authoritative and OSM evidence does not establish complete regional presence |
| Boat access | 367 source records in 305 R7 cells; integrity passed | Partial coverage, duplicate physical sites possible, BC public/ramp/seasonal states unknown and old source vintage |
| Shore access | 3,367 R7 cells; integrity passed | 1,803 BC cells have null accessible fraction; 184 denominator-mismatch flags remain; no invented BC numerator or repaired ratio |
| Calendar | 4,018 native dates; integrity passed | Nonspatial calendar/proxy support; no arbitrary H3 expansion or claim of measured effort |
| Land transport / population travel | Native R7; declared artifacts/inputs passed | Graph vintage, closures/public access, destination coverage and travel assumptions require qualification |
| AIS | Native R6 2020–2024 activity; partial | Two declared historical input files missing; rights/completeness unresolved; no valid observer-effort or acoustic interpretation |
| Ferry | Native route/local-service-day and monthly-derived support; integrity passed | Incomplete history and estimated months; UTC daily conversion requires defensible native evidence or reviewed local-day exception |
| Observer effort | Native weekly proxy | Missing declared configuration input; AIS rights and proxy interpretation unresolved |
| Land reporting opportunity | Native source/target R7, daily/weekly targets R6 | Missing declared configuration and daily target artifact; source completeness and scientific qualification unresolved |
| Water observation opportunity | Native source/target support and weekly components | Missing configuration and daily target artifact, plus one changed upstream manifest; AIS rights and scientific qualification unresolved |
| Planned direct effort, seasonal presence, tourism, recreation, fishing, acoustic exposure | No qualified implemented complete product | Independently sourced evidence, rights, methods and validation are required; no proxy substitution |

These are dated evidence findings, not hardcoded acceptance rules. Rerun the auditor
against the actual selected manifests. A byte-perfect artifact may still be unsuitable.
OSM may supplement mapped access/facility evidence with retained tags and attribution;
it cannot fill AIS activity, surveyed effort, closure histories or missing shore-line
coverage. Existing OSM snapshots are retained without asserting they are current.

The approved shared profile requires per-metric status/coverage, readable fields and
UTC daily support where scientifically defensible. This snapshot intentionally claims
no conformance. Source qualification, explicit mappings, UTC reductions (or reviewed
exceptions), and consumer acceptance remain separate deliverables. Do not label blocked
products final-qualified, replicate R6 into R7, divide monthly totals into invented
daily observations, or treat snapshot publication as model promotion.

## Checks

```bash
python -m pytest -q tests/test_release.py tests/test_static_matrix.py
```

Tests cover null-versus-zero preservation, immutable output, checksum/input/row-count
failure, ambiguous JSON, copied-source independence, concurrency and post-build failure
cleanup. CI repeats these from the installed wheel outside the checkout on both Python
versions. The full native suite and runtime requirements remain in `WORKFLOWS.md`.
