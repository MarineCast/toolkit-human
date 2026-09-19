# 1. Executive Summary

Review date: 2026-09-19. Reviewed checkout: **toolkit-human**, commit `bc99300b00b1e1b2258a82a3ceb121f4e0783ce6`; distribution `toolkit-human==0.1.0`, import package `human`, executable `human`. The requested name `toolkit-humanlayer` does not identify a separate checkout in `WORKSPACE.yaml`. The working tree was clean before this review.

**The architecture is a credible research foundation, but is not yet a clean production human-environment dependency.** Acquisition/build/inspection families, explicit missingness, source snapshots, and the stronger generation contracts are worth preserving. Responsibility is broader than the requested boundary: this package also owns observation geometry, heuristic effort multipliers, reporting-opportunity composition, model feature selection, shadow-prediction formatting, and whale-oriented destination ranking.

The most immediate correctness problem is calendar source reuse: a valid but obsolete snapshot can produce false non-holidays and still publish `source_completeness=complete`. Calendar outputs also depend on the requested window. The largest spatial risk identified is center-based H3 candidate generation omitting small disconnected polygons; several access adapters also trust missing CRS/schema information. Historical infrastructure is explicitly contemporary research context in the strongest products, but this distinction is not uniformly enforced across base products.

**Readiness:** suitable for explicitly scoped research consumption, with pinned inputs and family-specific contracts; not ready for unrestricted production use or historical observer-effort inference. No P0 was established. Passing tests and a working wheel establish useful engineering baselines, not source completeness or scientific calibration.

This review changed only this document. No refactor, source acquisition, regional processing, artifact promotion, or consumer modification was performed.

# 2. Current Architecture

```text
Census / StatCan     WA / BC GIS / OSM      holiday library
        |                  |                      |
        +-------- family download / snapshot -----+
                           |
               family build / normalization
                           |
       population, places, shore/launch evidence, calendar
                           |
notebook routing snapshots + optional native road-only OSRM
                           |
                 transport / population reach
                           |
AIS archive + ferry sources ---> activity aggregates
                           |
legacy human.viewshed + external weather/daylight + access/calendar
                           |
        land/water reporting-opportunity research products
                           |
 family manifests / selected immutable generations / inspectors
                           |
 deep human.* imports and artifacts consumed by OrcaCast
                           |
       lag/formulation/shadow helpers ALSO remain in human

External checksum-bound sighting releases --> toolkit evaluation/diagnostics
```

Actual structure: 252 Python files under `src/human`, organized into `accessibility`, `demography_and_presence`, `temporal_context`, `activity_and_effort`, `viewshed`, `utils`, `core`, and packaged `resources/config`. There are 12 CLI families. Configuration lives in `config/` and a tested matching packaged copy. Tests cover human families and legacy viewshed; five notebooks retain research/acquisition workflows. There is no tracked Makefile, dependency lock/environment file, or repository CI workflow.

Guidance reviewed: workspace `AGENTS.md`, `WORKSPACE.yaml`, `.github/INFRASTRUCTURE.md`, toolkit `AGENTS.md`, README, architecture/contracts/workflow/migration documentation, relevant migration handoffs, family source notes/configuration, feature catalog, packaging and representative tests. No deeper AGENTS files were found. The existing Graphify `build_access_kernel` neighborhood confirms its links to land build, smoke, terrain, source sampling, and water support. Source was then used to verify behavior; graph freshness was not certified and no graph was rebuilt.

Source adapters exist in `utils/{arcgis,wfs,osm,acquisition}.py` and family-specific code. Normalization often remains in a family's `build.py`; that is reasonable while small, but required external-schema checks are inconsistent. Infrastructure is split among `utils/artifacts.py`, `core/artifacts`, and family generation modules. This is a local implementation, not adoption of the proposed workspace-wide product contract.

A static import scan found an apparent cycle around `viewshed.prepare.area` re-exports and `from . import domains`. Because package re-exports confound this simple analysis, it is not evidence of a broken runtime cycle. All 35 available family-stage modules imported from the installed wheel. Dependency complexity is more clearly demonstrated by direct consumer imports and modules exceeding 1,200 lines than by an asserted circular-import failure.

# 3. What Is Working Well

- Independent package installation, explicit workspace initialization, resource shipping, and an AST test prohibiting OrcaCast imports. No reverse runtime application import was found.
- Source checksums, resolved configuration hashes, provenance records, and checksum-verified readers. Land/ferry/water generation machinery provides stronger publication guarantees than ordinary flat outputs.
- Controlled missingness distinguishes observed/derived zero, unknown, partial, unmapped, unavailable, and processing failure. Source outages do not automatically become zero AIS or access.
- Public-shore processing uses projected dissolved line intersections; points do not become shoreline length. Denominator mismatches retain QC evidence and a null fraction rather than clipping misleading ratios.
- Population retains US 2020 and Canadian 2021 vintages, unavailable Canadian records, and separate country components in border cells.
- Fine spatial support and full water-area denominators have dedicated regression tests. Legacy distance attenuation is documented as already integrated; no second multiplication is recommended.
- AIS vessel-hours are labeled proxies; ferry passengers, vessel-platform measures, estimates, and fallbacks remain distinguishable. No canonical sum of overlapping water components is imposed.
- Research schema adapters require explicit opt-in. Documentation admits source gaps and historical limitations instead of claiming production completeness.
- The test suite exercises substantive scientific and failure behavior, including unavailable inputs, temporal alignment, generation integrity, and sparse/dense equivalence.

# 4. Findings

Paths below are relative to this repository unless explicitly prefixed with `Modeling/OrcaCast`. Line references describe the reviewed commit. P1 means a significant correctness, scientific, or architectural risk; it does not imply a demonstrated bad regional release.

| ID | Severity | Area | Finding | Evidence | Recommendation |
| -- | -------- | ---- | ------- | -------- | -------------- |
| F01 | **P1** | Temporal/source contract | Calendar build accepts an obsolete or incompatible holiday snapshot and publishes false non-holidays as complete. Checksums validate bytes, not requested coverage/jurisdiction. | `src/human/temporal_context/calendar/build.py:71–94,104–109`; `_holiday_columns` maps absent holiday names to empty strings. Reproduced: download for 2020, change config to 2025-01-01..02, build succeeds and labels New Year's Day non-holiday for both countries, completeness `complete`. | Validate raw product, artifact identity, supported years and jurisdiction against the request before build. Missing coverage must fail or be explicitly unavailable. |
| F02 | **P1** | Responsibility/science | Toolkit supplies observer-response assumptions and consumer model behavior, contrary to the requested human-layer boundary. | `calendar/features.py:136–158` adds weekend 0.20, holiday 0.15 and season bonuses; `land_reporting_opportunity/dynamic.py:463–473,514–531` applies weather curves and calendar weights. `land_reporting_opportunity/modeling.py:128–237` owns model lags, formulations, and shadow fallback. `places/build.py:482–484` adds 3 points for whale/orca names. All paths are under `src/human` with their respective category prefixes. | Keep measured/interpretable components; transfer application ranking, prediction behavior and observation-response composition through a separately scoped consumer migration. Retain versioned research compatibility until consumers migrate. |
| F03 | **P1** | Model eligibility | The modeling helper explicitly selects coverage/availability predictors despite the stated policy that coverage is metadata. This bypass is independent of catalog eligibility. | `src/human/activity_and_effort/land_reporting_opportunity/modeling.py:23–31,194–209`: every non-baseline formulation seeds its allowlist with coverage measures and adds `prior_land_effort_available`. Reproduced `composite` selection returning `previous_week_land_dynamic_coverage`. `src/human/TODO.txt`, shared-contract policy says coverage stays metadata. | Remove default coverage predictors; if required for an application experiment, make the exception explicit downstream and separately assess reporting/source-adoption bias. |
| F04 | **P1** | Atomic publication | Base access products publish files before their manifest, so an interrupted overwrite can invalidate the previous usable release. Per-file atomicity is not family atomicity. | `src/human/accessibility/boat_launch_access/build.py:252–267` writes facilities then H3 output, with manifest later; `src/human/utils/artifacts.py:55–68` replaces each destination independently. | Stage a complete family and atomically switch its manifest. Test failure after the first output replacement. Reuse a local proven generation pattern rather than inventing an ecosystem framework. |
| F05 | **P1** | Spatial support | Population grid candidates start from center-contained cells, then expand one ring. A tiny disconnected polygon with no contained center is unsupported; far disconnected islands can be omitted even when a mainland supplies base cells. | `src/human/demography_and_presence/population/common/h3_grid.py:78–97`. Reproduced an EPSG:4326 box at approximately (-123,48), width/height 0.00002°, R7: `RuntimeError: H3 polygon fill returned no base cells for the boundary.` | Construct intersecting candidates for every polygon component, including components with no contained H3 center; retain exact clipping. Add tiny-island, narrow-polygon, and mainland-plus-island tests with conservation. |
| F06 | **P2** | Calendar/window invariance | Same date gets different long-weekend and nearest-holiday values depending on the requested interval. | `calendar/features.py:111–132` searches holidays only in the output frame; `calendar/holidays.py:76–78` builds only requested years. Reproduced May 25, 2024 long-weekend false for May 25–26 but true for May 24–27; Dec 31, 2024 nearest US holiday 6 days versus 1 when the end extends into 2025. | Build sufficient boundary context before cropping; unify snapshot and direct APIs. The download path already includes adjacent years, but its long-weekend calculation still crops too early. |
| F07 | **P2** | Source adapters/CRS | Missing required external columns can silently remove WA launch records; missing source CRS is guessed as WGS84. Optional fields and structural failures are conflated. | `src/human/accessibility/boat_launch_access/build.py:40–45,93–96,121–124,151–153,186–189`: `_column` defaults to all-null, launch selection becomes empty, and normalizers use `frame.crs or 'EPSG:4326'`. | Validate required provider fields and declared CRS before normalization. Keep optional amenity fields nullable; fail on renamed required fields and unknown coordinates. |
| F08 | **P2** | Provenance | Generic run identity is only a config hash; manifests lack required processing revision/dependency identity and use absolute paths. Reused snapshots receive a new apparent retrieval time. | `src/human/utils/artifacts.py:126–153,179–198`; `run_id=config.config_hash[:16]`. Routing download reuses snapshots and calls `source_record` again (`accessibility/land_transport_access/download.py:51–58,93–103`). | Separate source acquisition, adoption, and build times; record software revision/version and dependency fingerprint; distinguish run ID from config hash and define relocatable artifact references. |
| F09 | **P2** | API/integration | Package root exports no supported product API; consumers depend on deep implementation modules. Documentation claims application shadow behavior remains downstream although helpers remain here. | `src/human/__init__.py`; `docs/ARCHITECTURE.md`; consumer `Modeling/OrcaCast/src/orcacast/models/sightings/weekly/joint_land_effort.py:20–23`, evaluation `land_opportunity/shadow.py:18–23`, publishing `places_of_interest.py:11–15`. | Publish a small validated product-reader facade and compatibility policy; move application helpers separately. Deep imports are observed, not hypothetical. |
| F10 | **P2** | Reproducibility | Default routing adopts notebook outputs; the alternative reproducible graph builder hard-codes one Homebrew installation. Neither provides a portable clean-checkout regional workflow today. | `src/human/accessibility/land_transport_access/download.py:45–49`; `routing.py:17–19,63–83`; `context_snapshot.py:19–28` reads notebook-derived population/city origins. | Configure executable/profile paths and validate versions; publish the exact origin-selection pipeline. Preserve archived snapshots as research evidence. |
| F11 | **P2** | Tests/CI | No tracked CI protects installation, dependency consistency, tests, wheel resources, lint/type checks, notebooks, or security scanning. Current packaging tests execute within the source checkout. | `git ls-files '.github/*' '*lock*' '*requirements*' '*environment*' '*Makefile*'` returned no files; `pyproject.toml`, `tests/test_packaging.py`. | Add wheel-outside-checkout CI, dependency checks and offline tests; add explicit lint/type/security controls with scoped baselines. Do not assume organization branch/security settings are absent; they were not inspected. |
| F12 | **P2** | Notebooks/documentation | No small production-API validation notebook; AIS exploration imports a nonexistent module, while the land notebook contains thousands of lines of duplicated research calculations. | `notebooks/exploration/02_AIS.ipynb` imports `human.activity_and_effort.ais.analysis`, absent from source and wheel; `notebooks/viewshed/02_LAND_BASED_VIEWSHED.ipynb` has 5,309 code lines, routing requests and dynamic calculation functions. `src/human/README.md` still says viewshed was not migrated. | Repair or explicitly retire broken examples; add one deterministic validation notebook calling package APIs and verifying manifests. Label retained legacy notebooks as historical, with no production authority. |
| F13 | **P2** | Access semantics | Launch counts count provider records rather than unique physical launches; historical/operating validity and restricted access remain incomplete. | `src/human/accessibility/boat_launch_access/build.py:159–163,211–226,288–297`; source notes explicitly acknowledge agency/OSM overlap and circa-2004 BC inventory. | Name counts as source-record counts until validated matching exists; retain provider IDs, restrictions and time validity. Do not represent these as observed daily launch use. |
| F14 | **P2** | Spatial configuration | Shoreline length CRS and sampling spacing are accepted without verifying metric units or a candidate-coverage bound. A configurable geographic CRS would yield angular lengths labeled meters; excessive spacing can miss cells. | `src/human/accessibility/public_shore_access/config.py:123–124`; `build.py:329–366`. Defaults use EPSG:32610 and 200 m, so this is an accepted-configuration risk, not evidence the default run used degrees. | Validate projected meter units, positive spacing, and a conservative resolution-dependent spacing bound or use guaranteed intersecting candidates. |

No finding asserts that research warnings are absent everywhere. They are substantial; the problem is uneven enforcement and an ownership boundary broader than the requested one.

# 5. Responsibility Boundary

| Capability | Belongs Here | Belongs Downstream | Needs Discussion |
| ---------- | ------------ | ------------------ | ---------------- |
| Provider acquisition, normalized site records, source provenance | Yes | No | Rights/completeness gates per provider |
| Census allocation and vintage-preserving population context | Yes | No | Allocation assumptions and spatial support must remain explicit |
| Raw holiday/weekend/date flags | Yes | No | Locale/time basis must be declared |
| Raw routes, travel minutes, road proximity, origin coverage | Yes | No | Network scope, legal destination and ferry semantics |
| 60/120/240-minute decay components | Potentially, as named scenarios | Model selection/calibration | Reusable domain assumptions, never observed visitation |
| Calendar effort bonuses and peak “viewing season” | Raw season labels only | Response coefficients/composition | Existing coefficients are heuristic, not verified fitted effects |
| AIS/ferry acquisition and class-separated activity | Yes in current ownership | Observation/detection effects | Eventual AIS toolkit migration is separate scope |
| Legacy terrain/canopy geometry | Compatibility dependency | Application interpretation | Dedicated viewshed toolkit is preferred eventual owner; numerical equivalence required |
| Weather/daylight weighted reporting composites | Publish raw supporting covariates | Yes | Retain old formulas only as explicit research compatibility |
| Whale/orca destination-name bonus, display ranking | Normalize base places | Yes | UI export is application responsibility |
| Sighting association, border diagnostics, shadow predictions, feature formulations | Generic validation primitives only | Yes | Outcome-aware diagnostics should be visibly isolated |
| Closures/legal access evidence | Carry sourced state | Model interpretation | Authoritative regulatory geometry belongs to governance |

No species-trained effort coefficients were established in the inspected producers. The boundary violation is demonstrable heuristic observation/model logic and whale-oriented ranking, not a claim that all coefficients were fitted on SRKW labels.

# 6. Public API Assessment

**Intended today:** `human`/`python -m human`, `initialize_workspace`, and documented family `download`, `build`, `inspect` modules. There are 12 family names and 35 available family-stage combinations. Family builders return paths; configuration uses `HUMAN_WORKSPACE` or cwd. Root import is intentionally minimal but offers no product reader.

**Accidental API:** consumer dependencies on `human.core`, `human.utils.artifacts`, family configuration classes, `land_reporting_opportunity.modeling`, places utilities and viewshed finalization. Internal result columns, config field names and schema adapters are consequently already compatibility surfaces. A package rename or directory reshuffle would break real consumers.

**Minimum recommended stable surface:** retain CLI syntax and existing APIs during migration; add a small `human.products` interface for `resolve_product`, `load_product`, and contract inspection. These are proposed names, not existing functions. It should resolve a manifest once, validate product/schema/support, return data with provenance, and reject unknown semantic versions. Keep family-specific builders available under documented modules; a universal builder abstraction is unnecessary. Offer neutral calendar generation independently of response weighting.

Compatibility must cover row keys, CRS/H3 support, units, null/state semantics, period definitions, formula/schema versions, and interpretation. Research schema acceptance must not imply ecological-model eligibility. An installed package test should exercise representative readers with tiny manifests, not merely import symbols.

# 7. Data Contract Assessment

| Product | Grain/geometry/CRS | Units and temporal meaning | Provenance/validation | Assessment |
| --- | --- | --- | --- | --- |
| Calendar | One date, wide US/CA jurisdiction flags; no geometry | Boolean context, day distances; unitless heuristic multiplier/weight; date strings | Snapshot version and manifest, required-column validator | Useful schema, but stale coverage and window invariance fail. Jurisdiction is config, not an explicit row key. |
| Population | Country-qualified R7 allocations and collapsed R7 context; configured output EPSG:4326 polygons | Allocated persons; US 2020 versus CA 2021; land support | Country validators, population conservation/harmonization tests and unavailable-source evidence | Stronger scientific contract. Fractions are estimates, not exact people per hex. Small polygon candidate gap remains. |
| Launches | `ACCESS_SITE_ID`, WGS84 point/representative point; occupied R7 table | Source-record counts; capability/public/season states, variable vintage | Unique per-source IDs, manifest, provider notes | Physical duplication unresolved; absence is unknown, not zero; required provider schema/CRS insufficiently strict. |
| Public shore | Source site points; projected line intersections aggregated to R7 | Shoreline meters, fraction [0,1] where defensible; snapshot context | Denominator mismatch QC, null BC numerator, access evidence tests | Good missingness and length semantics; CRS/config guards and dated validity need work. |
| Transport | One R7 source centroid, tabular H3 | Meters, minutes and exponential components; contemporary network snapshot | Row uniqueness, snapshot checksums, graph metadata for road-only variant | Centroid reachability is not access to a legal shoreline entrance. |
| Population travel | One R7 destination with selected R4 origins | Population-weighted exponential demand; log1p/q99 components [0,1] | Evaluated/routed-origin fractions and cap metadata | Scenario-dependent support, not trips or visitors; default origins remain adopted research context. |
| AIS | DATE or WEEK_START × R6; no point tracks in aggregate output | Unique vessels and unique MMSI-hour proxies, observed-hour support | Required input schema, coverage and class checks, partial-source gate | Not exact durations, complete traffic, or observer hours; unresolved input rights/provenance. |
| Ferry | Native date × route × R7 sources plus aggregates | Rider/platform minutes and vessel-km proxies, measured/estimated/fallback status | Crosswalk, manifests, conservation and weekly tests | Monthly-to-daily estimates must not become observed service-date demand. |
| Legacy static viewability | Source R7 × target R7; explicit terrain/water support | Bounded geometry/attenuation weights; static | Metadata schema, pair and raster fingerprints, dense/sparse tests | Distance already integrated. Not a sighting/detection probability; not certified interchangeable with toolkit-viewshed. |
| Land/water opportunity | Daily/weekly R6 targets; source tables and support artifacts | Formula-specific weighted sums, component states, conditions, coverage | Strong research schema/lineage and generation checks | Much stronger envelope than base families, but beyond desired producer remit. |
| Places | Catalog IDs and nearshore point representations | Names/categories and heuristic ranking; snapshot | Explicit schema and source metadata; catalog excludes model use | Base locations reusable; whale-oriented ranking/UI export downstream. |

Generic manifest `schema` is an observed name/dtype inventory, not a declarative contract for geometry, units, nullability, ranges, or identifiers. Strong validators exist in population and opportunity modules; base families need smaller explicit contracts rather than another framework. `activity`, `opportunity`, `weight`, and `count` must be interpreted with family formulas: they are not interchangeable measures.

# 8. Geospatial and Temporal Risks

**Spatial calculations.** Inspected population allocation uses projected area CRSs (US EPSG:5070, Canada EPSG:3005). Shoreline lengths use projected EPSG:32610 by default. Place distance helpers reproject; no blanket allegation of degree-based distance calculations is warranted. F07/F14 concern accepting invalid input/configuration. Geometry repairs and H3 boundary expansion are present, but F05 proves a small-polygon limitation.

Launch and shore points use representative points for non-point records, losing footprint support for cell assignment. Coastal proximity does not establish traversability: an island, estuary, cliff, fence or private property can separate a site from a target. Road-only routing improves ferry exclusion and disconnection states, but centroid snapping still differs from reaching a public entrance. Explicit restrictions and missing access data should accompany raw distances. No transport-network replacement is required for simple proximity products.

Shoreline fractions use a length denominator, not cell area; launch counts are occupied-cell source records, not density per km². Do not rename either to “density” without a denominator/window. No validated general marina-density or visitation-density API was established. Source overlap and boundary sampling need conservation fixtures.

R7-to-R6 aggregation requires full intended support, including zero children. New opportunity contracts deliberately retain canonical water denominators. AIS R6 allocation to finer children remains an assumption, not measured fine-scale activity. Preserve native ferry support and avoid counting overlapping ferry/passenger AIS twice.

**Time.** Calendar uses date values rather than event timestamps; the shipped config says UTC. US/BC holiday interpretation requires an explicit choice of local civil date when joining UTC activity. Inspecting a date-only field does not prove a timezone conversion bug, but boundary tests are missing. Gregorian `year` plus ISO `week_of_year` is an unsafe composite key near New Year; use date/Monday week start or an explicit ISO year.

Infrastructure, circa-2004 BC launch data, modern OSM, census vintages, historical AIS, and ferry service dates have different knowledge times. Strong opportunity tables include `KNOWLEDGE_TIME_UTC`, source vintages and `HISTORICAL_RECONSTRUCTION`; the land reader explicitly calls results retrospective contemporary-static context. This is an acknowledged limitation, not certified historical replay. Base access products need equivalent machine-readable validity/unknown states. Recorded closures/opening-hours text is evidence; it is not yet a full operational calendar.

Seasonal flags describe configured months, not empirically measured tourism. The fixed 2020–2024 water interval and 2020–2026 land examples are research windows, not current operational feeds. Modern maps must not be silently backfilled into earlier model years.

# 9. Scientific / Modeling Risks

- **Observer-bias leakage:** `evaluate.py` and water diagnostics ingest sightings. Their diagnostic presence is not proof that labels train the producer. The strongest notes explicitly prohibit choosing sensitivity curves from whale outcomes. Keep outcome diagnostics outside the supported producer path and audit selection decisions downstream.
- **Target leakage:** no direct label-trained base-layer calculation was established. Whale-name place ranking is explicitly target-oriented selection, however, and belongs outside a neutral geographic catalog.
- **Reporting-process leakage:** F03 allows coverage and availability into model feature sets. Those fields can encode source adoption or reporting geography; a separate diagnostic experiment is different from a default biological predictor.
- **Spatial leakage:** population-travel normalization uses a source-universe q99 cap (`population_travel_time/build.py:58–74`). Changing the domain changes scores; fitting across held-out regions would be transductive preprocessing. Publish raw demand and reference support; fit model transforms on training support where required.
- **Temporal leakage:** fixed-reference scaling can use later periods when applied to early dates. Land modeling deliberately consumes raw values and prior completed weeks, which mitigates this path. That protection must remain explicit if downstream consumers select bounded indices instead. Contemporary infrastructure cannot certify historical knowledge.
- **Proxy interpretation:** residents are not tourists; launches are not trips; passengers are not reporting observers; AIS presence is not survey effort; physical viewability is not detection probability. Exponential travel decay, calendar bonuses and weather curves are scenarios. Their determinism alone does not establish empirical validity.
- **Composite indices:** land composites expose components and states, a useful design. Yet default multiplication embeds behavioral assumptions before the consumer can fit them. Preserve unweighted ingredients and place interpretation in an application-owned composition layer.

Privacy review found public infrastructure and aggregate census products as the main inputs. AIS raw/intermediate data include MMSI and hour/cell identity (`ais/build.py:28,136–138,197–214`), which can identify vessels; do not distribute that archive by default. No evidence of raw personal-device trajectories or person-level identifiers was found in inspected production paths. AIS rights are explicitly unresolved and ferry rights are recorded only as broad provider terms. This review does not determine legal redistribution rights or verify current provider terms; source licensing text here describes repository declarations only.

# 10. Test Coverage Assessment

**Executed:** 327 tests passed in 28.44 seconds on Python 3.14 after an editable installation in a temporary venv using existing system-site dependencies. The initial run before installation failed with `ModuleNotFoundError: human`; that is an environment prerequisite, not a repository regression.

| Purpose | Existing protection | High-value gap |
| --- | --- | --- |
| Package/import | No OrcaCast AST imports, workspace behavior, resource/config parity, artifact imports | Automated clean dependency resolution, minimum Python matrix and installed-wheel tests |
| Source adapters | Mocked OSM outages, WA enum mapping, StatCan parsing, ferry source fixtures | Required-field rename/removal, schema/type drift, CRS absence and pagination mutation |
| Data contracts | Opportunity state/value consistency, lineage, schema guards, population keys | Uniform small contracts for base access/calendar products |
| Geospatial | Raster support, pair identity, terrain composition, allocation and shoreline QC | Tiny disconnected islands, CRS-unit rejection, sample-spacing invariance, physical-site duplication |
| Temporal | Calendar pipeline, land date windows, ferry weekly aggregation and lag histories | Calendar subset invariance, stale jurisdiction/year snapshots, civil-date/UTC boundaries |
| Integration/regression | Synthetic production land smoke, generations, sparse/dense and batch invariance | Installed readers with real consumer contracts; portable routing setup |
| Failure/recovery | Land failed-publication and mixed-generation checks; core artifact tests | Interrupted flat access-family overwrite and concurrent-writer tests |

Structure/import assertions are useful but lower-value than numeric and failure fixtures; they cannot prove a layer is scientifically safe. Avoid expanding tests that merely mirror formulas while leaving boundary conditions untested. The autouse fixture creates temporary synthetic water/land files under the configured workspace and permits existing files to win (`tests/conftest.py`). This can make some tests depend on machine state; prefer a dedicated temporary workspace with explicit real-data tests separately.

Manual review probes demonstrated F01, F05, F06 and the F03 allowlist bypass without editing tests. No broad source tests were added during this audit.

**CI:** no local workflow file was found. Consequently no repository-defined guarantee exists for tests, lint, formatting, type checking, secret scanning, dependency consistency, package builds or notebook validation. Remote organization/security settings were not queried. Add controls rather than weakening existing assertions.

# 11. Reproducibility / Provenance Assessment

| Input/source | Authority and configured location | Version/time/coverage | Rights/limitations recorded in repository |
| --- | --- | --- | --- |
| US population | Census API `api.census.gov/data/2020/dec/pl`, TIGER geography | 2020 population; geography URLs/vintages in population config; US regional subset | Census source context; spatial allocation is derived |
| CA population | Statistics Canada 2021 BC profile download and DA boundaries | 2021; explicit unavailable population records | Official source attribution; mixed-vintage cross-border context |
| WA access | Ecology ArcGIS SEA FeatureServer layers 3/4/9 | Mutable live layers; acquisition hash/time, incomplete effective dates/update cadence | Ecology disclaimer; shoreline mismatch limitations |
| BC launches | DataBC ArcGIS coastal-launch layer | Circa 2004, no planned updates per local notes | Open Government Licence BC; public status/operation unknown |
| BC recreation/shoreline | DataBC recreation WFS and ShoreZone WFS | Snapshot partial inventory; shoreline is denominator only | BC licence recorded; no complete public-access numerator |
| OSM | Overpass endpoints; Geofabrik dated regional PBF extracts for routing | Live supplements versus dated routing extracts; hashes recorded | ODbL attribution; optional outage state; no completeness inference |
| Routing | Adopted OSRM notebook results or pinned native road-only graph | Default historical snapshot graph unreconstructable; alternative records graph/runtime/profile hashes | OSM-derived provenance; ferry/closure/destination limitations |
| Calendar | Installed `python-holidays` | Library version stored in snapshot; padded years on download | MIT declaration; build does not enforce snapshot request compatibility |
| AIS | Locally supplied yearly Parquet archive | 2020–2024, R6 MMSI-hour aggregates, partial coverage | Endpoint and licence unresolved; strict production blocked |
| Ferry | WSF Tableau, official BC monthly totals, WSDOT history, route crosswalk | Observed WSF and estimated BC allocations, route/history gaps | Provider terms and crosswalk provenance; redistribution not certified |
| Places | WSDOT terminals, WA parks/Ecology marinas, OSM, Natural Earth | Mutable snapshots; Natural Earth “10m” is resolution, not a release version | Mixed public terms/ODbL; application ranking |
| Geometry/conditions | Externally provisioned seascape, weather/daylight; legacy terrain/canopy workflow | Checksum-bound inputs and selected immutable inventories | Separate producer coverage and availability remain required |

Source authority, URL and licence are often configured; retrieval time and hashes are common. Dataset revision, effective dates, update frequency, CRS, schema evolution, and complete geographic coverage are unevenly declared. A live URL plus a hash identifies obtained bytes but does not guarantee the same source can be recovered later. No live provider validity or licensing claims were verified in this audit.

**Computational reproducibility:** strongest with frozen inputs, explicit config and existing scientific fixtures. Weaknesses include unbounded dependency ranges, missing processing identity in generic manifests, per-family lifecycle differences, default current-date behavior in the direct calendar helper, notebook-derived origins and hard-coded native tools. No random-output defect was established; deterministic sampling is tested in the geometry workflows.

**Source reproducibility:** weaker. Default routing and AIS require external snapshots; mutable GIS/Overpass/Tableau sources can change. Archive original permissible bytes, provider metadata, source schemas and immutable identifiers where available. Preserve acquisition timestamps separately from adoption/rebuild time.

**Publication/portability:** land/ferry/water generations are not a universal guarantee for all families. Generic manifest paths are absolute; moving a workspace breaks verification. A reader can also depend on present config/upstream files even for an archived generation. Define relocation and historical-read modes explicitly instead of silently disabling checksum checks.

**Actual install checks:** editable installation succeeded with `--no-deps --no-build-isolation`. A regular wheel built and installed into a second temporary venv. From `/tmp`, `human.__file__` resolved to its site-packages directory, all 35 family-stage imports succeeded, `init` copied 22 configs, and the routing Lua/artifact APIs were present. `pip check` reported no broken requirements. Both venvs reused the existing scientific dependency environment: this is not a fresh network dependency solve or proof of Python 3.11 compatibility. No full GDAL/OSRM regional pipeline was run.

Configuration classification: H3 identity conventions and unit conversions are domain/implementation constants; buffer/radius/grid/CRS choices define product support; provider URLs/vintages are dataset configuration; chunks/workspaces are runtime configuration; calendar response bonuses, weather exponents, model formulations and shadow fallback are model/scenario policy. Making all of the latter configurable inside this toolkit would not solve the ownership problem. `config/` and packaged resources currently match and are tested.

# 12. Performance Findings

No regional benchmark was executed, so **no demonstrated regional bottleneck** is claimed.

| Classification | Evidence | Appropriate next step |
| --- | --- | --- |
| Likely bottleneck | `public_shore_access/build.py:341–364` dissolves regional linework, samples in Python, and intersects the whole union for every candidate cell | Benchmark increasing shoreline length/cell count and peak RSS; compare indexed local intersections only if material |
| Likely bottleneck | Land access kernel retains a regional water-pixel index and final sparse kernel; dynamic basis scales with sparse support and date chunks | Benchmark source samples × radius × water pixels × dates, with peak RSS and resume hit rate; retain existing cKDTree, partitions and sparse multiplication |
| Likely bottleneck | OSRM origin/destination requests and population-demand tables scale with selected origins × destinations (`route_demand.py`) | Measure request counts, cache reuse, chunk sizes and disconnected-island cases before parallelizing further |
| Possible future optimization | Generic checksum verification rereads complete artifacts; some pipelines read frames after verifying files | Profile bytes read and repeated validation; retain integrity guarantees and avoid unsafe trust-by-mtime shortcuts |
| Possible future optimization | Calendar `_holiday_columns` uses a dense date × holiday distance array, while direct helper uses searchsorted | Unify the correct calendar implementation first; typical ten-year datasets are small |

AIS uses lazy Polars aggregation and partitioned output; population uses spatial overlays; kernel radius indexing, cache fingerprints and batching already exist. A generic rewrite or mandatory distributed engine is not justified. Static data should be reused only when source/config/support fingerprints match.

# 13. Recommended Target Architecture

```text
Provider adapters + frozen source metadata
                  |
       normalize + validate source contracts
                  |
 population / public access / travel / calendar / activity
                  |
 versioned neutral products + validated public readers
                  |
              consumer application
                  |
 viewshed + weather + covariates --> observer-response composition
                  |
     training transforms / detection / forecast / UI ranking
```

| Targeted change | Current problem solved | Benefit | Migration risk |
| --- | --- | --- | --- |
| Small product-reader facade | Deep imports and inconsistent resolution | Stable consumer boundary without moving every file | Preserve existing import paths until consumer migration is tested |
| Separate application policy | F02/F03 model/ranking behavior inside producer | Species-neutral data and inspectable consumer assumptions | High semantic risk; freeze current formulas and artifact versions before transfer |
| Required provider validators | Silent schema/CRS drift | Fail before bad normalized artifacts are published | Moderate: old snapshots may require explicit adapters |
| Extend local generation publication | Flat-file overwrite failure | Last valid family remains readable | Moderate: readers need manifest resolution and flat-layout migration |
| Route notebook production prerequisites through package APIs | Hidden research state and duplication | Reproducible validation and origin generation | Moderate: compare frozen output values and support, not just schemas |
| Keep geometry isolated pending equivalence | Duplicate physical-viewability owners | Explicit dependency with scientific safeguards | High if formulas/support change; no immediate replacement recommended |

Keep current family directories. Do not create empty `sources/schemas/features/api` trees merely to match an idealized template. `core/artifacts` contains potentially reusable checksums/publication infrastructure, but extracting a shared MarineCast package during this review would add ownership and compatibility risk. First make local lifecycle semantics consistent.

# 14. Prioritized Action Plan

## Fix Now

| Findings | Files likely affected | Intended change and reason | Risk | Validation |
| --- | --- | --- | --- | --- |
| F01/F06 | `temporal_context/calendar/{build,download,features,holidays,validation}.py`, calendar tests | Enforce source compatibility and boundary context; unify neutral date calculations | Changed historical feature values require formula/version disclosure | Old-snapshot rejection; jurisdiction mismatch; subset invariance; year-end, leap-year and regional holidays |
| F02/F03 | Calendar scoring, land `modeling.py`/`dynamic.py`, places ranking, consumer adapters | Document/freeze legacy research outputs; separate neutral products from downstream policy; remove implicit coverage predictors | High semantic/API impact; do not delete old artifacts or move everything at once | Formula parity for compatibility path; neutral outputs independent of species/predictions; explicit consumer tests |
| F04 | Access builders and local publication helpers | Publish complete generations with a single selector update | Path migration and recovery behavior | Inject failure at each write; previous generation remains checksum-valid; concurrent writers fail safely |
| F05/F14 | Population `common/h3_grid.py`, public-shore config/build | Guarantee candidate coverage and validate metric units/spacing | Boundary allocation changes affect totals | Exact tiny/island fixtures, conservation, wrong-CRS rejection and spacing invariance |
| F07 | Boat/shore provider normalizers and adapters | Distinguish required schema failures from optional missing fields | Previously tolerated malformed inputs fail | Frozen renamed/missing fields, missing CRS and invalid coordinates |

## Fix Next

| Findings | Files likely affected | Intended change and reason | Risk | Validation |
| --- | --- | --- | --- | --- |
| F08/F09 | `utils/artifacts.py`, product readers, family manifests | Add processing/time/support identity and validated reader API | Versioned manifests and migration required | Relocation, unknown-schema rejection, changed input/config and archived read tests |
| F10 | Routing/context generation and configs | Portable tool discovery and reproducible origin selection | Native runtime parity must be demonstrated | Clean setup instructions, frozen miniature graph/origins, checksum parity |
| F11/F12 | CI, packaging tests, notebooks, docs | Automate installed-wheel acceptance; repair examples and add tiny validation notebook | Native optional checks need explicit environments | Offline CI with installed imports, `pip check`, numeric fixtures, notebook PASS/FAIL |
| F13 | Access schemas, normalizers and source notes | Accurate record-count names; explicit restrictions/vintage | Consumer column/version migration | Cross-source duplicate and unknown/closed/seasonal fixtures |

## Later

Benchmark the credible hotspots in section 12; reduce large modules only along proven seams; consolidate generic infrastructure only after multiple local use cases agree; evaluate standalone viewshed replacement with identical support and formula comparisons; expand authoritative BC access, ferry histories and directly observed use datasets. Source-rights/completeness gates remain prerequisites for production promotion, regardless of implementation priority.

# 15. Codex Implementation Backlog

Each task is a bounded follow-on session, not authorization granted by this review.

1. **Reject incompatible calendar snapshots (F01).** Add raw artifact identity, jurisdiction and padded coverage validation in calendar build. Acceptance: a 2020 snapshot cannot publish a complete 2025 calendar; ordinary frozen fixture still passes.
2. **Make calendar generation window invariant (F06).** Share holiday/long-weekend calculation between direct and snapshot APIs and crop last. Acceptance: subset values equal corresponding full-range values across weekends, year ends and observed holidays.
3. **Enforce spatial candidate coverage (F05).** Fix population grid generation for tiny/disconnected polygons without changing established allocation formulas. Acceptance: mainland plus isolated island conserves expected support and population.
4. **Validate shore metric configuration (F14).** Reject angular/non-meter length CRSs and invalid spacing; bound candidate sampling. Acceptance: exact line-length fixtures and safe behavior under supported resolutions.
5. **Harden access source boundaries (F07).** Add provider-specific required schema/CRS checks while preserving optional nulls. Acceptance: renamed required fields fail loudly; known provider fixtures remain compatible.
6. **Publish boat-launch families transactionally (F04).** Start with this one two-output family and a validated resolver. Acceptance: simulated second-write failure leaves the previous manifest and both outputs readable. Expand to other flat families only after this pattern is accepted.
7. **Remove default coverage predictor selection (F03).** Coordinate the small producer helper/consumer contract change explicitly. Acceptance: default model allowlists exclude QC/coverage; optional downstream diagnostic experiments are separately named and tested.
8. **Expose neutral calendar/access ingredients (F02).** Define versioned raw products alongside legacy effort outputs. Acceptance: consumers can obtain date flags and raw reachability without behavioral multipliers; compatibility outputs retain their documented formula.
9. **Move application-only helpers (F02/F09).** In a separately scoped consumer change, transfer shadow formatting and whale-oriented place ranking; leave neutral normalization here. Acceptance: package contains no species-name ranking or prediction fallback, consumer parity verified.
10. **Specify a minimal product-reader facade (F09).** Implement readers for calendar and one access product first. Acceptance: installed-wheel test reads a tiny checksummed manifest, validates schema/support, and rejects invalid versions.
11. **Version processing provenance (F08).** Separate acquisition/adoption/build times and config/run identity; record software revision/dependencies with backward-compatible readers. Acceptance: unchanged config with different source/code is distinguishable, and snapshot reuse retains acquisition time.
12. **Make road-only tooling portable (F10).** Configure executable/profile locations, keep runtime checks, and document origin inputs. Acceptance: missing tools fail informatively; fixture runs without a particular Homebrew prefix.
13. **Add installed-wheel CI (F11).** Build/install outside source, run imports/resource checks and offline tests, check dependency consistency; define optional native-job scope. Acceptance: intentionally omitted resources or undeclared imports fail CI.
14. **Create a deterministic validation notebook (F12).** Install, list families, build tiny calendar/access products through real APIs, show a small map/table, verify provenance/contracts, and emit real PASS/FAIL. Repair or label the broken AIS notebook separately; no live downloads required.
15. **Clarify access identity and historical semantics (F13).** Rename record counts with a migration note; add validity/knowledge-time and restricted-access states. Acceptance: duplicate physical facilities across providers are not presented as deduplicated counts, absent cells remain unknown.

Review evidence retained in temporary logs: `/tmp/human-review-pytest.log` and `/tmp/human-review-wheel.log`. These are local execution evidence, not durable product artifacts. Unrun: fresh dependency solve, Python-version matrix, full notebook execution/rendering, live provider acquisition, regional/native routing builds, source-rights verification, performance benchmarks, and end-to-end OrcaCast integration.
