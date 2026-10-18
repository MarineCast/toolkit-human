# Final-only Human delivery

The supported entry point is now `from human import get_human_data; get_human_data()`. It collects configured sources, builds exactly these three products, validates them, publishes with rollback and cleans its processing directory. See [configuration and lifecycle](get-human-data.md). The lower-level commands below remain implementation/legacy reference.

The current public `Data/Human` directory contains only:

| File | Row identity | Current size / rows |
| --- | --- | --- |
| `static.parquet` | One native R7 H3 cell | 13,461 rows, 64 columns |
| `dynamic-calendar.parquet` | Local civil date × jurisdiction | 32,870 rows, 18 columns |
| `static-native.parquet` | Native feature ID; mixed points, lines and optional source areas | 6,752 features: 6,692 records + 60 context footprints |

These are three distinct grains. Calendar dates retain their jurisdiction, reference timezone and original aware UTC day boundaries, including DST; they are not H3 rows or UTC daily labels. Native geometry is never replaced by H3 centers. Static census values remain US 2020 / Canada 2021 allocations with their original unknown-source, support and zero-imputation qualifiers. Packaging changes no scientific values, source attributes, geometry, units or row order.

Native geometry uses GeoParquet 1.1, EPSG:4326 and WKB in `geometry`. `geometry_json` and `properties_json` preserve the exact source geometry and heterogeneous properties, including nulls; common record/group/category/location fields are also normal columns. `feature_id` is unique; `record_id` can link an optional source footprint to its primary record. There are 367 launches, 4,095 shore sites, 750 destinations and 1,480 classification lines. Names/categories do not guarantee current access, activity or completeness. Original source access attributes remain evidence; the explorer excludes the retired derived `access_state` property and group summaries under its existing adapter policy.

Every Parquet embeds its full scientific dictionary and provenance under Arrow schema key `human_study`. The `geo` key describes native geometry. `human_delivery` identifies the final-only contract and external checksum receipt path. The external toolkit-managed `data/delivery/current/catalog.json` binds the three files and stores per-product companions; no manifest, README, cache, staging directory or old release is placed in `Data/Human`. The app verifies this receipt, embedded metadata, row identity, and every file checksum before export. Human calendar context is retained as a final product but is not currently a map layer.

`human.pipelines.population.delivery.export_static_study`, `human.pipelines.native.features.extend_static_delivery` and `human.legacy.study.export_study` now stage outside the final directory and publish only final Parquets. The historical study API retains its native calendar/activity meanings; a supplied native ferry source would require its own `dynamic-ferry-rN.parquet`, never a join to jurisdiction/date rows. The current approved catalog contains no ferry activity or land-opportunity product. Older, distinct ferry snapshots and original source evidence remain in gitignored toolkit `data/retained`, not in this current delivery. Land opportunity is legacy modeling research, outside active Human scope.

## Lower-level reproduction reference

From the toolkit checkout, use a processing workspace outside the final directory:

```sh
python -m human.pipelines.population.delivery \
  --study-config ../../config/study.v1.json \
  --workspace data/static-work \
  --source-manifest /absolute/path/to/the/verified/sources.json \
  --retained-static ../../Data/Human/static.parquet \
  --native-source-specification /absolute/path/to/the/verified/native-source-specification.json \
  --output-dir /absolute/path/to/empty/staged-final \
  --metadata-dir /absolute/path/to/toolkit-managed/receipts
```

Validate staged products before promoting them. `python -m human.pipelines.delivery.finalize --source <legacy-or-final-directory> --output <empty-final-directory> --metadata-dir <external-toolkit-directory>` repackages without acquisition or scientific recomputation. It verifies checksums, all Arrow values/nulls/types and full native geometry/property round trips. `read_catalog`, `embedded` and `native_payload` in this module are the producer's public Python readers. Receipts must move with a portable delivery, with their path explicitly rebound and hashes regenerated; embedded scientific metadata remains inspectable in each individual file.

Local storage after the 2026-10-17 cleanup: `data/static-work` contains the current normalized source/calculation cache; `data/provenance` contains necessary original source and review evidence; `data/retained/study-2009-forward` and `data/retained/ferry-current-grid` retain distinct older research inputs. Earlier logs retain their historical paths. `data/provenance/final-layout-20261017` records exact relocation and recovery paths. The byte-identical duplicate land-opportunity copy and superseded processing caches were moved recoverably to the task's named Trash folder. This task did not permanently delete unique source data or empty Trash. At final readback the named recovery folder had disappeared: 24 obsolete/duplicate cached or replaced-encoding files were unavailable there. All current products and necessary original source evidence remained hash-verified; the ledger records each missing recovery path and its checksum. The cause of the recovery-folder disappearance is unknown.

Checks: `PYTHONTZPATH='' python -m pytest -q tests/test_static_delivery.py tests/test_native_features.py tests/test_study.py tests/test_calendar_lookup.py`. Existing fixture assertions cover native geometry/attributes, nulls, original static/calendar values, DST, clean directory membership, repeat native extension and tamper rejection. The adapter regression is `python -m unittest discover -s scripts -p test_human_input.py` from the explorer checkout.
