> Historical review from the originating application. Current ownership and validation
> limits are in [ARCHITECTURE.md](ARCHITECTURE.md) and [MIGRATION.md](MIGRATION.md).

# Viewshed modeling review

Date: 2026-07-13  
Scope: OrcaCast land-source and water-source viewshed/viewability code paths under `src/human/viewshed`, with emphasis on observer-effort correction, app-facing generalized viewable-area products, and ranking places of interest by forecast likelihood or historic sighting density inside their viewable areas.

## Executive summary

The current pipeline has a strong modular factor design: it separates terrain line-of-sight, distance attenuation, vegetation attenuation, and later environmental/daylight layers. That separation is scientifically useful because each factor can be audited independently and reweighted without rebuilding every artifact. The largest current risk is not that the pipeline is obviously wrong; it is that several defaults and aggregation choices can silently change the meaning of the output from “observable area” to “sampled, attenuated support score.” For observer-effort correction and public app products, those are related but not interchangeable.

The highest-value improvements are:

1. **Publish separate app products for binary/generalized observable area and weighted detectability support.** Keep `weight_terrain`, `weight_distance`, `weight_vegetation`, and physical composites, but add explicit product contracts for “can be seen at all,” “clear-sky area fraction,” and “effective visibility score.”
2. **Replace sampled H3 water-area aggregation with either full aggregation for final static products or calibrated fractional H3 coverage.** The default `pixel_stride: 10` is fast but can make small visible slivers and shoreline cells unstable.
3. **Calibrate or at least sensitivity-test the distance model, observer/target heights, and visibility thresholds against known viewpoints / sightings.** The default logistic distance curve and 30 km cutoff are plausible, but they currently act as modeling assumptions rather than validated detectability estimates.
4. **For water-source visibility, distinguish “open-water horizon shortcut” from DEM/coastline-obstructed visibility and platform class.** The shortcut is a valuable optimization, but app users and effort models may need platform-specific products rather than the current best-platform collapse.
5. **Add canary QA maps and numeric invariants to every static build.** Viewshed outputs are highly vulnerable to CRS, raster alignment, H3 resolution, shoreline mask, and config drift.

## Current pipeline: what the code appears to do

### Configuration and defaults

The compact viewshed config sets H3 resolution 7, 30 m raster resolution, 30 km maximum distance, 1.7 m observer eye height, 1.0 m target height, and enables land and water source types plus distance, terrain, vegetation, and water LOS families. The schema normalization expands those compact values into nested runtime sections, including GDAL as the default backend, UTM zone 10 as the projected CRS, curvature coefficient `0.85714`, five sample points per source cell, sampled aggregation, and `pixel_stride: 10`.

### Land-source terrain visibility

For land sources, the terrain stage samples observer points inside each source H3 cell, runs a radius-based GDAL viewshed for each observer, masks visible pixels to water, accumulates visibility counts and distances, maps visible water pixels to target H3 cells, and writes compact pair partitions with `source_h3`, `target_h3`, and `weight_terrain`.

The terrain stage supports two explicit elevation-surface models:

- `bare_earth`: the water-flattened endpoint DTM is passed directly to the viewshed backend.
- `canopy`: the CHM is aligned to the endpoint grid and the intervening land surface becomes `DTM + CHM`. Water pixels remain on the endpoint DEM, and every sampled observer pixel is restored to DTM before GDAL runs. Consequently, GDAL's `-oz` remains eye height above ground rather than eye height above canopy, and `-tz` remains target height above the water surface.

The canopy surface uses no land-cover classes. CHM nodata behavior is explicit through `viewshed.canopy_nodata_policy`: `error` rejects gaps over modeled land, while `zero` treats masked CHM classes as zero canopy and records the affected pixel count/fraction in partition metadata. The Port Angeles notebook uses `zero` because the ETH product masks built-up areas, snow/ice, and permanent water with value 255.

The terrain support score is not simply binary visibility. It is computed as:

```text
terrain_visibility_support = observer_sample_fraction * visible_area_fraction
```

where `observer_sample_fraction` is the fraction of sampled observer points that can see the target H3 and `visible_area_fraction` is the estimated visible water area divided by total water area in that target H3. This is a reasonable support metric, but it should be labeled and published separately from generalized “viewable area.”

### Distance weighting

Distance weights are built from the canonical source-target lookup and filtered to finite, nonnegative distances within the resolved maximum distance. The default model is logistic with a 9 km half-detection distance, 2.7 km slope, and 30 km hard cutoff. This is probably the dominant app-facing attenuation after terrain/vegetation, so it should be treated as a calibrated parameter, not just a convenience default.

### Land-cover weighting

Land-cover accessibility or attenuation is a separate, optional factor pipeline. It is not used to build the canopy LOS raster. In the Port Angeles notebook it is deliberately disabled and represented by a neutral `weight_vegetation = 1.0`; this prevents CHM from being counted once as a hard LOS obstacle and again through the older combined land-cover/CHM attenuation path.

If land-cover weighting is reintroduced, it should consume its own config and should model source access or residual transmission only. It must not reapply the same CHM obstruction already represented by `DTM + CHM`.

### Final physical view score

Land finalization joins terrain, vegetation, and distance weights on pair keys and multiplies them into `land_pair_physical_view_score`, then sums by target H3 as `land_physical_view_score`. Water finalization similarly multiplies water terrain, water vegetation, and water distance and reports both sum and max by target. These aggregations are good for effort-correction covariates, but raw sums are sensitive to source-cell density and should not be interpreted as a normalized probability without additional scaling.

## High-confidence issues

### 1. The same artifact can mean “observable area” or “weighted support,” depending on stage

**Evidence.** Terrain partitions persist only `weight_terrain`, while rich diagnostics such as visible area fraction and observer fraction are discarded in the compact partition. The final view score then multiplies terrain, vegetation, and distance and sums by target.

**Why it matters.** For app products, “generalized viewable area from Lime Kiln” should answer a different question than “distance/vegetation-attenuated observer effort support.” If users see only the attenuated product, distant but physically visible water may disappear. If effort correction uses only binary visibility, it may over-credit barely visible distant water.

**Recommendation.** Define three stable product families:

- `observable_binary`: target H3 is visible from at least one source/sample under clear-sky physical LOS.
- `observable_area_fraction`: clear-sky visible water fraction by target H3, optionally generalized/dissolved for the app.
- `effective_view_score`: terrain × distance × vegetation × enabled temporal modifiers, intended for effort correction. The canonical configuration currently disables the weather modifier and retains daylight context.

Keep these in separate parquet/PMTiles/GeoJSON outputs and avoid overloading “viewability.”

### 2. Default sampled aggregation can reduce output quality for shoreline and small-target H3 cells

**Evidence.** Normalized config defaults to sampled H3 aggregation with `pixel_stride: 10`. The H3 aggregation samples visible pixels on the global row/column stride and estimates visible area as sampled pixel count × pixel area × stride².

**Why it matters.** At 30 m DEM resolution and stride 10, the effective sample lattice is roughly 300 m. H3 resolution 7 cells have edge lengths around kilometer scale, and many coastal/water cells are partial, narrow, or shoreline-fragmented. A 300 m lattice can miss narrow visible corridors or overrepresent cells based on grid phase. This can materially affect place-of-interest ranking because POI viewable areas often hinge on shoreline geometry.

**Recommendation.** Use `aggregation_mode: full` for final static/app products, or add a validated two-pass mode: sampled for candidate generation, full aggregation only for candidates near the threshold or near POIs. If full mode is too slow region-wide, compute app products from a POI-specific subset or cache a full water-pixel-to-H3 map once per raster/H3 resolution.

### 3. Water-source LOS currently collapses platform classes to “best visible” in the simple builder

**Evidence.** The simple water LOS builder loops over small craft, ferry, and large commercial observer height classes, then writes `weight_terrain` as the maximum LOS across classes.

**Why it matters.** “Observable on water” is not a single physical condition. A ferry bridge, whale-watch vessel, kayak, and shoreline user have different horizon distances and visual constraints. Collapsing to the best platform can overstate observability for general boating effort and can make app products confusing if they imply a small craft can see where only a large commercial platform could.

**Recommendation.** Preserve platform class in water-source pair artifacts or publish separate app products: `water_small_craft_viewable`, `water_ferry_viewable`, `water_large_platform_viewable`, plus an optional `water_any_platform_viewable` for maximum envelope.

### 4. Final target sums are not normalized for source density or source-type semantics

**Evidence.** Land finalization sums pair scores by target H3 and counts unique visible sources. Water finalization sums all-platform physical weights and also records a best score.

**Why it matters.** A target near many source H3 cells naturally gets a larger sum than a target near fewer cells, even if the per-observer viewing quality is similar. That may be desirable for observer effort if source-cell count approximates observer opportunity. It is less appropriate for “how viewable is this water cell?” or for comparing POIs unless source availability and actual human access are modeled.

**Recommendation.** Publish both `sum` and normalized variants:

- mean score over candidate source cells,
- max/best viewpoint score,
- count of visible sources,
- access-weighted sum if/when source accessibility is modeled,
- POI-level summary statistics over the viewable target set.

### 5. Source cells may not equal actual observer-accessible places

**Evidence.** Source points are sampled inside land H3 source cells. The normalized defaults only require a minimum land fraction of 0.01 and allow coastal cells with high water fraction. The vegetation sampler uses polygon sampling but does not by itself know about roads, parks, trails, pullouts, private land, ferry terminals, or viewpoints.

**Why it matters.** For observer-effort correction, physical visibility from arbitrary land pixels can overstate effort in inaccessible shorelines and understate known high-effort viewpoints. For app POIs, arbitrary H3 source cells are less meaningful than named viewpoints/segments.

**Recommendation.** Split “physical visibility substrate” from “observer availability.” Keep current land-cell substrate, but add an accessibility/source-weight layer: parks/viewpoints/trails/roads/ferry terminals/population/tourism proxies. For app POIs, calculate viewable areas from actual POI points or buffered viewpoint polygons rather than all land cells.

## Medium-confidence risks

### 1. DEM sea-surface and bathymetry handling should be explicitly QA’d

The DEM preparation reprojects USGS 3DEP to WGS84 and the viewshed uses terrain surface plus target height. In coastal DEMs, water pixels can contain nodata, interpolated surfaces, bridges, docks, or shoreline artifacts. Because water targets are masked after viewshed generation, DEM values under water can still affect target elevation or line-of-sight behavior depending on backend semantics.

**Recommendation.** Add a QA report for DEM value distribution over water mask: min/median/max, nodata fraction, unexpected positive/negative values, and spot checks around San Juan Islands, Puget Sound narrows, Hood Canal, and major ferry routes. Consider forcing water DEM cells to sea level for viewshed analysis if backend behavior warrants it.

### 2. Land-crossing shortcut for water sources depends on representative lines

The water shortcut classifies open-water pairs using a straight line between source and target H3 representative points / centroids and prepared land intersection. This is fast and sensible, but H3 cells are areas, not points; a line between centroids can cross land even when part of the source/target cell pair has water-to-water visibility, or avoid land when the area-level corridor intersects islands.

**Recommendation.** For high-value POIs and app products, use edge/area-aware checks: multiple sample points per water H3, line-of-sight against dissolved land buffered by a small tolerance, or DEM fallback for uncertain line-crossing cases near islands/shoreline.

### 3. Curvature/refraction assumptions need sensitivity testing

The default curvature coefficient is `0.85714` and GDAL applies it through `gdal_viewshed`. That is a standard effective-earth style value, but marine visibility is sensitive to refraction, observer height, target height, and atmospheric conditions.

**Recommendation.** Run a small sensitivity matrix over curvature coefficient, observer height, and target height for known viewpoints and compare visible-area envelopes. Publish chosen defaults in metadata.

### 4. Land-cover attenuation can double-count canopy obstruction

The canopy surface now models trees as hard physical obstacles inside terrain LOS. Multiplying those results by the older vegetation path factor would count CHM twice because that factor also incorporates CHM-derived obstruction.

**Recommendation.** Keep land-cover access/transmission independent of CHM-based hard LOS. For app POI products, use POI-specific observer points placed in known viewing openings and apply a separate land-cover accessibility model only when it represents information not already present in the canopy surface.

### 5. Weather/daylight dynamic viewability should be causally aligned to forecast/evaluation dates

The user goal includes ranking POIs against forecast likelihoods and historic sighting density. The dynamic viewability config references daylight, lunar features, and date windows; its weather input is disabled after retirement of the atmospheric-viewability score product. If weather is reintroduced, it must use the strict R5 core-weather contract and preserve availability timing rather than recreating the retired proxy score. Any use in model training/evaluation must ensure that same-day or future products are not leaking information unavailable at forecast time.

**Recommendation.** Treat static viewshed as time-invariant, but version dynamic effort features by issue time / forecast horizon. For historical correction, define whether weather is observed/reanalysis or forecast-issued; for future ranking, use only forecasts available at app run time.

## Low-confidence smells worth checking

1. **H3 resolution contract.** Source and target resolution are both 7 by default, but app products may want lower-resolution generalized outputs. Aggregating pair-level scores to lower H3 parents must be area-weighted or source-normalized, not naive summed duplicates.
2. **Output path ambiguity.** Viewability config points `source_target_lookup`, `distance_weights`, `terrain_weights`, and `vegetation_weights` to the same static land weights parquet. That may be intentional for a consolidated app prep artifact, but it is easy to misread as separate factor inputs.
3. **Threading around GDAL external processes.** The GDAL backend runs multiple external `gdal_viewshed` calls concurrently. This can be fast, but IO/temp-raster pressure may dominate on network disks or small containers.
4. **Bitmask observer counting capped at 63 samples.** The code falls back when more than 63 observers are used, but high sample counts would change how `visible_from_n_points` is computed.
5. **Sparse inner joins drop pairs silently in finalization.** Inner joins between terrain, vegetation, and distance are appropriate if all factors are required, but missing vegetation rows can remove terrain-visible pairs entirely. Track row counts before/after each join.

## Output quality improvements

### A. Define product contracts first

Recommended static product table columns:

| Product | Grain | Key columns | Meaning |
|---|---:|---|---|
| `source_target_clear_sky` | source H3 × target H3 | `terrain_binary`, `observer_sample_fraction`, `visible_area_fraction`, `weight_terrain` | Physical clear-sky visibility before distance/vegetation. |
| `source_target_effective` | source H3 × target H3 | `weight_terrain`, `weight_distance`, `weight_vegetation`, `physical_view_score` | Effort-correction support. |
| `target_land_viewability` | target H3 | `source_count`, `score_sum`, `score_mean`, `score_max`, `binary_visible` | How observable a target is from land-source universe. |
| `poi_viewable_area` | POI × target H3 | `visible_binary`, `visible_area_fraction`, `effective_score` | App layer for selected places of interest. |
| `poi_rank_inputs` | POI | `viewable_forecast_mean`, `viewable_forecast_sum`, `historic_density_sum`, `historic_density_mean`, `viewable_area_km2` | Ranking features. |

### B. Improve H3/raster aggregation quality

- Use full water-pixel aggregation for final products where possible.
- For sampled mode, randomize or stratify sample grid by H3 cell rather than using a fixed global stride phase.
- Store `target_water_area_km2`, `visible_area_km2`, and `visible_area_fraction` in persisted outputs, not only compact weights.
- Add a minimum target-water-area threshold for app display, or classify tiny shoreline H3 cells separately.
- Smooth/generalize app polygons after scoring, not before scoring. Use score-preserving H3 parent rollups or vector dissolve only for display.

### C. Calibrate detectability assumptions

- Validate known viewpoints: Lime Kiln, Point Robinson, Bush Point, Fort Casey, Cattle Point, Westside Preserve, ferry routes, and urban waterfronts.
- For each, compare modeled viewable envelope against expert-expected visible waterways and horizon limits.
- Sensitivity-test observer height (1.7 m, 3 m, 10 m), target height (0.5 m, 1.5 m, 3 m), max distance (10/20/30 km), and distance curve.
- Use historic sightings only cautiously: sightings are effort-biased, so they validate “where people report from,” not necessarily true visibility.

### D. Improve POI-specific app products

For named places of interest:

1. Store POI geometry: point, shoreline segment, or polygon.
2. Generate 1–N observer points per POI using actual accessible/viewing locations.
3. Run or query source-target visibility from those source H3 cells.
4. Build a POI × target H3 table with binary visible area and effective score.
5. Join forecast likelihood or historic sighting density by target H3.
6. Rank POIs using both total opportunity and quality metrics:
   - `sum(forecast_prob * effective_score)`
   - `mean(forecast_prob over visible_binary area)`
   - `max/percentile forecast_prob in visible area`
   - `viewable_area_km2`
   - `distance-weighted expected sightings`

Do not use only raw summed score, because large viewable areas will always rank high even if forecast likelihood is diffuse.

## Optimization opportunities

### Current strengths

- GDAL viewshed output is aligned back to the parent water grid without per-observer reprojection when possible.
- Production runs allocate only the union window of observer viewsheds per source unless cumulative rasters are requested.
- Water-pixel-to-H3 mapping and target water area are cached by raster/H3 key.
- Source batches are grouped spatially and can reuse batch DEM/water context.
- The water open-water shortcut avoids DEM viewshed work for unobstructed water-source pairs.

### Highest-value speedups

1. **Two-pass aggregation.** Use sampled mode only to identify candidate target H3 cells, then full aggregate those candidate cells for final static/app products.
2. **POI-driven subset builds.** For app products, avoid rebuilding regional all-source products when only a list of POIs is needed. Map POIs to source H3 cells and evaluate only their relevant targets.
3. **Cache clear-sky binary envelopes by source cell.** If source/source config is stable, store source-cell visible target sets and diagnostics. Distance/vegetation/dynamic layers can be recomputed without rerunning terrain.
4. **Uncertain-only DEM fallback for water.** Keep shortcut for obvious open water, but route near-land ambiguous pairs to DEM or multi-sample tests.
5. **Tune worker allocation empirically.** GDAL external process parallelism can saturate disk before CPU. Add build logs with cells/sec, observer/sec, temp IO size, and memory, then choose defaults by environment.
6. **Use lower-resolution display products.** Keep analysis at H3 R7, but publish generalized R6/R5 app layers with documented aggregation logic for fast rendering.

## Alternative approaches

### 1. GDAL viewshed by named POI / viewpoint only

Instead of all land H3 cells, run viewshed from curated POIs and access points. This is likely the best app-product path: better semantic quality, smaller compute, easier QA, and directly aligned to “places of interest.” It is less suitable for broad observer-effort correction unless the POI catalog is comprehensive.

### 2. Raster cumulative visibility / visual magnitude surface

Create a raster where each water pixel stores the number or weighted sum of source observers that can see it, then aggregate to H3. This is close to the current target aggregation but could be run source-batched and accumulated globally. It is excellent for app heatmaps and effort surfaces, but requires careful source weighting to avoid dense-source bias.

### 3. Horizon + land-barrier approximation

For some water-source and low-relief marine cases, approximate visibility using horizon distance and vector land-barrier intersection without DEM viewsheds. This is much faster and often adequate over open water, but it misses terrain relief, headlands, elevated viewpoints, and local shoreline topography.

### 4. WhiteboxTools / GRASS / richdem alternatives

Other viewshed engines may support faster in-process operation or cumulative viewsheds. They should be benchmarked against GDAL on a small validation set before replacement. Avoid switching engines without equivalence tests because small differences in curvature, nodata, and target height handling can produce silent drift.

### 5. Network/accessibility-weighted effort model

For effort correction, physical visibility should be multiplied by human availability: road/trail access, parks, ferry schedules, vessel AIS, population/tourism, season/daylight/weather. This is not a replacement for viewshed; it is the next layer needed to make viewshed an effort model rather than a physical observability model.

## Recommended implementation roadmap

### Phase 1: QA and documentation hardening

- Add metadata to every static artifact: config hash, DEM path/hash, water mask path/hash, H3 resolution, aggregation mode, pixel stride, observer/target height, curvature coefficient, source type.
- Add row-count and score-distribution reports for each join and final artifact.
- Add canary maps for 5–10 named viewpoints and ferry/water cases.
- Add a written product contract distinguishing binary, area-fraction, and effective score outputs.

### Phase 2: Output quality upgrade

- Persist terrain diagnostics needed by app products: binary visible, visible area fraction, observer sample fraction, target water area.
- Run full aggregation for app/POI targets or implement two-pass sampled→full aggregation.
- Create POI × target H3 app products and ranking inputs.
- Publish normalized target and POI scores alongside raw sums.

### Phase 3: Calibration and validation

- Build a validation checklist for known viewpoints and water routes.
- Sensitivity-test key physical parameters.
- Compare app-facing generalized products to expert maps before using in ranking.
- For effort correction, test whether adding viewshed features changes model behavior plausibly without introducing spatial leakage or overfitting to reporting density.

## Minimal canary checks

Run these after any viewshed rebuild:

1. **Schema check:** all pair products have `source_h3`, `target_h3`, and factor columns in `[0, 1]`.
2. **Join retention check:** report rows retained after terrain→vegetation→distance joins by source type.
3. **Known POI check:** Lime Kiln / San Juan west side should see Haro Strait but not water hidden behind major landmasses.
4. **Water route check:** central open-water cells should have many unobstructed water-source pairs; island-separated pairs should be blocked or DEM-tested.
5. **Aggregation sensitivity check:** compare sampled vs full aggregation for 20 random source cells and 5 POIs; flag rank/order changes.
6. **Temporal check for dynamic layers:** for any date used in training/evaluation, verify weather/daylight features are from allowed observation/forecast issue times only.

## Bottom line

The current viewshed system is a solid physical-visibility substrate, especially for regional all-source/all-target processing. The main improvement is to make output semantics explicit and preserve enough diagnostics so the same computations can support three distinct needs: observer-effort correction, generalized app viewable-area maps, and POI ranking against forecast or historical whale-use surfaces. The best near-term path is not a wholesale rewrite; it is product separation, full/validated aggregation for app outputs, source-accessibility weighting, and canary QA around known viewpoints and waterways.
