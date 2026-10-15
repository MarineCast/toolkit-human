# Human Toolkit

<img src="docs/assets/human-toolkit-banner.png" alt="Ink panorama of a coastal harbor with roads, marina, ferries, ships, and navigation lines" width="100%">

Python distribution `toolkit-human`, import package `human`, command `human`.
Extracted from OrcaCast on 2026-09-16 with current local edits preserved. This is a
research package with offline validation, not a claim of regional or production readiness.

## Install and initialize

```bash
python -m pip install -e '.[test]'
human --workspace /path/to/human-workspace init
human families
human --workspace /path/to/human-workspace build calendar --help
python -m pytest -q
```

Python 3.11+ is declared; CI covers Python 3.11 and 3.14. Configuration and data
resolve against `HUMAN_WORKSPACE`, or the calling directory when it is unset. `init` copies
packaged configuration without replacing existing files. A wheel includes configuration,
source documentation and the road-routing Lua profile. Neither installation nor `init`
downloads source data. Review configured paths before running a producer.

Install `.[acquisition]` for provider clients. GDAL's Python bindings and native libraries
and the `gdal_viewshed` CLI are additionally needed for terrain execution and the full
test suite. Follow the [native setup and validation commands](docs/WORKFLOWS.md#native-validation-environments),
including the matching-binding preflight, before running tests.
The optional `.[meteorology]` extra uses the independent `meteorology` daylight API when
that workflow is selected. These toolkits may need installation from their own checkouts
or wheels until published; no sibling directory layout is required.

## Products and boundaries

| Area | Implemented capabilities |
| --- | --- |
| Demography and presence | US/Canada population harmonization and public places |
| Accessibility | Boat launches, public shore access, land transport, population travel time |
| Temporal context | Jurisdiction-aware holiday and calendar features |
| Activity and effort | AIS aggregates, ferry proxies, observer effort, land/water reporting opportunity |
| Legacy observation geometry | Migrated viewshed and access-kernel implementation used by existing reporting products |

`human.viewshed` preserves the originating observation-geometry implementation and tests.
It is not an alias for `viewshed_toolkit`, and numerical interchangeability has not been
established. MarineCast's standalone physical-viewability toolkit remains `toolkit-viewshed`.
AIS and ferry processing moved here with the human domain; `toolkit-ais` remains a scaffold.
Future consolidation requires an explicit contract and equivalence tests.

Human presence, access, reporting opportunity, physical viewability, vessel pressure and
species occurrence are distinct quantities. Retain unavailable, partial and observed-zero
states. Research feature availability does not establish fitness for ecological inference.

- [Architecture and ownership](docs/ARCHITECTURE.md)
- [Scientific and input contracts](docs/CONTRACTS.md)
- [Workflows and validation](docs/WORKFLOWS.md)
- [Static resident population API and neighborhood counts](docs/population.md)
- [Static H3 matrix export](docs/static-matrix.md)
- [Migration scope and evidence](docs/MIGRATION.md)
- [Executed checks and limitations](docs/VALIDATION.md)
- [Domain source notes](src/human/README.md) and [remaining research work](src/human/TODO.txt)
- [Agent and Graphify guidance](AGENTS.md)

## Static population and study exports

The standalone population API estimates residents in full H3 cells and within
25, 50, 100 and 250 km of their centers. Prepare an explicit normalized census
source manifest using the [population guide](docs/population.md), then query it:

```python
from human import get_population

result = get_population(
    bbox=(-125.8, 46.85, -121.6, 50.0), h3_res=7,
    source_manifest="/path/to/sources.json", workspace="./human-workspace",
    mode="offline", include_bands=True,
)
result.to_parquet("./population-r7-static.parquet")
```

Strict coverage is the default. Explicit `allow_partial=True` preserves incomplete
canonical totals as null and reports available-source subtotals separately. Run
`python examples/population_offline.py --workspace /tmp/synthetic-human-example`
for a complete synthetic example without provider access.

`human.study.export_study` and [examples/export_study.py](examples/export_study.py)
write static location features, a non-spatial calendar, and native spatial activity
Parquets with metadata and a catalog. The executed workspace study used the configured
2020–2030 calendar period; retained activity ends on 2026-08-31. See the
[study output contract](docs/population.md#static-and-regional-exports) and
[executed validation record](docs/VALIDATION.md#static-population-implementation-2026-10-14)
for file names, coverage limits and checks.
