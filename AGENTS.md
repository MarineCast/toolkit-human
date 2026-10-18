# toolkit-human agent guidance

## Supported product and layout

`human.get_human_data()` and `human get-human-data` own exactly `static.parquet`, `dynamic-calendar.parquet` and `static-native.parquet`. Read [the run guide](docs/get-human-data.md), [architecture](docs/ARCHITECTURE.md), [source placement](docs/census-sources.md), [contracts](docs/CONTRACTS.md) and [workflows](docs/WORKFLOWS.md) before changes or execution.

The root contains real orchestration in `main.py`, CLI parsing in `cli.py`, package exports and `__main__.py`. Put supported implementations under `pipelines/population`, `calendar`, `native` or `delivery`; native source builders have their own boat_launches/shore_access/places folders. Pipeline-specific functions stay with their pipeline. Shared helpers belong in `utils`; typed config/geometry/artifact infrastructure remains in `core`. Package configs, exact schemas and the feature catalog under `resources`. Avoid forwarding-only modules and do not restore the removed alias tree; intentional changes are documented in [import migration](docs/import-migration.md).

Real historical research is grouped under `human.legacy`, outside the supported pipeline. Do not expose extra ferry/routing/viewshed/modeling outputs through the active CLI/catalog. AIS is owned by toolkit-ais. No 250 km population metric or distance-to-features feature is currently approved. Keep unimplemented plans in documentation rather than empty package stubs.

## Shared context and scientific contracts

Before changing boundaries, dependencies, shared schema/provenance or consumers, read `../../.github/INFRASTRUCTURE.md` from this checkout, or the [organization guide](https://github.com/MarineCast/.github/blob/HEAD/INFRASTRUCTURE.md) in an independent clone. Do not import sibling applications. Preserve unrelated dirty files and inspect branch/status before work.

Keep source dates, units, spatial support, source rights and missingness explicit. Activity, mapped access, reporting and ecological response are distinct. Preserve dataset/schema/method identifiers when moving code; Python paths are not scientific identities. Calendar is jurisdiction/local civil date with timezone boundaries, not H3 UTC-day observations. Population retains its collected-source qualifications and census vintages; the three misleading raw shoreline summaries remain withheld, not scientifically corrected.

## Source and storage boundaries

Canadian originals live in ignored `data/raw/statcan/2021/`, as pinned in both census recipe copies. If only extracted downloads remain, register them with `human.pipelines.population.local_sources`; keep their private local recipe/member receipts distinct from original ZIP identity. Never claim repacking recovers an upstream hash. Reuse exact local files before requests; mismatches fail without replacement. Verify provider ZIP bytes/CRC and vintage before moving originals. Never repack extracted folders to claim an original hash, commit raw bytes, duplicate bulk inputs, or sweep pre-existing caches. Source receipts remain outside final output. Clean only owned processing/staging/backups; preserve approved data until separately authorized publication.

Private `.env` stays ignored. Read only START_DATE, END_DATE, DATA_DIR and optional VERBOSITY, with argument > process > file precedence. Preserve existing values/secrets and do not echo unknown keys. Dates affect inclusive civil calendar rows only. There is no append/REBUILD setting. `human preflight` must not make provider/study writes.

## Validation

Use synthetic/offline fixtures and existing environments. Run supported checks, Pyflakes and `scripts/check_pipeline_docs.py` as documented in WORKFLOWS; keep config and resource copies synchronized. Validate a regular wheel outside the checkout without inherited PYTHONPATH. The full research suite needs GDAL and OSRM preflights from [native workflows](docs/legacy-workflows.md#native-validation-environments), PYTHONTZPATH='', and an isolated HUMAN_WORKSPACE with shipped configs. Its autouse fixture creates small synthetic water/land inputs; never use a live source deployment for tests. Native OSRM fixtures need a temporary loopback listener. Use a task-owned NUMBA_CACHE_DIR when necessary.

Report tests actually run, raw-acquisition versus retained-source qualification, untested regional/application paths and preservation hashes. Do not promote unit tests or historical runs into end-to-end proof. Read and update affected canonical docs/Knowledge notes proportionally, preserving other owners' edits. Graphify is optional; use scoped rg for known paths and do not build a graph for this cleanup alone.
