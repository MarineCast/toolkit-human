# Radius-aware Human source collection

Population source acquisition must cover the support of the calculations, not only
the displayed H3 registry. The current study retains the same exact R7 targets and
25/50/100 km scientific radii. The collector materializes the union of every full
target cell and the maximum-radius WGS84 geodesic neighborhood of every H3 center.
This also handles disconnected components and interior holes without relying on an
edge-cell heuristic. An additional 50 m acquisition-only margin accounts for the
25 m polygon approximation tolerance. It does not change the scientific radii.

The union is a source-selection footprint, not new target cells and not an assertion
of complete census geography or counts. Provider file queries use its conservative
bounding envelope; complete intersecting original census polygons and their original
areas are retained. Census source polygons are never clipped to the footprint or
renormalized. Complete existing official archives are reused by checksum; expanding
a footprint does not require redownloading an archive already covering that area.

## Plan and prepare

```bash
python -m human.pipelines.population.collection \
  --study-config ../../config/study.v1.json \
  --workspace /absolute/path/to/task-owned-workspace \
  --specification /absolute/path/to/source-specification.json \
  --plan-only
python -m human.pipelines.population.collection \
  --study-config ../../config/study.v1.json \
  --workspace /absolute/path/to/task-owned-workspace \
  --specification /absolute/path/to/source-specification.json
```

Both commands print the collection-plan JSON path. `--plan-only` writes the geometry
and plan but never reads/acquires census data or calculates population. The plan
binds `footprint.parquet` by exact file hash, the geometry by normalized WKB hash,
and all targets by the checksum of sorted LF-terminated H3 IDs. The source manifest
path/hash appear after preparation. `examples/prepare_static_study.py` delegates to
this installed entry point with the same arguments.

Preparation is offline by default. Explicit live mode retains official-host URL,
request/byte budgets and no redirects/retries from the census adapter. There is no
nationwide sweep or arbitrary provider API loop. Every input file is hash checked
again before completing normalization. Jurisdictions are diagnosed against the
actual required union, avoiding false missing jurisdictions at bounding-box corners.

Use the plan's `source_manifest` for `python -m human.pipelines.population.delivery`. Delivery
rejects a radius-aware bundle prepared for different targets, resolution or radii.
Earlier explicit source manifests without the additive collection capability remain
supported. Population and product schema versions, units, missingness and original
area-allocation formulas are unchanged. No shared study config or shared physical
mask is edited by the collector.

## Interpreting remaining gaps

A sufficient acquisition envelope guarantees selection of input geographies for all
neighborhoods. It cannot make an unavailable source count known, reconcile conflicting
national boundaries, or establish whether an uncovered shoreline sliver is land or
water. The pinned physical context is used exactly as supplied and is never buffered
to fill coverage. Unknown geographic support outside it remains unknown. Canadian
incompletely enumerated counts and quarantined border counts retain their original
nulls. Derived collected estimates retain their explicit missing-as-zero flags.

The previous retained raw-source preparation used a broader 250 km bounding envelope,
even though the current public product excludes 250 km. Therefore this explicit
100 km collection change is not expected to remove residual shoreline/count gaps
merely by increasing acquisition width. Report actual before/after coverage from the
full rebuild; do not claim completeness from the footprint alone.

Checks:

```bash
PYTHONTZPATH='' python -m pytest -q tests/test_population_collection.py tests/test_population_static.py tests/test_static_delivery.py
```

Tests cover true geodesic boundary points at 0.5-degree bearings, disconnected
regions, coarse cells extending past the radius, deterministic target ordering,
invalid plans, full original source retention, actual-footprint jurisdiction checks,
artifact bindings and delivery mismatch rejection. Full native suite and regular
outside-checkout wheel checks remain required by AGENTS.md.
