# Final-only Human delivery

`human.get_human_data()` publishes exactly two Parquets under `human.delivery.final_only.v7` and packaged `final-products.v7.json`. Original sources and immutable receipts remain outside delivery. The user-owned `REVIEW_RESULTS.txt` is retained and permitted by validation.

| File | Row identity | Reviewed dimensions |
| --- | --- | --- |
| `static.parquet` | Native R7 H3 cell | 13,461 rows × 14 columns |
| `dynamic-calendar.parquet` | Local civil date × country × reference timezone | 13,148 rows × 11 columns, 2009–2026 |

## Compact static

`human.study.static.v6` has exactly these public columns, in order:

| Column | Arrow type | Meaning / units |
| --- | --- | --- |
| `h3_index` | large_string | Native H3 cell identifier; primary key |
| `h3_resolution` | int64 | Native resolution, fixed at 7 |
| `population_count` | double | Area-allocated available-source resident population in the full cell; persons |
| `population_within_25km` | double | Cumulative population within 25 km of cell center; persons |
| `population_within_50km` | double | Cumulative population within 50 km of cell center; persons |
| `population_within_100km` | double | Cumulative population within 100 km of cell center; persons; default |
| `population_density_per_km2` | double | Population count divided by full cell area including water; persons/km² |
| `boat_launch_count` | int64 | Total mapped launch source records in the cell; nulls encoded zero |
| `shore_access_site_count` | int64 | Total mapped shore-access site records in the cell; nulls encoded zero |
| `place_record_count` | int64 | Named-destination catalog records in the cell; nulls encoded zero |
| `has_port` | int64 | `int64(fill_null(original_port_record_count, 0) > 0)` |
| `has_boat_launch` | int64 | `int64(boat_launch_count > 0)` |
| `has_shore_access_site` | int64 | `int64(shore_access_site_count > 0)` |
| `has_place` | int64 | `int64(place_record_count > 0)` |

The 2026-10-25 user review adds presence counterparts for all three retained counts and changes `has_port` from Arrow Boolean to int64. All four fields contain only **0 or 1**, with no nulls and units `0/1`. One means the corresponding mapped record count is positive; zero includes both no counted record and support previously filled from unknown. Counts and population values remain unchanged. The three count fields remain alongside their indicators; neither representation replaces the other.

The 2026-10-24 user review removes `_collected` from the five population column names without changing their values, precision, method or missing-contributions-as-zero assumption. These remain fractional US 2020 / Canada 2021 census allocations, not fully observed/current resident totals or attendance. Density still uses full-cell area; cumulative neighborhoods overlap and must not be summed. Coverage, geographic support, area, method/imputation arrays and source IDs remain in the external audit.

Boat launches consolidate to the preceding all-launch total. Shore access consolidates to the preceding all-site total, including public, unknown, conditional and restricted records. Category subtotals are not added to that total, so no record is double-counted by consolidation. These are mapped record counts; source reconciliation does not establish unique physical facilities or current access/use. Public/access-category subcounts and shore-access evidence state are removed from public static. `mapped_marine_shoreline_length_m` is removed from Human; shoreline quantities belong with Seascape, without substituting an unverified cross-toolkit shoreline definition here.

The user explicitly requests unknown-as-zero encoding for all retained infrastructure counts. Safe int64 casts reject fractional, negative, nonfinite and out-of-range counts instead of silently truncating. The three count columns have no nulls; the four presence indicators likewise encode unknown/no-record support as 0. This changes public representation, not the underlying coverage claim: zero can include unknown source support and never proves physical absence. Metadata declares the policy, source count fields and transformations; original nullable arrays are mandatory audit evidence. A port cell may contain multiple source records; Binary presence deliberately loses that multiplicity only in public static.

`place_record_count` is retained pending a modeling decision. It mixes marinas, ferry terminals, beaches, parks, viewpoints, lighthouses and piers. It describes named-destination catalog density, not visits, public access, capacity or observed effort; category selection/source coverage can drive this total and overlap access inventories. The historical native group documents all 750 retained records and categories, while the fixed H3 summary has its own narrower support. Do not treat those inventory-wide category totals as a category breakdown of current cell counts. A future split/removal requires a separate decision.

All products remain research-only/model-ineligible. No new source collection, census allocation or cross-resolution adapter is implied.

## External audit and reconstruction

**`static-audit.parquet` beside the immutable external `catalog.json`** retains every original source array that is removed or transformed. For the original 67-column source, 60 fields plus the H3 join key are in audit; the other seven invertible identity/population arrays remain in public static with aliases. Crucially, all four infrastructure source-count arrays are audited, including their original types/null masks and port multiplicities. A 64-column source without ports retains 57 fields plus H3, while the public port flag is 0. No support/missingness is assumed constant or reconstructed from aggregates.

The audit header retains the original complete Arrow schema/order and scientific dictionary. Its embedded binding declares absolute path, SHA256, row count and ordered columns. It must remain outside delivery, beside its receipt. `read_catalog` checks checksum and H3 membership; `restore_static(table, info)` aligns by H3 and returns the original full table/dictionary. Re-finalization supports earlier full-width and compact static v2/v3/v4/v5 inputs, including exact reconstruction of the preceding Boolean-port schema. Historical contracts remain packaged.

`validate_delivery` reconstructs original scientific arrays and validates population/area arithmetic, coverage and exact port source multiplicities. It also recomputes the reviewed public projection from reconstructed evidence and checks every public array/type/null, preventing tampered public counts/flags from hiding behind valid audit arrays. A missing, corrupted or mismatched audit fails. Portable copies need an explicit relocation/rebinding workflow.

Population definitions use `support_location: external_static_audit` and per-metric `audit_fields`; density references the original cell-area denominator. Infrastructure definitions declare `null_fill_policy: unknown_as_zero_user_review_20261024`, fill values, source count field and derivation. Original scientific missingness remains inspectable, although public counts/flags are dense.

## Column names

The additional indicators are deterministic derived columns, recorded by `derived_from` and `derivation` in the contract/dictionary; they do not add source records or alter the external audit partition.

Population aliases remove only `_collected`. Infrastructure aliases consolidate the original all-launch/all-site totals, retain place count, and convert port count to presence. Embedded/external metadata binds original source names to current names; derived indicators retain public count references:

| Original source name | Public name |
| --- | --- |
| `h3_index` | `h3_index` |
| `h3_resolution` | `h3_resolution` |
| `population_count_collected` | `population_count` |
| `population_within_25km_collected` | `population_within_25km` |
| `population_within_50km_collected` | `population_within_50km` |
| `population_within_100km_collected` | `population_within_100km` |
| `population_density_per_km2_collected` | `population_density_per_km2` |
| `boat_launch_access__BOAT_LAUNCH_COUNT` | `boat_launch_count` |
| `public_shore_access__PUBLIC_ACCESS_SITE_COUNT` | `shore_access_site_count` |
| `places__CATALOG_RECORD_COUNT` | `place_record_count` |
| `ports__SOURCE_RECORD_COUNT` | `has_port` |

Data Explorer exports exactly thirteen value columns plus native H3 identity, with twelve selectable features. It reads integer counts and four integer presence indicators exactly and excludes removed shoreline/access-category fields. Population labels/units, classification, method/assumption and inspection remain attached to renamed fields through producer metadata. Retained population/total-count feature IDs stay stable; port presence has distinct ID `HUMAN__has_port` because it is a changed quantity. Historical unsuffixed strict population fields stay retired; only the current producer schemas and allocation evidence permit unsuffixed estimate names. Current Coverage remains absent. The reader verifies/watches external audit evidence without exporting its removed arrays.

## Compact calendar

`human.study.calendar_lookup.v2` has columns `date`, `country`, `timezone`, `is_weekend`, `holiday_names`, `is_holiday`, `iso_year`, `iso_week`, `day_of_year`, `year`, `month`. Date is ISO local civil date; integer calendar fields use int64 and flags use boolean. The primary key is `(date, country, timezone)`. US uses America/Los_Angeles and unions federal, WA and OR calendars. CA uses America/Vancouver and unions federal and BC calendars. Names are sorted, deduplicated and joined with `; `; coincident actual and shifted observed holidays both survive. Empty string means no holiday. `is_holiday` means any configured jurisdiction lists a holiday, not that everyone in that country observes it.

ISO week (1–53) is paired with ISO week-numbering year; civil year/month and day of year (1–366) describe the date itself. The embedded metadata retains `rules_version`, `timezone_rules_version`, `temporal_support.as_of`, configured jurisdictions, inclusive/exclusive date support and limitations. Dates after as-of remain rule-based future context. UTC day-boundary columns and repeated rule pins are removed. A local day can still last 23, 24 or 25 hours. Convert event timestamps to the applicable local civil date before joining; these reference zones do not map all subregions, notably parts of Oregon. No activity or attendance is inferred.

## Metadata and publication

Each file embeds scientific metadata under `human_study`, plus `human_delivery` with an external immutable checksum receipt path. Readers accept historical final-only v1–v6 receipts and current v7 receipts; the supported validator requires the two-file v7 product set and its external static audit. Static native-file bindings are removed; necessary original source receipts and infrastructure provenance survive in metadata. Receipts stay outside Data/Human. Normal runs lock, stage on the output filesystem, validate, publish by directory rename and roll back on final readback failure. Review notes survive normal replacement.

## Review migration

For an existing verified two-file or historical three-file delivery, migrate without acquisition or population recalculation:

```python
from human.pipelines.delivery.migration import migrate_reviewed_delivery

migrate_reviewed_delivery('/path/to/Data/Human',
                         metadata_dir='/path/to/toolkit-human/data/delivery/new-review-receipt')
```

This explicit operation checks existing receipt hashes, locks publication, checks staging space, projects static to 14 columns, writes its external audit, and applies the reviewed encodings and verifies exact reconstruction of every original column/value/type/null. An already compact calendar retains all values/types; an older jurisdiction calendar is condensed as documented above. It validates the staged and final products against pinned rules and retains the review note, prior external receipts and originals. A failed final readback restores the old directory. After success only its temporary stage and superseded publication backup are removed; `migration-validation.json` records before/after hashes, audit binding, code/environment identity and preservation checks outside delivery. A failed run can retain its bounded immutable receipt evidence outside Data; never reuse that directory for another run. A new immutable receipt directory is required.

`finalize_delivery` is the lower-level empty-destination repackager. It excludes historical native geometry products and compacts v1 jurisdiction calendars; historical research activity exports retain their own native support. The removed `HumanData.native`, native geometry export helpers and `--native-source-specification` delivery argument have no active replacement. The source collectors needed for static counts remain. See [the run guide](get-human-data.md) and [port counts](ports.md).
