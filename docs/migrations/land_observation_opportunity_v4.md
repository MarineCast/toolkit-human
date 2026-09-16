# Access-conditioned land observation opportunity

This implementation produces a relative observation-opportunity scenario for represented public land sites. It does not measure observer-hours, whale presence, detection probability, reporting-catalog coverage, or corrected sightings. No ecological model was fitted or changed, and no Pacific Northwest regeneration was performed.

For the subsequent support, scaling, endpoint repair, and provenance corrections, see [the follow-up handoff](land_observation_opportunity_followup.md). The recorded validation below describes the initial implementation.

## Entry points and reproducible fixture

Run from the repository root with the provisioned Python environment:

```bash
export ORCA_PY=python
export MPLCONFIGDIR=/tmp/orca-land-mpl
"$ORCA_PY" -m human.activity_and_effort.land_reporting_opportunity.smoke --output-dir outputs/validation/land_observation_opportunity_smoke
"$ORCA_PY" -m human.activity_and_effort.land_reporting_opportunity.build --config outputs/validation/land_observation_opportunity_smoke/land.yaml --allow-partial --overwrite
"$ORCA_PY" -m human.activity_and_effort.land_reporting_opportunity.inspect --config outputs/validation/land_observation_opportunity_smoke/land.yaml
```

The smoke command writes an explicit `land.yaml` and `viewshed.yaml`, synthetic source inputs and manifests, then runs the real canonical raster preparation, isolated-observer GDAL LOS, source-budget builder, dynamic sparse builder, weekly aggregation, immutable generation publication, and inspection. It does not mock LOS or preparation. The two public sites are a represented beach line and a pier point; a restricted headland shares the beach's source cell. A CHM obstruction, unequal modeled child-water areas, and seven UTC dates with one weather region absent are included.

The general-land fixture diagnostic includes the restricted headland. Primary samples exclude it. `method_comparison.csv` compares the legacy general-source/equal-child calculation against the new access-conditioned water-area calculation on the same synthetic inputs. The legacy scenario uses corrected condition diagnostics; it is not a replay of an old binary or a claim of improved whale-model accuracy.

`sampling_reference_5.parquet`, `sampling_reference_20.parquet`, `sampling_reference_80.parquet`, and `sampling_convergence.json` report actual GDAL sampling discrepancies against the denser reference. These are convergence diagnostics, not monotonicity guarantees. The immutable generation's `inspection.html` and `access_placement.png` show samples, access classes, raw opportunity, coverage, and missingness.

## Regional prerequisites (commands documented, not run)

Rebuild public-access facilities if the existing generation does not retain original represented geometry. Existing facility points remain usable as points; richer geometry is never fabricated.

```bash
"$ORCA_PY" -m human.accessibility.public_shore_access.build --allow-partial --overwrite
"$ORCA_PY" -m human.viewshed.cli.main build-dual-surface-canopy-weights --config config/modeling/effort/viewshed.yaml --overwrite
"$ORCA_PY" -m human.viewshed.cli.main finalize-viewshed-lookups --config config/modeling/effort/viewshed.yaml --overwrite
"$ORCA_PY" -m human.activity_and_effort.land_reporting_opportunity.download --overwrite
"$ORCA_PY" -m human.activity_and_effort.land_reporting_opportunity.build --allow-partial --overwrite
"$ORCA_PY" -m human.activity_and_effort.land_reporting_opportunity.inspect
```

These require the existing DEM, canopy, water/land geometry, H3 preparation, source-target lookup, transport, population/travel, and daily-condition inputs. They do not download a new provider. The land `download` entry point records checksums of its processed inputs and prerequisites. `--overwrite` alone is not a cache migration: algorithm versions and scientific input content identities invalidate affected canopy surfaces and land partitions, and final static metadata rejects prior canopy assumptions. Old immutable land generations are retained.

## Product and migration contract

- Land formula: `land_reporting_opportunity_v2_access_conditioned`.
- Land product schema: `4.0.0-research`. The shared component vocabulary/lineage contract remains v3, so water products are not redefined.
- The canonical daily fields remain `LAND_OBSERVATION_OPPORTUNITY_RAW` and `VERIFIED_LAND_OBSERVATION_OPPORTUNITY_RAW`. Existing effort-name aliases represent these same daily quantities. Legacy ecological feature loading rejects land schema v4 unless a caller explicitly opts into the changed observation-model semantics. Nothing promotes the product to model eligibility.
- Source-cell activity remains population/travel opportunity multiplied by road proximity. Equal weights divide each cell's budget among distinct eligible parent sites separately for mapped and verified scenarios. Within-site weights sum to one. No additional city or transport multiplier is applied to the primary.
- Parent identity comes from explicit parent IDs or provider-scoped group/record IDs. Duplicate representations are unioned before sampling. Proximity and names do not merge destinations. Redundant collinear subdivision vertices are removed before projection. Cross-provider identity requires explicit shared lineage; unresolved duplicates are an input limitation.
- Public-shore facilities now retain original WGS84 `REPRESENTED_GEOMETRY_WKB` alongside their facility point when available. Older point-only records remain point-only. Invalid or unsupported geometries are flagged; missing strict access support is rejected rather than replaced by arbitrary land samples.
- Lines use a nested dyadic design with length weights; polygons reuse the existing nested metric design with clipped Voronoi area weights; points retain their represented locations. Configurable `samples_per_site`, `max_site_design_points`, and `site_allocation: equal_parent_sites` declare these assumptions.
- The kernel integrates static distance decay once at observer/water-pixel resolution. Fine-distance bins retain atmospheric attenuation before coarse aggregation. Outgoing target kernels are not normalized. Bare-earth and canopy numerators remain separately inspectable.
- Canonical water support is independent of pair rows: every modeled water-mask pixel is assigned by center to H3 R7 and then by logical H3 parent to R6. Modeled projected pixel area is retained separately. The R6 raw value is the water-area average, not an area-integrated value. This support definition is deliberately distinct from directly clipping R6 polygons.
- Successful invisible/out-of-radius computations contribute explicit zeros. Failed LOS aborts publication; absent support is not silently declared zero. Partial evaluated contributions are not divided by coverage. Geometric evaluation, condition availability, and real-world access-mapping completeness are separate; the latter remains null/unknown.
- Daylight, atmosphere, wind, and precipitation diagnostics use their own available-support means and coverage. Deterministic daylight is independent of weather. Calendar uses UTC date labels; weeks start Monday. Daily inputs do not imply hourly conditions.
- Weekly existing raw aggregation and display behavior are retained. Explicit `*_AVAILABLE_DAY_SUM`, `*_AVAILABLE_DAY_MEAN`, available-day counts, `EXPECTED_DAY_COUNT`, and sum states expose the actual temporal quantities. Partial weeks are not multiplied up to seven days. Canonical and compatibility state reducers preserve spatial partialness, including partial zeros. Static diagnostic values are retained once per week.
- Raw values remain separate from fixed-reference display indices. The generation manifest records input hashes, parameters, formulas, algorithms, runtime versions, source lineage, and software content hashes. Generation timestamps/IDs need not repeat for modeling values to reproduce.

Additional generation artifacts are `observation_sites`, `observer_samples`, `access_kernel`, `target_water_support`, `inspection_html`, and `inspection_png`. The source/target R7 component tables retain broad general-support diagnostics; the access kernel is the canonical access-conditioned static basis. The daily broad reachability stream remains an alternate general-support scenario, not evidence of access-confirmed observation.

## Validation

```bash
"$ORCA_PY" -m pytest tests/viewshed tests/domains/human -q --disable-warnings
"$ORCA_PY" -m pytest tests/domains/human/activity_and_effort/test_land_opportunity_smoke.py -q
git diff --check
```

The supplied `orca_review_reproductions.py` informed production-function tests for canopy independence, missing-weather daylight, static access states, and area aggregation. Its water-distance illustration is outside this land implementation, and its calendar-lag safeguard remains unchanged. Additional regressions cover null transport/display aggregation, valid zeros, weekly partialness, supported site placement, duplicate/split budgets, and nested sampling.

Numerical fixtures use `rtol=1e-6, atol=1e-8`; fixed GDAL boolean grids are compared exactly. Changing a raster's resolution, water mask, or grid changes the physical discretization and is not an invariance test. The synthetic smoke uses a fixed 60 m grid; production uses its configured grid. No extra raster-boundary error is hidden in the fixed-input comparisons.

The GDAL tests explicitly skip only if their required GDAL capability is absent. This environment has both Python GDAL bindings and the CLI. Full-region regeneration, observation-model integration, empirical visitation calibration, and assertions about complete real-world access mapping remain outside this handoff.

## Changed implementation areas

- `public_shore_access/build.py`: preserve represented geometry.
- `viewshed/prepare/elevation/{canopy,cache}.py`, `weights/terrain/{los,gdal}.py`, and `finalize/final_artifacts.py`: isolated grounding and scientific cache/metadata identities.
- `land_reporting_opportunity/{sites,support,access_kernel,conditioned_basis}.py`: represented sources, conserved weights, canonical water support, and integration with the existing sparse dynamic builder.
- Existing land `build`, `download`, `config`, `dynamic`, `generations`, `inspect`, and `modeling` entry points: publication, conditions/states, explicit temporal quantities, and migration guard.
- `land_reporting_opportunity/smoke.py` and focused human/viewshed tests: reproducible offline production fixture and regressions.

Unrelated pre-existing working-tree changes were preserved.

## Recorded validation (2026-09-05)

The human/viewshed suite passed: **321 passed**, with no skipped tests. The GDAL-backed production smoke test was also rerun after report-layout changes and passed. `git diff --check` passed. The fixture publishes 77 daily rows and 11 weekly rows from two eligible parent sites and six observer samples; all 77 daily rows retain partial support (49 positive contributions and 28 evaluated zeros). Daylight is 0.5 with coverage 1.0, while atmospheric coverage is 0.5. All 11 weekly canonical and compatibility states remain partial.

The 5- and 20-sample static R6 discrepancies from the 80-sample reference have maximum absolute values approximately 0.00130335 and 0.000246906, respectively. These are fixture-specific discrepancies, not estimated real-world errors. See the generated `sampling_convergence.json` for exact values and `method_comparison.csv` for the same-domain methodology comparison.

The retained handoff generation is `20260905T130521Z_089c0562`, selected by the fixture's `output/manifest_path.json`. Its daily modeling values (keys, mapped/verified raw values, water area, conditions, and primary state) were compared exactly with the prior fixture generation and were identical; generation lineage and timestamps were excluded from that comparison.
