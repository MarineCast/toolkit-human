# toolkit human

`get_human_data()` collects Human inputs, builds and validates the dataset, publishes three final Parquets, and removes the run's intermediate files:

- `static.parquet`: R7 population and infrastructure summaries.
- `dynamic-calendar.parquet`: local civil date × jurisdiction, with explicit UTC day boundaries.
- `static-native.parquet`: native launches, shore sites, places, shoreline classifications and park context in GeoParquet.

```python
from human import get_human_data

result = get_human_data()  # config/human.yaml in HUMAN_WORKSPACE or cwd
print(result.static, result.calendar, result.native)
```

Install with `python -m pip install -e '.[test]'`. From another directory, pass `workspace='/path/to/toolkit-human'` or set `HUMAN_WORKSPACE`. The package has no application import dependency.

```sh
human init                            # creates two configs and .env.example; preserves edits
human get-human-data                  # collect missing sources; build all three
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

Dates are inclusive **local civil dates** and select the jurisdiction calendar output. They do not change the US 2020 / Canada 2021 static census baseline or imply daily observations. Human stages all three products for the requested calendar range, validates them and replaces the destination together; it has no weather-style append or `REBUILD` setting. `start_date`, `end_date`, `output_dir`, `env_file` and `verbosity` are available on `get_human_data` and corresponding CLI flags.

The shipped recipe uses the approved MarineCast study and verified census polygons, and collects a fresh native inventory from WA/BC/OSM/Natural Earth. H3 summaries use that same inventory. Existing verified snapshots are reused; a new `source_store` directory selects another native vintage. The tracked `config/census-sources.v1.json` supplies five official census inputs with full SHA256 pins, relative original-source paths and shared download/byte limits. Missing normalized-manifest locations fall back to this recipe; corrupt present bundles fail. The Canadian provider endpoints returned HTTP 403 in the latest check, so original-source acquisition remains externally blocked without accessible matching archives. The current deployment can still calculate from its verified retained normalized bundle. See [the pipeline guide](docs/get-human-data.md). The configured, checksum-pinned NGA port inventory is now Human-owned; [port ownership and reproduction](docs/ports.md) describes native points, partial source-record counts and the narrow migration that preserves existing Human values.
Original source snapshots and immutable provenance receipts remain outside the final output directory. Only run-owned normalization files, calculations, temporary configuration, staging and superseded publication backups are cleaned. Existing source stores, caches and user files are never swept. Existing final products are replaced only after a complete staged validation, with rollback if publication readback fails.

Population retains collected count/density and 25/50/100 km totals with missing-source/zero-imputation qualifiers. There is no 250 km field. The three misleading legacy shoreline summary fields remain withheld from selection; raw audit values are preserved. Calendar dates remain local jurisdiction dates. Native geometry and access classifications remain source evidence, without guarantees of current public access, operation, activity or visitation.

The supported catalog describes exactly these three products. Previous family CLI routes, ferry, routing, legacy calendar-effort, viewshed and modeling products are retired from the supported interface. Their direct modules and source configuration remain for existing research compatibility; `get_population` also remains a lower-level compatibility API. AIS belongs to toolkit-ais. No distance-to-features addition is included.

See [pipeline configuration and lifecycle](docs/get-human-data.md), [scientific delivery details](docs/static-delivery.md), and [validation commands](docs/WORKFLOWS.md).

Supported code is organized under `human/pipelines/delivery`, `population`, `native` and `calendar`. The population allocation engine is under `pipelines/population/static`. Shared artifact/checksum/download/study helpers live in `human/utils`. Earlier module paths are compatibility imports; they do not duplicate the producers. See [architecture](docs/ARCHITECTURE.md).

Local Canadian archive placement, official URLs, exact hashes and reuse/error behavior are documented in [retained census sources](docs/census-sources.md).

Package organization and deliberate internal import changes are described in [architecture](docs/ARCHITECTURE.md) and [import migration](docs/import-migration.md).
