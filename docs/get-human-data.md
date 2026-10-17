# Human data pipeline

`from human import get_human_data` is the supported entry point. It returns a `HumanData` object with `directory`, `static`, `calendar`, `native`, `receipt`, and `validation`. `config=None` selects `config/human.yaml`; `workspace=None` selects `HUMAN_WORKSPACE` or cwd. Optional arguments are `output_dir`, `mode='live'`, `workers=1`, `overwrite=True`, `start_date`, `end_date`, `env_file`, and `verbosity`.

The pipeline publishes exactly `static.parquet`, `dynamic-calendar.parquet` and `static-native.parquet`. The approved schema is packaged in `resources/contracts/final-products.v1.json`: 64 static, 18 calendar and 12 native columns. The default recipe pins the approved 13,461 H3 cells and yields 32,870 jurisdiction/date rows. Calendar rows follow the effective inclusive date range × five jurisdictions. Native counts vary with fresh collection; no historical native count is imposed on a new inventory.

## Configuration and collection

Every recipe uses `schema: human.pipeline.v1`. `human init` installs `config/human.yaml`, `config/census-sources.v1.json` and `.env.example`, preserving existing files. Paths are workspace-relative, including paths in a census acquisition specification. `study_config` selects the checksum-verified shared study and R7 membership. `output_dir`, `work_dir`, `source_store` and `metadata_dir` must be disjoint. `calendar` explicitly pins `as_of`, `holidays_version` and `tzdata_version`.

`population` accepts `source_manifest` or `manifest_pointer` for checksum-verified normalized full census polygons. Alternatively, `specification` identifies the tracked, packaged five-source census acquisition specification. Its paths are relative to the workspace, independent of the specification-file directory. It pins WA/OR 2020 TIGER `POP20`, 2024 US state inventory, 2021 Canadian cartographic DA boundaries and BC Census Profile counts. Each live acquisition requires its official HTTPS URL and expected SHA256; shared limits are five downloads and a hard 5,000,000,000-byte cap (not a measured provider total). State inventories use the same downloader. Missing originals may be downloaded; existing originals must match their pin and are never silently replaced. Downloads reject redirects/non-200 responses and remove partial files on failure. Immutable acquisition receipts record URL, bytes, SHA256 and available provider headers. In live mode, normalization uses the complete full-cell/100 km collection footprint. The original inputs are retained at their configured locations; normalized files and calculation caches created by this run are temporary. Existing retained normalization bundles are inputs and are never deleted. Optional `population.cache_workspace` reuses an existing calculation only when the complete source/method/cell/radius/parameter identity and its SHA256 match. It is read-only; a miss computes in the run directory, and a corrupt cache fails. It does not copy the old cache or write new intermediates there.

`native.specification` identifies a `human.native_sources.v1` file with launch/shore facilities, destination catalog, WA classification lines and optional source park polygons, bound to source manifests. Optional `native.sha256` pins the specification. For relocated originals, `native.path_map` explicitly maps old absolute prefixes to workspace-relative retained roots; source checksums still must match. Missing paths are reported as unresolved locations, not as evidence that data was lost.

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

`infrastructure` may pin a retained H3 snapshot with `path` and `sha256`. This preserves the approved historical summary support and its nulls, including cells with native records but no retained H3 measurement. Embedded scientific ancestry is carried forward. Without that section, H3 infrastructure is derived from the collected source build manifests; this can change values/support and represents a new collection, not exact historical replay.

For exact immutable files available over HTTPS, `assets` may contain `path`, `url`, `sha256`, attribution and license. Each missing asset requires live mode and a positive shared `max_download_bytes`; existing files must match and are never implicitly replaced. Download parts are removed on checksum, budget or HTTP failure. Assets must stay outside delivery, processing and receipt roots. No generic asset URL is guessed from a missing historical filename.

A missing manifest/pointer target falls back to acquisition. A present manifest with invalid schema/JSON, missing bound files or mismatching checksums fails; corruption never triggers silent replacement. The Canadian official ZIP links returned HTTP 403 to HEAD and ranged GET headers-only checks on 2026-10-17. Five-source acquisition is covered with synthetic HTTP fixtures; a clean official-source regional rebuild is not certified. Matching Canadian archives or restored official access are still needed for that qualification.

The shipped MarineCast recipe reuses the verified census manifest and collects fresh native sources into `data/sources/2026-10-17/native`. It derives infrastructure summaries from those same source builders. This adopts a new native snapshot; it does not assert reproduction of the unavailable historical native files. Calendar rules and the census baseline remain pinned to the approved numerical definitions.

## Publication and cleanup

A run obtains an exclusive output lock, verifies inputs before expensive computation, uses one temporary processing directory, and stages all three products on the output filesystem. Validation checks exact column order/types, unique identities, valid R7 cells, population arithmetic/coverage, native geometry/properties/context links, explicit jurisdiction dates and packaged timezone boundaries. It preserves source qualifiers and the five collected population display names.

Receipts use a new immutable directory per run. Both recipe and actual package versions/code-content fingerprint are recorded. An existing receipt directory cannot be reused. Embedded metadata retains source methods, hashes, calendar rules and local-day semantics. Generated normalization/build artifact paths are explicitly temporary derivations; their recipes/hashes and original source provenance remain in the final receipt.

After staged validation, directory renames publish the candidate. Existing products are checked again before replacement; a failed final readback restores the prior directory. Writers using this pipeline are locked out during the run. This is staged publication with rollback, not a claim of a multi-directory atomic transaction for arbitrary concurrent readers. `.DS_Store` is ignored by readers; unregistered data files are rejected.

On success or failure, temporary processing/calculation files are removed. After successful readback, the superseded run-owned publication backup is removed. Original source snapshots, provenance, pre-existing caches and unrelated files remain. Cleanup operates only on directories created and owned by this run. The output contains no staging, caches, HTML, CSV, manifests or processing sidecars.

## Local settings and dates

Copy `.env.example` to an ignored `.env` without replacing existing user values. Only `START_DATE`, `END_DATE`, `DATA_DIR` and optional `VERBOSITY` are parsed. Unknown keys, including secrets, are not loaded or echoed. There is no shell evaluation or interpolation. Explicit arguments override process environment, then the selected `.env`. With no `.env`, Python calls may use the recipe's calendar/output defaults; existing older recipes retain study-date defaults. An existing `.env` requires all three date/output values from file/environment/arguments. `VERBOSITY` defaults to False and accepts True/False.

Dates must be YYYY-MM-DD, start on/before end, and are inclusive local civil dates. They affect only the separate calendar product. Static census vintages, H3 membership, geographic support and native source vintages do not change with those dates. The calendar keeps its explicit as-of/rule pins and marks rule-based future dates. A chosen date range may differ from the shared study's default time range; spatial registry inputs remain unchanged.

File-relative DATA_DIR uses the `.env` directory. Explicit/process paths use `workspace` (HUMAN_WORKSPACE/cwd when omitted). An explicit env_file is workspace-relative and must exist. No private file is overwritten by initialization. `human preflight --env-file /path/to/.env` checks known settings, reports all errors without raw values/credentials, and makes no directories, locks, study reads or requests. The pipeline runs the same settings validation before verified study reads or collection. Operational progress goes to stderr and is controlled by `VERBOSITY` / `--verbose` / `--no-verbose`.

```python
from human import get_human_data
result = get_human_data(start_date='2021-01-01', end_date='2021-01-31',
                        output_dir='/path/to/scratch/Human', mode='offline')
```

Human does not append dates or add a REBUILD option. Every invocation stages and validates the complete three-product result for the requested calendar, reusing verified original inputs/calculation caches where configured. The existing `overwrite=False` / `--no-overwrite` guard can require an empty destination. Use a scratch destination for experiments; editing .env alone runs nothing.
