> Current study output layout: [final-only Human delivery](final-delivery.md). Public outputs are `static.parquet`, `dynamic-calendar.parquet` and optional `static-native.parquet`, with metadata embedded and receipts outside the final directory. Older filenames and workspace run tables below are historical evidence. Native ferry activity, when explicitly exported, retains its own dynamic Parquet; AIS belongs to toolkit-ais.

# Static resident population, version 1

For the current approved coastal-registry static product and the separate jurisdiction/date
calendar lookup, use [Static Human study delivery](static-delivery.md). Dated regional-run
tables below describe historical outputs, not the current coverage report.

`human.get_population` estimates residents inside full H3 cells and WGS84 geodesic
neighborhoods of their H3 centers. It is static: US 2020 and Canada 2021 remain separate
source vintages in a mixed-vintage baseline. People are assumed uniformly distributed
within each original census polygon. This is not measured visitation, access, observer
effort, detection, or whale occurrence. Neighborhoods overlap and must not be summed
across targets as regional population totals.

The new product is separate from legacy `population_context` and
`water_weighted_population_*mi` schemas. No water, AIS, viewshed, dates or routing inputs
are required. Original polygon areas remain denominators; acquisition windows select
intersecting complete polygons without clipping or renormalizing their populations.

Normalized inputs contain unique `source_id`, `jurisdiction`, `country`, `vintage`,
`population` (nonnegative finite persons or null), and polygon `geometry` with CRS.
Coverage polygons are explicit assertions of complete geographic source support, including
known empty support only when documented. They do not assert that every population value
is available. Unknown/suppressed values keep their geometries. Uncovered areas are unknown;
an empty intersection never establishes zero. Coverage is not inferred from bounding boxes,
source centroids, or coastal H3 outputs. Source rights/attribution must accompany inputs.
Census US sources are public domain; Canadian sources retain the Statistics Canada Open
Licence. No source data are bundled or published by this workflow.

Strict requests reject any incomplete metric. `allow_partial=True` retains rows, nulls
incomplete canonical totals, and reports separately named available-source subtotals.
Completeness and the `area_allocated_estimate` measurement type are separate. Geographic
coverage is never presented as a population percentage. Offshore areas without an explicit
known-empty support assertion remain unknown. Numerical zeros require complete support.

Computation uses one regional WGS84 Lambert azimuthal equal-area projection, recorded in
metadata, for original source areas, intersections, and full-cell density denominators.
H3 edges are sampled on WGS84 geodesics; radii are built with `pyproj.Geod.fwd`.
Sagitta tolerance and edge sampling are explicit parameters, tested by refinement.
Nearby custom radii share boundary azimuths to preserve nesting under discretization.
Polar, antimeridian and extents spanning over 30 degrees are rejected rather than projected
silently. All H3 resolutions 0–15 are supported subject to target/resource limits.
BBox selection includes H3 centers within inclusive bounds and never clips target hexagons.

## Public API and CLI

```python
from human import get_population

result = get_population(
    bbox=(-125.8, 46.85, -121.6, 50.0), h3_res=7,
    radii_km=(25, 50, 100), include_bands=True,
    workspace='./human-workspace', mode='offline', allow_partial=False,
)
print(result.table)                       # pandas; materializes the complete table
for batch in result.iter_batches(1024):   # bounded reading
    pass
result.to_parquet('./outputs/population-r7-static.parquet')
```

Use `target_h3=['...']` instead of `bbox`; inputs are mutually exclusive. H3 IDs
must be unique and at the requested resolution. Radius values are strictly increasing,
positive, finite kilometres (default maximum 500). No dates are required.
`resource_limits={...}` overrides packaged defaults; `dry_run=True` returns a plain
plan without writing data, building population, or downloading. The result exposes
`metadata`, lazy `table`, `iter_batches`, `path`, and `cache_hit`. Existing exports
are not replaced. Exports include `.metadata.json` with checksum, methods, exact
coverage/status counts, source fingerprints, source vintages and benchmark measurements.

```bash
human --workspace ./human-workspace get population \
  --bbox -125.8 46.85 -121.6 50.0 --h3-res 7 \
  --radii-km 25 50 100 --include-bands --mode offline --dry-run
human --workspace ./human-workspace get population \
  --bbox -125.8 46.85 -121.6 50.0 --h3-res 7 \
  --include-bands --mode offline --source-manifest SOURCES.json \
  --output ./outputs/population-r7-static.parquet
```

Configuration lives in `config/data/human/demography_and_presence/population_static.yaml`
and its synchronized installed copy. `--config` selects an explicit configuration.
The static workflow does not load the legacy population/water configuration.

## Preparing source inputs

The supported normalized API is
`human.pipelines.population.static.PopulationSources(geographies, coverage, metadata)`.
`write_sources(bundle, directory)` writes a versioned, checksum-verified content-addressed
bundle. Pass its manifest as `source_manifest=` to `get_population`. Source IDs must be
unique across countries/tiles; overlapping source support beyond the declared numerical
tolerance is rejected, including different IDs representing the same geography.
Supply provenance, attribution and source rights in `metadata`.

The census adapter reuses the existing Canadian Profile-table reader and retained Census
population caches. It does not call the legacy coastal pipelines:

```python
from human.pipelines.population.static.census import prepare_census_sources

manifest = prepare_census_sources(
    workspace='./human-workspace',
    source_extent=(-129.3, 44.5, -118.0, 52.3),  # obtain actual extent with dry_run
    mode='offline',
    specification={
        'max_downloads': 0, 'max_download_bytes': 0,
        'us': [{'jurisdiction': 'US-WA', 'state_fips': '53',
                'geography': '/inputs/tl_2020_53_tabblock20.zip',
                'population': '/inputs/block_population_2020.parquet'}],
        'canada': [{'jurisdiction': 'CA-BC', 'province_code': '59',
                    'geography': '/inputs/lda_000b21a_e.zip',
                    'population': '/inputs/bc_da_population_2021.parquet'}],
    },
)
```

This example intentionally leaves jurisdictions/support unspecified; strict estimates
require complete support and values. Geography selection uses polygon intersection;
full selected original polygons and zero/null source values are retained. US inputs use
`GEOID20`, `STATEFP20`, table `geoid20`/`population_2020`; Canadian inputs use
`DGUID`, `PRUID`, table `geo_id`/`population_2021`. These columns are configurable.
For official Canadian Profile ZIPs set `population_id: DGUID` and
`population_column: 'Population, 2021'`. The U.S. adapter also accepts an explicitly
selected `population_column: POP20` without a separate population table.

Any supplied US state or Canadian province is supported at the declared 2020/2021
baseline. Configure every needed jurisdiction; the adapter never assumes WA/OR/BC
cover arbitrary requests. Optional `jurisdiction_inventories` contain `path`, `id_column`
and `prefix` (e.g. TIGER states, `STATEFP`, `US-`). Canadian national DA inputs also
identify provinces intersecting the conservative acquisition envelope. Required/missing
jurisdictions are retained in provenance. The envelope can include places outside the
actual union of neighborhoods. Areas beyond supplied inventories remain explicit
unknown support, including unverified offshore regions. There is no implicit global
land/ocean mask and no water file is required.

Live mode requires explicit bounded source entries with `geography_url`/`population_url`,
`max_downloads` and `max_download_bytes`. Only configured official HTTPS Census/StatsCan
file endpoints are accepted. Requests have timeouts, no redirects or automatic retries,
and stop at the byte budget. No automatic nationwide sweep or population API request
loop occurs. Offline preparation requires existing local files and never downloads.

Source references: [US 2020 TIGER documentation](https://www.census.gov/programs-surveys/geography/technical-documentation/complete-technical-documentation/tiger-geo-line.2020.html),
[Canadian 2021 boundary reference guide](https://www150.statcan.gc.ca/n1/pub/92-160-g/92-160-g2021001-eng.htm),
[Statistics Canada Open Licence](https://www.statcan.gc.ca/en/terms-conditions/open-licence).
Canadian cartographic geography omits coastal water; its unsupported offshore complement
must not be interpreted as an observed population zero.

## Execution and verification

Sources are normalized and cached by raw input hashes, vintage, acquisition scope and
adapter parameters. Product identity includes source manifest/content fingerprints,
sorted target cells, resolution, radii, bands, method version and geometry parameters.
Batch size/workers do not alter scientific identity. Prepared coverage geometry is
owned by each worker; spatial trees are initialized before concurrent reads. The
source/projection-specific coverage union also has a checksum-verified WKB cache.
Compatible hits verify checksums and
skip source loading, provider access, spatial index building and population calculation.

The optimized engine prepares the complete-support union for exact containment tests,
then groups original source polygons in a spatial index, adds fully
contained groups, and clips only boundary sources. It reuses the conservative largest
neighborhood candidate groups across radii, including full-cell support at coarse H3
resolutions. It never spreads H3 totals uniformly or substitutes centroid inclusion.
There is no dense target-source matrix. Target batches stream to Parquet row groups;
source count, target count, elapsed time and process peak RSS are reported in metadata.
Resource controls bound targets, sources, workers, radii, batches and elapsed calculation.
Peak RSS is the process high-water mark, including preprocessing, not a claimed hard
memory cap. Population source preprocessing remains in memory within configured limits.

```bash
python examples/population_offline.py --workspace /tmp/synthetic-human-example
python -m pytest tests/test_population_static.py tests/test_static_matrix.py tests/test_study.py -q
```

The example labels all geometry, populations, and complete empty-world support synthetic.
Tests cover conservation, partial domains, geodesic boundary allocations, cross-border
inputs, missingness, zeros, ordering/batching/workers, resolution-specific centers,
offline operation, cache reuse/invalidation, schema rejection and geometry refinement.
Tests compare the grouped calculation against direct original-source intersections.
Refinement checks the change at a deliberately boundary-crossing source, rather than
claiming population is exact at a given geometric tolerance.

## Static and regional exports

The existing `export-static-matrix` accepts the new population `.metadata.json` alongside
legacy build manifests at R7. It adds a distinct `population_static_v1` component and
retains the native companion metadata; it never replaces legacy population semantics.

`human.legacy.study.export_study` accepts a `PopulationResult`, calendar configuration and
explicit checksum-verified retained static/dynamic artifact descriptors. It writes
`human-r7-static.parquet`, `calendar-temporal.parquet`, and one spatial dynamic Parquet
per native ferry family/resolution. Metadata/receipts are kept outside the final folder. The calendar covers configured
dates. Activity stays at its actual source dates; no unavailable history/future is filled.

Weather/viewability-weighted reporting-opportunity proxies are excluded from new
Human studies; their combination belongs to modeling. The historical run below
predates this [scope decision](product-scope.md), and its retained land artifact is
legacy modeling evidence rather than an active Human input or completion gate.
Native local-day activity keeps its timezone and original UTC interval boundaries,
including DST. Historical R6 land-opportunity artifacts retain their original support
as legacy modeling evidence outside new Human studies. These are
research exports, not fresh empirical validation or a production promotion. Existing
immutable releases are read only. Export has per-file atomic writes, not a whole-directory
transaction; use a new output directory on retries.

Cross-border source boundaries can disagree. By default overlapping source support fails.
For an explicitly reviewed partial research run, choose
`cross_border_overlap: quarantine` in the census specification, or call
`quarantine_cross_border_overlaps(bundle)` explicitly. Every original source geography
involved in a cross-border overlap retains its full geometry and becomes unknown in
its entirety. The original population remains in a provenance-only column and is
excluded from available-source subtotals. The policy records all affected source IDs,
overlap areas and quarantined original population. Canonical metrics intersecting any
quarantined geography stay null. This conservatively flags more than the disputed
slivers; it does not choose a boundary authority or impute population. Same-country
overlaps still fail. The normalized bundle retains the regional equal-area CRS used
for reconciliation, avoiding a further transformation of near-coincident boundaries.

`examples/export_study.py` is a runnable wrapper around this public API. It reads the
model bbox from `config/common.yaml`, uses the configured calendar period, accepts
explicit normalized source and retained release paths, and defaults to offline strict
coverage. Use `--allow-partial` explicitly when the coverage report justifies a partial
research export. It never rebuilds AIS, viewshed, routing or legacy opportunity families.

Run the wrapper from this checkout, supplying paths to an initialized workspace and
explicit retained releases:

```bash
python examples/export_study.py \
  --workspace /path/to/human-workspace --output-dir /path/to/new-output-directory \
  --common-config /path/to/human-workspace/config/common.yaml \
  --calendar-config /path/to/human-workspace/config/data/human/temporal_context/calendar.yaml \
  --source-manifest /path/to/sources.json \
  --static-release /path/to/static-release --dynamic-release /path/to/dynamic-release \
  --allow-partial --workers 4
```

The 2026-10-14 workspace run saved these files under `Data/Human/`:

| File | Grain and support | Rows |
| --- | --- | ---: |
| `human-r7-static.parquet` | One row per R7 location; mixed-vintage static population and retained static features | 24,553 |
| `calendar-temporal.parquet` | One row per date, without H3; configured 2020–2030 period | 4,018 |
| `ferry-r7-dynamic.parquet` | Native R7 ferry location/local-day features; 2020-01-01–2026-08-31 | 211,672 |
| `land-opportunity-r6-dynamic.parquet` | Native R6 land-opportunity location/local-day features; 2020-01-01–2026-08-31 | 4,095,670 |

Each Parquet has a `.metadata.json` companion; `catalog.json` records all products.
The two spatial files retain their different native resolutions. Source data, generated
study products, caches and run provenance remain local and are excluded from the source
commit. Complete cell population/density support exists for 16,619 targets, including
3,084 supported zeros. All canonical 250 km totals are null because support is incomplete.
See the [validation record](VALIDATION.md#static-population-implementation-2026-10-14)
for measured runtime, input reconciliation, cache checks and unverified paths.

The current default static population output contains per-cell count/density and
cumulative 25/50/100 km totals. The shared-study preparation and delivery explicitly
use those three radii, with no 250 km metric or associated coverage/subtotal fields.
The 250 km results described in the historical validation record above are from the
previous scope; previously generated products are not rewritten by this change.

### Official Geographic Attribute File input

A Canadian source entry may set `population_format: statcan_gaf_2021`,
`population_id: DGUID` and `population_column: "Population, 2021"` with an
explicit local CSV or ZIP path. The streaming adapter validates 2021 DB/DA IDs,
rejects duplicate blocks and corrupt counts, and aggregates complete DB counts
to DA. A DA with any `DBIR2021_IDRI2021=T` block or missing block count remains
unknown, including when another block or an incomplete block contains zero.

The 2026-10-17 user-supplied `2021_92-151_X.csv` was verified and retained under
the checkout's gitignored `data/raw/statcan/` cache. All 6,924 available selected
BC counts agree exactly with the existing Profile inputs. All 23 missing selected
areas have incompletely enumerated blocks with blank counts in the GAF, so this
source supplies no replacement counts. No allocation or coverage result changes.
The receipt records observation/copy time and SHA-256, without asserting a provider
or download timestamp. The user's Downloads copy remains untouched.

[StatCan GAF reference guide](https://www150.statcan.gc.ca/n1/pub/92-151-g/92-151-g2021001-eng.htm)
documents the absence of counts for incompletely enumerated reserves/settlements.


## Distinct national counts at cartographic boundary overlaps

An explicit acquisition specification may select
`cross_border_overlap: retain_distinct_national_counts`. US 2020 block and Canada
2021 DA records enumerate distinct national populations. Local overlap between
their provider geometries therefore does not establish duplicate census counts.
This policy retains each record once, its published count, and its full original
polygon denominator, while recording every cross-provider overlap. It does not
clip polygons to an invented international seam or claim positional agreement.
Same-country overlapping support and duplicate source IDs remain errors.
The older reject/quarantine policies remain available; quarantine deliberately
excludes whole affected sources and should not be mistaken for missing raw counts.
The allocation method version is `area_weighted_original_polygons.v1.3`.

Shared-study delivery now supplies separately named collected estimates for each
cell, density, and cumulative 25/50/100 km support. These reuse the exact
available-source subtotal and apply the explicitly permitted missing-contribution
as-zero assumption. Every estimate retains coverage and zero-imputation flags.
Density uses full H3 cell area, including water. Shared-study public metrics are
collected-only for count, density and 25/50/100 km distances; duplicate strict scalar
fields are excluded from the public table and field dictionary. Machine identifiers
retain `_collected`, with display labels Population Count, Population Density (people/km²),
and Population Within 25km/50km/100km. Measurements and coverage indicators use the
Population family; metadata/help states the collected-source assumptions. Their source-subtotal,
coverage, missing-source and geographic-support qualifiers remain as evidence. The
standalone engine retains its strict calculations for validation and other direct queries.
