# Human domain

This package owns non-viewshed human presence, access, activity, effort, and
exposure products. Feature families mirror their configuration, raw-data,
processed-data, and inspection-output paths.

## Organization

| Category | Active pipelines | Planned families |
| --- | --- | --- |
| `demography_and_presence/` | `population/`, `places/` | `seasonal_population_pressure/` |
| `temporal_context/` | `calendar/` | — |
| `activity_and_effort/` | `ais/`, `ferry/`, `land_reporting_opportunity/`, `water_observation_opportunity/` | `observer_effort/`, `fishing_activity/` |
| `accessibility/` | `public_shore_access/`, `boat_launch_access/`, `land_transport_access/`, `population_travel_time/` | — |
| `tourism_and_recreation/` | — | `park_visitation/`, `whale_watch_activity/`, `recreational_boating/`, `tourism_pressure/` |
| `exposure/` | — | `vessel_acoustic_exposure/` |

`viewshed/` remains an independent bounded context and is not part of this
migration.

## Pipeline contract

Each active family owns `download.py`, `build.py`, `inspect.py`, `config.py`,
and `DATA_SOURCES.md`. Run a stage directly, for example:

```bash
python -m human.activity_and_effort.ais.download
python -m human.activity_and_effort.ais.build --allow-partial
python -m human.activity_and_effort.ais.inspect
```

Accessibility sources are intentionally heterogeneous. Washington authoritative
access records, the official legacy B.C. boat-launch inventory, and
source-labeled OSM supplements remain distinguishable in processed artifacts.
The public-shore pipeline computes waterfront fractions only from authoritative
Washington linework; OSM representative points never substitute for access
boundaries.

Land transport and population travel-time products currently adopt
checksum-pinned OSRM notebook snapshots into standardized manifests.
`land_reporting_opportunity/` combines the static land viewshed, viewing
conditions, reachable population/travel, road and city access, public-shore
evidence, and calendar opportunity into daily and weekly research composites.
Every component is also published standalone. Verified-only and mapped-public
composites are explicit lower bounds; unknown access is never zero-filled.
These products remain research-only until routing, access coverage, response
curves, stability, and leakage checks pass.

`water_observation_opportunity/` publishes class-separated AIS and ferry
activity plus daily/weekly viewing-condition-weighted target components. Ferry
is propagated at native R7 and stays separate from passenger AIS. Whale-watch,
sea state, and reporting capture remain explicitly unavailable. No canonical
water composite is defined; the older passenger-plus-recreational AIS artifact
under `observer_effort/` is compatibility/sensitivity only. See
`docs/architecture/observation_opportunity.md` for the complete contract.

Paths mirror the source package below `config/data/human/`, `data/raw/human/`,
`data/processed/domain/human/`, and `outputs/domains/human/`. Download manifests
pin source snapshots. Build manifests pin configuration, upstream and output
checksums, schemas, coverage, attribution, licensing, completeness, and
measurement status. Inspectors verify manifests before generating HTML QA.

The valid source measurement states are `observed`, `derived`, `estimated`,
`fallback`, and `unavailable`. A missing value remains unknown unless complete
source coverage establishes a true zero.

Schema-v2 opportunity tables additionally use controlled component states:
`positive`, `observed_zero`, `derived_zero`, `unknown`, `partial`, `unmapped`,
`source_unavailable`, `outside_jurisdiction`, `not_applicable`, and
`processing_failure`.

## Ownership boundaries

- Places is an application-data product, not a model feature family.
- Static ports, marinas, piers, ferry terminals, and overwater structures stay
  in the environmental seascape anthropogenic family.
- Regulations, closures, and vessel-management zones are outside this repository's scope.
- Population remains a land-based R7 pressure source until accessibility owns
  a defensible population-to-water transfer mechanism.
- Survey, AIS, ferry, calendar, tourism, and acoustic evidence remain separate;
  this domain does not publish opaque observer-effort or disturbance indices.

Regenerate or verify `feature_catalog.yaml` after a producer-schema change:

```bash
python scripts/update_human_feature_catalog.py
python scripts/update_human_feature_catalog.py --check
```

## Roadmaps

- `TODO.txt` tracks feature-family implementation inside the human domain.
- `human_layer_todo.txt` tracks cross-domain integration, evidence, and release
  gates for the broader human layer.

The files are intentionally separate: completing a producer does not imply that
its integration or release-readiness gates have passed.
