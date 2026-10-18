> Archived source-layout notes from before the 2026-10-18 cleanup. Historical Python paths and relative links below are retained as context; use ../ARCHITECTURE.md and ../import-migration.md for the current implementation.

# Human implementation

The supported API is `human.get_human_data`; see the repository README and docs/get-human-data.md. Canonical code is grouped under `pipelines/delivery`, `pipelines/population`, `pipelines/native` and `pipelines/calendar`; `utils` owns shared artifact/download/study helpers. Root modules are compatibility aliases. Other family modules below remain source adapters or legacy compatibility, not independently supported outputs.

# Human domain

This data-only package owns human layers and source-supported effects datasets,
including acquisition, processing, documented data derivations and export. Current
products cover population, access, calendar and activity; broader pressure/exposure
and effects layers require their own sources, implementation and validation.
Ecological response fitting, prediction and observation/reporting assumptions belong
to consuming modeling work. Feature families mirror their configuration, raw-data,
processed-data, and inspection-output paths.

## Organization

| Category | Active pipelines | Planned families |
| --- | --- | --- |
| `demography_and_presence/` | `population/`, `places/` | `seasonal_population_pressure/` |
| `temporal_context/` | `calendar/` | — |
| `activity_and_effort/` | `ferry/` (AIS belongs to toolkit-ais) | Direct survey effort, `fishing_activity/` |
| `accessibility/` | `public_shore_access/`, `boat_launch_access/`, `land_transport_access/`, `population_travel_time/` | — |
| `tourism_and_recreation/` | — | `park_visitation/`, `whale_watch_activity/`, `recreational_boating/`, `tourism_pressure/` |
| `exposure/` | — | `vessel_acoustic_exposure/` |

`viewshed/` is retained legacy compatibility. Physical-viewability production is
owned by the independent Viewshed toolkit.

## Pipeline contract

Each active family owns `download.py`, `build.py`, `inspect.py`, `config.py`,
and `DATA_SOURCES.md`. Run a stage directly, for example:

```bash
python -m human.activity_and_effort.ferry.download
python -m human.activity_and_effort.ferry.build --allow-partial
python -m human.activity_and_effort.ferry.inspect
```

Accessibility sources are intentionally heterogeneous. Washington authoritative
access records, the official legacy B.C. boat-launch inventory, and
source-labeled OSM supplements remain distinguishable in processed artifacts.
The public-shore pipeline computes waterfront fractions only from authoritative
Washington linework; OSM representative points never substitute for access
boundaries.

Land transport and population travel-time products currently adopt
checksum-pinned OSRM notebook snapshots into standardized manifests. Their
source assumptions and coverage remain explicit.

Combining human sources with weather, viewshed or reporting assumptions belongs
to modeling. The `land_reporting_opportunity/`, `water_observation_opportunity/`
and existing AIS/viewshed `observer_effort/` implementations remain importable
for legacy research only. They are excluded from active CLI families, workspace
defaults, feature collections and study exports. See
[product scope](../../docs/product-scope.md). Atmospheric visibility does not
gate completion of a Human source-data pull.

Paths mirror the source package below `config/data/human/`, `data/raw/human/`,
`data/processed/domain/human/`, and `outputs/domains/human/`. Download manifests
pin source snapshots. Build manifests pin configuration, upstream and output
checksums, schemas, coverage, attribution, licensing, completeness, and
measurement status. Inspectors verify manifests before generating HTML QA.

The valid source measurement states are `observed`, `derived`, `estimated`,
`fallback`, and `unavailable`. A missing value remains unknown unless complete
source coverage establishes a true zero.

Retained legacy schema-v2 opportunity tables use controlled component states:
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
- `human_layer_todo.txt` retains the historical cross-domain modeling roadmap;
  its proxy-combination work is outside active Human scope.

Completing a source producer does not imply modeling integration or acceptance.
