> Current static schema: `human.study.static.v6`, exactly 14 columns. Cell area and all eight population coverage/geographic-support columns are external audit evidence; the stored density still uses full-cell area.  The 2026-10-24 compact-static migration moves omitted per-cell columns and their original dictionary into a checksum-bound audit Parquet beside the external receipt. The historical full-width details below describe source/audit evidence; see [compact static](final-delivery.md#compact-static) for the public projection.

# Static Human study delivery

Current file layout is defined in [final-only delivery](final-delivery.md): `Data/Human` contains only `static.parquet` and `dynamic-calendar.parquet`. Embedded metadata and toolkit-managed external receipts replace public sidecar files. Earlier filenames below describe the historical input contract.

`python -m human.pipelines.population.delivery` builds a Human-only product from the approved
shared study registry. It never selects targets from a historical rectangle. It
verifies the exact mask, registry manifest and membership hashes, preserves all
positive-area reporting members, and pins the study identity again after building.
The existing Human static R7 contract applies unless the shared configuration
explicitly assigns a Human resolution. No shared configuration is written.

The static table retains `human.study.static.v1`, `h3_index`, the existing population
field dictionary for retained quantities, embedded scientific metadata and external checksum bindings used by the
Data Explorer. An optional retained static product supplies only boat-launch,
public-shore-access and place columns. Newly selected cells have null access values;
mapped records are neither complete infrastructure inventories nor evidence of use.
Legacy marine population transfer, effort, opportunity and viewability fields are
excluded. AIS, ferry and other dynamic activity are outside this delivery.

Population uses original full census polygon areas, including complete source
polygons outside the reporting extent. Source selection covers every full cell and
the largest 100 km neighborhood; it does not clip contributors to the planning
rectangle. Cell counts and cumulative 25/50/100 km counts are floating point
area allocations, not density samples. The delivery excludes the 250 km total,
its coverage/subtotal fields and annular bands; preparation uses the same 100 km
maximum as export. Radii use WGS84 geodesic distance from the
H3 center; full-cell density divides by full H3 polygon area. The existing engine
records projection and geometric approximation tolerances. Overlapping target
neighborhoods must not be summed into regional totals. Cell counts conserve the
allocated fraction of source counts over the target union; a coastal subset is
not expected to sum to the population of whole states/provinces.

The reference is US Census 2020 and Canada Census 2021, with consistent uniform
allocation over each original polygon and explicit different source vintages.
It is one static baseline, never 18 annual population surfaces. US TIGER `POP20`
contains the published disclosure-protected count. Canadian missing/suppressed
Profile counts stay unknown. Same-country source overlap is rejected. The current
explicit distinct-national-count policy retains both US/Canada census records
across recorded cartographic boundary overlaps, using unchanged full polygons
and denominators. An optional legacy quarantine policy withholds whole conflicting
sources instead; no policy duplicates source records. Census-free water within the pinned physical-water context, excluding pinned physical
land, now supplies verified zero-population geographic support. This is an explicit
static cartographic resident-population assumption for gaps only; original census
allocations and missing/quarantined counts are unchanged. The producer verifies both
context checksums against the validated shared registry manifest. Water is never
buffered or extrapolated, and geometry outside the supplied context stays unknown.
Unverified offshore regions stay unknown.
Internal incomplete strict totals stay null. Delivered collected estimates use
available-source subtotals with an explicit missing-contribution-as-zero assumption. Geographic completeness is not a population coverage percentage.

## Calendar migration

Current `dynamic-calendar.parquet` is `human.study.calendar_lookup.v2`, keyed by `(date, country, timezone)`. It unions actual and shifted observed holiday names across configured federal/WA/OR or federal/BC calendars. Local civil date, weekend, holiday names/flag, ISO year/week, day of year, civil year and month are columns; rule pins, jurisdictions and as-of stay in metadata. This is regional context, not nationwide observance, empirical attendance or measured activity. Reference zones do not cover all subregions. See the authoritative [compact calendar and migration](final-delivery.md#compact-calendar).

The old v1 jurisdiction lookup and UTC-boundary columns are historical. Consumers must update their join and field selection explicitly; a UTC-date join remains invalid. Source-backed static values are preserved. The map reader accepts both final-only delivery versions and no longer gets native overlays from current products.

## Reproducible commands

Run from this checkout in the documented native environment:

```bash
python examples/prepare_static_study.py \
  --study-config ../../config/study.v1.json \
  --workspace data/static-work \
  --specification data/static-work/source-specification.json
python -m human.pipelines.population.delivery \
  --study-config ../../config/study.v1.json \
  --workspace data/static-work \
  --source-manifest /absolute/path/from/collection-plan/source_manifest/sources.json \
  --retained-static ../../Data/Human/static.parquet \
  --output-dir /absolute/path/to/empty/staging-directory \
  --metadata-dir /absolute/path/to/toolkit-managed/receipts \
  --allow-partial --workers 2 --as-of 2026-10-16
PYTHONTZPATH='' python -m pytest -q tests/test_static_delivery.py tests/test_calendar_lookup.py tests/test_population_static.py
```

Preparation now prints a radius-aware collection-plan JSON path. Read its
`source_manifest` value for the delivery command above. The plan binds the explicit
maximum-radius union and full cells; see [radius-aware collection](population-collection.md).
Preparation is offline by default and reuses checksum-verified source bundles. Live
preparation still requires explicit official URLs, byte and request budgets. No
credentials or new terms are needed for the retained official inputs. Keep only final Parquets under `Data/Human`; place the current catalog and companions in toolkit-managed `data/delivery/current`. Independently validate staged products and preserve distinct source evidence when promoting. Toolkit `data/provenance` records acquisition specification, commands, measured work, checks and limits.

Official source definitions:
[US 2020 TIGER block population record layout](https://www.census.gov/programs-surveys/geography/technical-documentation/complete-technical-documentation/tiger-geo-line.2020.html),
[Statistics Canada boundary guide](https://www150.statcan.gc.ca/n1/pub/92-160-g/92-160-g2021001-eng.htm),
[Statistics Canada Open Licence](https://www.statcan.gc.ca/en/terms-conditions/open-licence).

Calendar rule references: [US federal holiday scope and observance](https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/) and [python-holidays Canada subdivision documentation](https://holidays.readthedocs.io/en/latest/auto_gen_docs/canada/).

## Collected-source 100 km display estimate

The current app-default field is `population_within_100km`; its source/audit counterpart is `population_within_100km_collected`, labeled
“Population Within 100km”. It reuses the existing 100 km available-source
subtotal and replaces an absent subtotal with zero. Unavailable source/geographic
contributions are explicitly assumed zero for this derived estimate only. The
existing WGS84 H3-center radius, original-polygon allocation and source vintages
are unchanged. No acquisition or full recomputation is required.

Its `__zero_imputed` boolean marks every estimate with incomplete source coverage,
including positive partial sums; the collected distance `__coverage_status` aliases
are excluded from the source product and explorer. Original source completeness
remains in provenance qualifiers, and `__method` is `area_weighted_collected_sources_missing_as_zero.v1`. Thus an
imputed zero is distinguishable from a supported zero. The catalog's
`default_population` object supplies these field names and display text.

Census blanks, source IDs, subtotals and geographic flags remain intact. Public
per-cell quantities are `population_count_collected` and
`population_density_per_km2_collected`; their strict scalar duplicates are removed.
Public distance totals expose only
`population_within_25km_collected`, `population_within_50km_collected` and
`population_within_100km_collected`. The duplicate strict distance scalar columns
and their public field definitions are removed; the standalone engine still computes
strict values internally for validation. Existing source-subtotal, coverage, missing-source
and geographic-support qualifiers remain available for each collected distance. The GAF confirms the 23 selected
missing Canadian counts are genuinely unavailable due to incomplete enumeration.
The new estimate does not claim those contributions were observed. This study
delivery now retains partial base metrics by default to provide the requested
collected estimate; `--allow-partial` remains accepted for CLI compatibility. The
standalone population API's strict coverage behavior is unchanged.


### Independent review corrections (2026-10-17)

The current delivery uses `retain_distinct_national_counts` for the official US and
Canadian sources. Both national census counts remain available despite their
recorded cartographic boundary overlaps; full original polygons and denominators
are retained and every source contributes once. Same-country overlaps and duplicate
IDs still fail. This avoids discarding entire populated areas due to small mapped
seams. Genuine incompletely enumerated counts remain null.

Scientific neighborhood polygons now use a 0.25 m sagitta tolerance, improved from
25 m; WGS84 center distances and 25/50/100 km radii are unchanged. The acquisition
footprint retains its conservative 50 m margin, which supports the refined circles.
This reduces approximation sensitivity near dense urban radius boundaries.

Collected cell, density and 25/50/100 km fields share the documented
missing-contribution-as-zero policy. Only collected count, density and distance
quantities are published; `public_population_delivery` performs the same idempotent
column projection on every subsequent shared-study export. Strict calculations
remain internal. Display names are Population Count, Population Density (people/km²),
and Population Within 25km/50km/100km, all in the Population family. Collected-source
limitations live in metadata/help, source provenance and zero-imputation qualifiers.
The three collected 25/50/100 km coverage-status aliases are intentionally excluded
from the delivered table, field dictionary and app cache; future exports preserve this projection.
The schema projection does not recalculate values or change acquisition, calendar
or unrelated fields.


## Native infrastructure and destinations

Native source collection remains necessary for static launch, shore, destination and port counts. Sources, classifications, geometry and unknowns stay in the retained original store with provenance; they are partial inventories and do not establish present public access, operation, visitation or a unique physical-site count. Native geometry export, park display repair and native extension commands are removed from delivery after the 2026-10-24 review. `static-native.parquet` is no longer produced. Static summaries retain their original spatial support and nulls.

The historical snapshot had 367 launch records, 4,095 shore sites, 750 destinations, 1,480 WA classification lines and 84 NGA port points. Those counts describe that inventory, not current completeness or a newly collected run. Withheld shoreline length/fraction/state summaries remain audit-only. See [current delivery](final-delivery.md) and [port source counts](ports.md).


The public delivery also excludes `population_density_per_km2_collected__coverage_status`.
Density values, full-cell-area denominators, zero-imputation flags and original
source provenance are unchanged. The cell-count coverage field is retained.
