# Static Human study delivery

Current file layout is defined in [final-only delivery](final-delivery.md): `Data/Human` contains only `static.parquet`, `dynamic-calendar.parquet` and `static-native.parquet`. Embedded metadata and toolkit-managed external receipts replace public sidecar files. Earlier filenames below describe the historical input contract.

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

`dynamic-calendar.parquet` (formerly `calendar-temporal.parquet`) is `human.study.calendar_lookup.v1`, with primary
key `(date, jurisdiction)`. Consumers of the older single-date calendar must select
one jurisdiction or join on both fields. The current map adapter consumes only the
static table, so its interface is preserved without application changes.

Rows cover each configured civil date, with an exclusive end date, for US federal,
Washington, Oregon, Canada federal and British Columbia reference calendars. Regional
rows follow applicable state/provincial rules; they are not the union of all
federal employment holidays. `is_actual_holiday` identifies the rule's calendar
date; `is_observed_holiday` identifies an additional shifted observance returned by
the rules library (including coincident observances). Actual does not mean empirical
attendance was observed. Employer-specific substitute days are not inferred.
Names, rules-library version and as-of date are retained; future entries are labeled
`rule_based_future`. Past entries are also rule-generated, not source event records.

Date labels are local civil dates, not UTC day labels. Reference timezone columns
and aware UTC day boundaries preserve 23/24/25-hour days under the packaged IANA
rules. Their `timezone_rules_version` is retained in rows and the companion,
independent of the host operating system database. Reference zones are America/Los_Angeles and America/Vancouver; they are not
a complete timezone map of each jurisdiction (parts of Oregon use Mountain time).
Convert a timestamp into its applicable local date before the jurisdiction/date
join. There are no calendar-effort, viewing-season or human-presence scores.
The active feature catalog advertises this lookup and excludes calendar-effort weights.
Legacy direct calendar helpers and `human build calendar` retain their existing
output compatibility; use `python -m human.pipelines.population.delivery` for this product.

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

The current app-default field is `population_within_100km_collected`, labeled
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

`python -m human.pipelines.native.features --source-specification <source-specification.json>
--extend-static <current-delivery-directory> --output-dir <empty-staging-directory>`
extends a verified static delivery using retained checksum-bound launch, shore and
place sources. `human.pipelines.population.delivery --native-source-specification` accepts the
same specification for future full exports. Neither operation acquires new inputs.
The source specification and source manifests remain checksum-linked in the native
companion and current catalog. The delivered `static-native.parquet` uses
EPSG:4326 native points, lines and source areas in GeoParquet with namespaced feature identities.
Primary records and optional context footprints have separate counts.

The retained snapshot has 367 launch records, 4,095 shore-site records, 750 named
destinations and 1,480 WA shoreline classifications. It is a partial source
inventory, not necessarily a unique count of physical sites. Native geometry
is not replaced with H3 centers. Original facility/access/source attributes and
unknown values remain attached. Retained destination selection scores are selection
evidence, not activity or visitation. Vintages vary and include legacy BC launch
data circa 2004; a current export timestamp does not make those source records new.

Sixty selected WA park footprints are available as optional context. Marine-facing
representative destination points are not surveyed entrances. Nine invalid source
park polygons are repaired only in the display copy with `make_valid`; original
geometry validity and WKB checksums remain explicit and original source files are
unchanged. Footprints do not establish legal access boundaries.

The WA access-line source includes public-evidence, restricted, conditional,
watercraft-only and unknown classes; source CLASS and CLASS_DESC remain attached.
The legacy all-line accessible-length/fraction summaries included nonpublic classes
and are therefore withheld from explorer selection. Their raw values remain
unchanged for audit. Useful mapped-record counts and total marine shoreline length
remain separate H3 summaries, limited to the shared reporting grid. Missing cells
are unknown, never an inferred absence of facilities. Mapping or a public label does
not guarantee current public access, operation, safe access or observed use.

Validation: `PYTHONTZPATH='' python -m pytest -q tests/test_native_features.py`
checks source checksums, identity, restrictions, representative points, optional
source geometry, deterministic exports and exact population/calendar preservation.


The public delivery also excludes `population_density_per_km2_collected__coverage_status`.
Density values, full-cell-area denominators, zero-imputation flags and original
source provenance are unchanged. The cell-count coverage field is retained.
