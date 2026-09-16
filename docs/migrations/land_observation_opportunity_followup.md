# Access-conditioned land opportunity follow-up

This handoff supersedes the support and validation statements in the initial v4 handoff. The product remains a research observation-opportunity scenario, with no ecological model integration, new providers, water-effort changes, or full-region regeneration.

## Corrected contracts

- **Geometric evaluation:** the kernel builder creates a sample/source/scenario plan before LOS execution. Publication requires every planned sample to complete both surfaces. The certificate binds the sparse kernel and canonical water support by content digests. Missing, truncated, nonfinite, or uncertified computations fail closed. A completed out-of-radius pair can be absent from the sparse table; it is a proven zero under the persisted inclusive metric-radius rule. Full canonical child-water area remains in the R6 denominator, including zero children.
- **Required weather:** only positive canopy-kernel bins with nonzero or unresolved activity require conditions for the primary stream. Physical diagnostics use positive physical-kernel bins. Known invisible/out-of-radius sources do not change local condition diagnostics or required-weather coverage. Unknown activity remains unresolved. Available contributions are summed without division by coverage.
- **Known zero:** a completely evaluated target with no potentially contributing support retains raw zero even without weather. Its required-weather coverage is 1 (no unmet requirements); condition values remain null if no local contributing support exists. Missing all relevant weather gives null raw and zero coverage; missing some gives an uninflated partial sum. Access inventory completeness remains unknown.
- **Observer ground:** endpoint nodata repair now samples the authoritative DEM at fixed coarse-grid centers, with the established three-valid-neighbor median fallback for isolated voids. Repair is tiled, independent of sampled observers, and performed before canonical canopy construction. Unresolved ground fails observer validation; other unresolved terrain follows the configured fail/opaque policy. A's authoritative subpixel elevation is not claimed: this implementation explicitly chooses a canonical coarse-grid repair. The observer-neutral canopy and private observer grounding are retained.

## Scaling and resume behavior

The canonical water inventory is constructed once per kernel call using raster block reads. A retained `cKDTree` indexes pixel centers. Each sample's distance, LOS lookup, and bin aggregation use only its radius query. Samples are accumulated immediately into a source partition; full-domain sample DataFrames are no longer retained.

Source Parquet partitions have checksummed completion records. The cache identity includes the independent sample plan and coordinates, requested/effective configuration, raster identities, bin width, runtime versions, and relevant viewshed implementation hashes. A completed matching partition is reused; incomplete or mismatched partitions are rebuilt. Generation publication remains immutable. Inspection computes means, states, and coverage with streaming Polars aggregations and retains only a 100-row selected-column preview.

The pixel index still requires memory proportional to the regional water inventory. Final sparse kernel assembly and the daily basis require memory proportional to the resulting sparse table. This is radius-bounded observer work and source-partition streaming, not a claim of constant-memory regional processing. Full-region capacity has not been measured.

## Input boundaries and provenance

Global `PARENT_SITE_ID` is retained across declared source-cell fragments. Each fragment has a distinct `SITE_ID` and `PARENT_FRAGMENT_SHARE`. Shares use deduplicated represented area, length, or point count, normalized across the global parent. For each scenario, a fragment's site weight is its global share divided by the number of eligible parents in its cell. Within-fragment sample weights sum to one. This prevents fragmentation from multiplying a parent budget; source allocations cannot exceed one. Residual source allocation is explicit in `*_SOURCE_UNALLOCATED_WEIGHT` and is not redistributed. Single-cell parent behavior is unchanged.

The configured regional facilities file was inspected read-only: 4,095 Point rows, no `REPRESENTED_GEOMETRY_WKB`, and 22 provider groups spanning multiple H3 source cells. The fragment builder produced 4,052 fragments across 4,025 global parents; all global fragment shares summed to one, and maximum parent/source allocation was one. This is **point-only support**. Existing authoritative shoreline-line products are not connected to the land-site sampler. The synthetic smoke includes an explicitly supplied line to exercise that supported geometry path.

The regional facilities file matches the source manifest's file-content SHA-256. This read-only inventory audit did not modify or regenerate regional artifacts.

The generation now records requested and effective viewshed settings separately, actual LOS execution modes, Python and CLI GDAL versions, kernel profiling/certification, geometry-type counts, and hashes for 82 viewshed Python files alongside land implementation identities.

## Schema and migration

Land schema remains `4.0.0-research`, with the shared component contract unchanged. The kernel algorithm is now `site_weighted_radius_partitions_v2`; site geometry uses `represented_access_site_fragments_v2`; canonical raster stack uses v2 and endpoint repair uses `canonical_grid_center_repair_v3`. Rebuild old kernels and endpoint/canopy caches through the existing entry points. Old uncertified kernels are rejected. Do not reinterpret missing rows from earlier generations as sparse structural zeros.

The evaluation certificate is persisted in the target-support Parquet's pandas metadata and copied into the generation manifest as `kernel_evaluation_plan`. Readers that strip that metadata must restore the matching manifest certificate before calling the conditioned-basis builder. A certificate cannot be inferred from surviving kernel rows.

Daily/weekly canonical opportunity names are unchanged. Corrected weather states can differ from initial v4 fixtures. The default ecological loader block against silently consuming v4 features is preserved and regression-tested.

## Reproduction and verification

Run from the repository root:

```bash
export ORCA_PY=python
export MPLCONFIGDIR=/tmp/orca-mpl
export XDG_CACHE_HOME=/tmp/orca-cache
"$ORCA_PY" -m human.activity_and_effort.land_reporting_opportunity.smoke --output-dir outputs/validation/land_observation_opportunity_followup_final
"$ORCA_PY" -m pytest tests/domains/human tests/viewshed -q
git diff --check
```

The smoke writes exact `land.yaml` and `viewshed.yaml`, source fixtures, sampling comparisons, and an immutable generation selected by `output/manifest_path.json`. That generation contains sites, samples, sparse kernels, water support, source/target components, daily/weekly products, metadata, HTML inspection, and a map. `followup_runtime_profile.json` alongside the configs extracts the recorded profile and output states.

The full human/viewshed suite passed **327 tests**, with no skips and one existing joblib physical-core-detection warning. Actual in-process GDAL exercised A-only, A+B, reversed order, and resumed ground repair with source elevations 20 m and 80 m inside one coarse nodata pixel (canonical center 42 m); A's fixed visibility grid matched exactly. Existing canopy invariance and Python/CLI agreement tests also passed.

The radius-index implementation agrees with a dense all-pixel radius reference at `rtol=1e-6, atol=1e-8`. Adding remote water increased total domain pixels without changing the fixed local kernel or its processed-pixel count. Completed partition resume produced identical kernels with zero new observer pixels processed. Tests cover irrelevant source weather, known zeros without weather, genuinely missing contributing weather, unknown activity, rejected missing geometry, retained zero-child water area, and cross-cell parent conservation.

See the profile JSON for the final generation's measurements. Kernel timing starts after canonical raster-stack preparation; peak RSS is process-wide high-water memory, including earlier smoke stages, not isolated incremental kernel memory. Partition pixel counts count each sample's selected water pixels once; each sample evaluates two LOS surfaces. These small-fixture measurements are not a regional runtime forecast.
