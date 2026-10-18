# Import migration after source cleanup

The supported public calls are unchanged:

```python
from human import HumanData, get_human_data, get_population
```

`human get-human-data`, `human preflight`, `human validate` and `python -m human` retain their behavior. The orchestrator is implemented in `human.main`; it is not another forwarding module. Native producers, calendar, population and delivery validation have one canonical implementation each. No `sys.modules` alias tree is installed.

The internal compatibility paths below are deliberately removed. Consumers that imported implementation helpers must update to the canonical module. Scientific product/schema identifiers and existing dataset paths are unchanged; do not rewrite historical metadata to match Python module names.

| Removed module | Canonical implementation |
|---|---|
| `human.collected_population` | `human.pipelines.population.collected` |
| `human.collection` | `human.pipelines.native.collection` |
| `human.demography_and_presence.population.canada.gaf` | `human.pipelines.population.gaf` |
| `human.demography_and_presence.population.static` | `human.pipelines.population.static` |
| `human.demography_and_presence.population.static.api` | `human.pipelines.population.static.api` |
| `human.demography_and_presence.population.static.census` | `human.pipelines.population.static.census` |
| `human.demography_and_presence.population.static.cli` | `human.pipelines.population.static.cli` |
| `human.demography_and_presence.population.static.collection` | `human.pipelines.population.static.collection` |
| `human.demography_and_presence.population.static.engine` | `human.pipelines.population.static.engine` |
| `human.demography_and_presence.population.static.geometry` | `human.pipelines.population.static.geometry` |
| `human.demography_and_presence.population.static.sources` | `human.pipelines.population.static.sources` |
| `human.final_delivery` | `human.pipelines.delivery.finalize` |
| `human.native_features` | `human.pipelines.native.features` |
| `human.pipeline` | `human.main` |
| `human.population_collection` | `human.pipelines.population.collection` |
| `human.shared_study` | `human.utils.study` |
| `human.static_delivery` | `human.pipelines.population.delivery` |
| `human.static_matrix` | `human.pipelines.native.summary` |
| `human.static_water_support` | `human.pipelines.population.water_support` |
| `human.temporal_context.calendar.lookup` | `human.pipelines.calendar.lookup` |
| `human.validation` | `human.pipelines.delivery.validation` |

Active native adapters moved from `human.accessibility.boat_launch_access`, `human.accessibility.public_shore_access` and `human.demography_and_presence.places` into `human.pipelines.native.boat_launches`, `human.pipelines.native.shore_access` and `human.pipelines.native.places`. The streaming StatCan Profile reader is in `human.pipelines.population.tables`; shared population error types are in `human.pipelines.population.errors`.

Historical population harmonization uses `human.legacy.population`; ferry/proxy research uses `human.legacy.activity`; routing uses `human.legacy.accessibility`; old calendar builders use `human.legacy.calendar`; terrain/vegetation research uses `human.legacy.viewshed`. The old root-level release/profile/study helpers are now `human.legacy.release`, `human.legacy.static_profile` and `human.legacy.study`. These paths preserve research implementations, not a promise of current production eligibility. The full native suite still tests them; base pipeline CI excludes only the optional legacy packaging import.

The generated feature catalog is packaged at `human/resources/feature_catalog.yaml`; read it with `importlib.resources.files('human').joinpath('resources/feature_catalog.yaml')`. Configuration and output contract resources retain their existing locations. Original source data, approved Data/Human and audit receipts are not moved by this source cleanup.
