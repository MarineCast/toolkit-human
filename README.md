# toolkit human

`get_human_data()` collects Human inputs, builds and validates the dataset, publishes two final Parquets, and removes the run's intermediate files:

- `static.parquet`: 14 columns of R7 population/infrastructure quantities and identity.
- `dynamic-calendar.parquet`: local civil date × country × reference timezone, with combined holiday names and calendar fields.

```python
from human import get_human_data

result = get_human_data()  # config/human.yaml in HUMAN_WORKSPACE or cwd
print(result.static, result.calendar)
```

Install with `python -m pip install -e '.[test]'`. From another directory, pass `workspace='/path/to/toolkit-human'` or set `HUMAN_WORKSPACE`. The package has no application import dependency.

```sh
human init                            # creates two configs and .env.example; preserves edits
human get-human-data                  # collect missing sources; build both products
human get-human-data --mode offline   # rebuild from retained verified snapshots
human validate /path/to/Data/Human
```

For local defaults, copy `.env.example` to `.env` in the toolkit workspace:

```dotenv
DATA_DIR=../../Data/Human/
START_DATE=2009-01-01
END_DATE=2026-12-31
VERBOSITY=True
```

Explicit API/CLI arguments override process environment, then `.env`, then recipe defaults for file-free Python calls. A present `.env` requires all three date/output settings from the file or overrides. File-relative `DATA_DIR` resolves beside `.env`; explicit/process-relative paths use the Human workspace. Only known settings are read, without changing process environment or loading credentials. Existing private files are never overwritten. Run `human preflight` to check date syntax/order and output-parent access without study reads, source calls or writes.

Dates are inclusive **local civil dates** and select the compact country/timezone calendar output. They do not change the US 2020 / Canada 2021 static census baseline or imply daily observations. Human stages both products for the requested calendar range, validates them and replaces the destination together; it has no weather-style append or `REBUILD` setting. `start_date`, `end_date`, `output_dir`, `env_file` and `verbosity` are available on `get_human_data` and corresponding CLI flags.

The shipped recipe uses the approved MarineCast study and verified census polygons, and collects a fresh native inventory from WA/BC/OSM/Natural Earth. H3 summaries use that same inventory. Existing verified snapshots are reused; a new `source_store` directory selects another native vintage. The tracked `config/census-sources.v1.json` supplies five official census inputs with full SHA256 pins, relative original-source paths and shared download/byte limits. Missing normalized-manifest locations fall back to this recipe; corrupt present bundles fail. The Canadian provider endpoints returned HTTP 403 in the latest check, so original-source acquisition remains externally blocked without accessible matching archives. A retained verified normalized bundle can support calculation without reacquisition; the 2026-10-29 local review found that configured bundle absent. See [the pipeline guide](docs/get-human-data.md). The configured, checksum-pinned NGA port inventory is now Human-owned; [port ownership and reproduction](docs/ports.md) describes retained source points, partial source-record counts and the narrow migration that preserves existing Human values.
Original source snapshots and immutable provenance receipts remain outside the final output directory. Only run-owned normalization files, calculations, temporary configuration, staging and superseded publication backups are cleaned. Existing source stores, caches and user files are never swept. Existing final products are replaced only after a complete staged validation, with rollback if publication readback fails.

Population retains collected count/density and 25/50/100 km totals with missing-source/zero-imputation qualifiers. There is no 250 km field. The three misleading legacy shoreline summary fields remain withheld from selection; raw audit values are preserved. Calendar dates remain local civil dates. Retained source geometry and access classifications remain evidence outside delivery, without guarantees of current public access, operation, activity or visitation.

The supported catalog describes exactly these two products. Previous family CLI routes, ferry, routing, legacy calendar-effort, viewshed and modeling products are retired from the supported interface. Their direct modules and source configuration remain for existing research compatibility; `get_population` also remains a lower-level compatibility API. AIS belongs to toolkit-ais. No distance-to-features addition is included.

See [pipeline configuration and lifecycle](docs/get-human-data.md), [scientific delivery details](docs/static-delivery.md), and [validation commands](docs/WORKFLOWS.md).

Supported code is organized under `human/pipelines/delivery`, `population`, `native` and `calendar`. The population allocation engine is under `pipelines/population/static`. Shared artifact/checksum/download/study helpers live in `human/utils`. Earlier module paths are compatibility imports; they do not duplicate the producers. See [architecture](docs/ARCHITECTURE.md).

Local Canadian archive placement, official URLs, exact hashes and reuse/error behavior are documented in [retained census sources](docs/census-sources.md).

Package organization and deliberate internal import changes are described in [architecture](docs/ARCHITECTURE.md) and [import migration](docs/import-migration.md).

The 2026-10-24 review removes native geometry export processing and `HumanData.native`. Existing three-file deliveries require the explicit [review migration](docs/final-delivery.md#review-migration). The 11-column calendar unions federal/WA/OR or federal/BC holiday names per country/reference zone; it does not imply nationwide observance. Source rule pins and as-of remain in embedded metadata. `REVIEW_RESULTS.txt` is preserved as a user-owned note.

The compact static contract is `human.study.static.v6` / final-only v7. Removed methods, imputation flags, source-subtotals, missing-source IDs and infrastructure diagnostics remain in checksum-bound `static-audit.parquet` beside the external receipt. H3-keyed reconstruction validates every original value/type/null; the audit is never exposed as extra map variables. The three public infrastructure counts are int64 with nulls filled as zero; all four presence indicators use nonnull int64 0/1. Original nullable arrays and all port multiplicities remain in audit evidence. Migrate older deliveries explicitly with the [review migration](docs/final-delivery.md#review-migration).

Presence fields `has_boat_launch`, `has_shore_access_site`, `has_place` and `has_port` are int64 indicators: 1 for a positive corresponding count, otherwise 0. The three count columns remain. See [the 14-column contract](docs/final-delivery.md#compact-static).

## Reproducibility review

The [2026-10-29 review and example maps](docs/reproducibility-review.md) reproduced all 13,148 calendar rows from a fresh installed wheel. Static replay and complete delivery validation remain blocked by missing retained inputs and the mandatory external catalog/audit. Existing Parquets were preserved; examples are explicitly qualified previews of their values.

Canadian population acquisition includes a user-managed download step. Follow the [manual Canadian checklist](docs/census-sources.md#manual-canadian-download-checklist) for official portal selections, required schema/files, placement and offline ingestion. This is a supported prerequisite; the missing historical static audit is a separate recovery issue.
