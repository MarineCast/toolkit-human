# Human data pipeline

`from human import get_human_data` is the supported entry point. It returns a `HumanData` object with `directory`, `static`, `calendar`, `receipt`, and `validation`. `config=None` selects `config/human.yaml`; `workspace=None` selects `HUMAN_WORKSPACE` or cwd. Optional arguments are `output_dir`, `mode='live'`, `workers=1`, `overwrite=True`, `start_date`, `end_date`, `env_file`, and `verbosity`.

The pipeline publishes exactly `static.parquet` and `dynamic-calendar.parquet`. The schema is packaged in `resources/contracts/final-products.v7.json`: exactly 14 static columns and 11 calendar columns. The approved static table retains 13,461 R7 cells. The default 2009-01-01–2026-12-31 calendar has 13,148 rows: one per civil date, country and reference timezone. Static methods and all omitted per-cell evidence are preserved in a mandatory external audit receipt, with no extra file under Data/Human. See [the compact static contract](final-delivery.md#compact-static).


Calendar columns are `date`, `country`, `timezone`, `is_weekend`, `holiday_names`, `is_holiday`, `iso_year`, `iso_week`, `day_of_year`, `year`, `month`. Holiday names are the sorted distinct union of actual and shifted observed names across US federal/WA/OR or Canada federal/BC. Empty names mean no holiday. This is broad regional context, not nationwide or employer-specific observance. ISO week uses ISO year, which may differ from civil year. Rule versions, jurisdiction definitions and as-of remain in metadata; local-day UTC boundaries are no longer repeated as columns. Native geometry export is removed; retained originals still supply static infrastructure counts. See [migration and support](final-delivery.md).

## Configuration and collection

Every recipe uses `schema: human.pipeline.v1`. `human init` installs `config/human.yaml`, `config/census-sources.v1.json` and `.env.example`, preserving existing files. Paths are workspace-relative, including paths in a census acquisition specification. `study_config` selects the checksum-verified shared study and R7 membership. `output_dir`, `work_dir`, `source_store` and `metadata_dir` must be disjoint. `calendar` explicitly pins `as_of`, `holidays_version` and `tzdata_version`.

`population` accepts `source_manifest` or `manifest_pointer` for checksum-verified normalized full census polygons. Alternatively, `specification` identifies the tracked, packaged five-source census acquisition specification. Its paths are relative to the workspace, independent of the specification-file directory. It pins WA/OR 2020 TIGER `POP20`, 2024 US state inventory, 2021 Canadian cartographic DA boundaries and BC Census Profile counts. Each live acquisition requires its official HTTPS URL and expected SHA256; shared limits are five downloads and a hard 5,000,000,000-byte cap (not a measured provider total). State inventories use the same downloader. Missing originals may be downloaded; existing originals must match their pin and are never silently replaced. Downloads reject redirects/non-200 responses and remove partial files on failure. Immutable acquisition receipts record URL, bytes, SHA256 and available provider headers. In live mode, normalization uses the complete full-cell/100 km collection footprint. The original inputs are retained at their configured locations; normalized files and calculation caches created by this run are temporary. Existing retained normalization bundles are inputs and are never deleted. Optional `population.cache_workspace` reuses an existing calculation only when the complete source/method/cell/radius/parameter identity and its SHA256 match. It is read-only; a miss computes in the run directory, and a corrupt cache fails. It does not copy the old cache or write new intermediates there.

`native.specification` identifies a `human.native_sources.v1` file with launch/shore facilities, destination catalog, WA classification lines and optional source park polygons, bound to source manifests. Optional `native.sha256` pins the specification. `native.ports` independently pins a retained NGA GeoParquet and explicit source IDs; it is supported with both retained native specifications and source-adapter collection, without downloads or a Governance runtime dependency. See [physical ports](ports.md). For relocated originals, `native.path_map` explicitly maps old absolute prefixes to workspace-relative retained roots; source checksums still must match. Missing paths are reported as unresolved locations, not as evidence that data was lost.

The default collects current source snapshots through the existing WA/BC/OSM/Natural Earth domain adapters:

```yaml
native:
  acquire: true
  # Optional overrides; otherwise the packaged source definitions are used.
  # configs:
  #   launches: config/my-launch-sources.yaml
  #   shore_access: config/my-shore-sources.yaml
  #   places: config/my-places-sources.yaml
```

`mode='live'` (the default) runs the source downloaders for a missing acquisition receipt; offline mode requires verified raw manifests/snapshots already in `source_store/native`. Existing receipts are immutable inputs and are reused, not re-dated. Set a new `source_store` vintage to request a refresh. Normalized facilities, catalogs and H3 summaries are built in the run directory. Original source snapshots, availability/attribution/licensing records and acquisition receipts stay in the source store. Family configs cannot export into an app: the pipeline replaces their raw/output paths and drops app-export configuration. Source failures keep their explicit partial/unavailable semantics.

`infrastructure` may pin a retained H3 snapshot with `path` and `sha256`. This preserves approved historical summary support and original null masks in the external audit, including cells with native records but no retained H3 measurement. Current public counts encode those nulls as zero and presence indicators as 0 by explicit user instruction. Embedded scientific ancestry is carried forward. Without that section, H3 infrastructure is derived from the collected source build manifests; this can change values/support and represents a new collection, not exact historical replay.

For exact immutable files available over HTTPS, `assets` may contain `path`, `url`, `sha256`, attribution and license. Each missing asset requires live mode and a positive shared `max_download_bytes`; existing files must match and are never implicitly replaced. Download parts are removed on checksum, budget or HTTP failure. Assets must stay outside delivery, processing and receipt roots. No generic asset URL is guessed from a missing historical filename.

A missing manifest/pointer target falls back to acquisition. A present manifest with invalid schema/JSON, missing bound files or mismatching checksums fails; corruption never triggers silent replacement. The Canadian official ZIP links returned HTTP 403 to HEAD and ranged GET headers-only checks on 2026-10-17. Five-source acquisition is covered with synthetic HTTP fixtures; a clean official-source regional rebuild is not certified. Matching Canadian archives or restored official access are still needed for that qualification.

The shipped MarineCast recipe reuses the verified census manifest and collects fresh native sources into `data/sources/2026-10-17/native`. It derives infrastructure summaries from those same source builders. This adopts a new native snapshot; it does not assert reproduction of the unavailable historical native files. Calendar rules and the census baseline remain pinned to the approved numerical definitions.

## Publication and cleanup

A run obtains an exclusive output lock, verifies inputs before expensive computation, uses one temporary processing directory, and stages both products on the output filesystem. Validation checks exact column order/types, unique identities, valid R7 cells, population arithmetic/coverage, the complete country/timezone holiday union and civil-date fields against pinned rules. It preserves source qualifiers externally and keeps population display names/units while public column names drop `_collected`.

Receipts use a new immutable directory per run. Both recipe and actual package versions/code-content fingerprint are recorded. `static-audit.parquet` holds H3-keyed removed columns and the original dictionary/schema, bound by checksum in the static metadata and receipt catalog. Audit keys must be unique and match the delivered cell set. Missing/corrupt audits fail reads and validation. Reconstructing the full source table checks the original population and partial-inventory invariants. An existing receipt directory cannot be reused. Embedded metadata retains source methods, hashes, calendar rules and local-day semantics. Generated normalization/build artifact paths are explicitly temporary derivations; their recipes/hashes and original source provenance remain in the final receipt.

After staged validation, directory renames publish the candidate. Existing products are checked again before replacement; a failed final readback restores the prior directory. Writers using this pipeline are locked out during the run. This is staged publication with rollback, not a claim of a multi-directory atomic transaction for arbitrary concurrent readers. `.DS_Store` and the user-owned `REVIEW_RESULTS.txt` are permitted; unregistered data files are rejected. Normal overwrites preserve the review note.

On success or failure, temporary processing/calculation files are removed. After successful readback, the superseded run-owned publication backup is removed. Original source snapshots, provenance, pre-existing caches and unrelated files remain. Cleanup operates only on directories created and owned by this run. The output contains no staging, caches, HTML, CSV, manifests or processing sidecars. External audit receipts are retained evidence, not disposable processing files.

## Local settings and dates

Copy `.env.example` to an ignored `.env` without replacing existing user values. Only `START_DATE`, `END_DATE`, `DATA_DIR` and optional `VERBOSITY` are parsed. Unknown keys, including secrets, are not loaded or echoed. There is no shell evaluation or interpolation. Explicit arguments override process environment, then the selected `.env`. With no `.env`, Python calls may use the recipe's calendar/output defaults; existing older recipes retain study-date defaults. An existing `.env` requires all three date/output values from file/environment/arguments. `VERBOSITY` defaults to False and accepts True/False.

Dates must be YYYY-MM-DD, start on/before end, and are inclusive local civil dates. They affect only the separate calendar product. Static census vintages, H3 membership, geographic support and source vintages do not change with those dates. The calendar keeps its explicit as-of/rule pins in metadata; dates after as-of are rule-based future context. A chosen date range may differ from the shared study's default time range; spatial registry inputs remain unchanged.

File-relative DATA_DIR uses the `.env` directory. Explicit/process paths use `workspace` (HUMAN_WORKSPACE/cwd when omitted). An explicit env_file is workspace-relative and must exist. No private file is overwritten by initialization. `human preflight --env-file /path/to/.env` checks known settings, reports all errors without raw values/credentials, and makes no directories, locks, study reads or requests. The pipeline runs the same settings validation before verified study reads or collection. Operational progress goes to stderr and is controlled by `VERBOSITY` / `--verbose` / `--no-verbose`.

```python
from human import get_human_data
result = get_human_data(start_date='2021-01-01', end_date='2021-01-31',
                        output_dir='/path/to/scratch/Human', mode='offline')
```

Human does not append dates or add a REBUILD option. Every invocation stages and validates the complete two-product result for the requested calendar, reusing verified original inputs/calculation caches where configured. The existing `overwrite=False` / `--no-overwrite` guard can require an empty destination. Use a scratch destination for experiments; editing .env alone runs nothing.

Local Canadian archive placement, official URLs, exact hashes and reuse/error behavior are documented in [retained census sources](census-sources.md).

For user-supplied extracted StatCan downloads, `population.local_specification` selects a separately pinned local source recipe on raw-source fallback. It does not replace the official ZIP pins or a valid retained normalized bundle. See the census source guide for registration, member checks and the remaining original-archive provenance limit.

For a new Canadian source setup, use the [manual download and ingestion checklist](census-sources.md#manual-canadian-download-checklist). User-supplied official extracts are a supported acquisition path; automation begins with verified retained inputs. This does not resolve or reconstruct a missing historical external static audit.
