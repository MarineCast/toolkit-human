# toolkit-human

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

Python 3.11+ is declared; the migration is validated on Python 3.14. Configuration and data
resolve against `HUMAN_WORKSPACE`, or the calling directory when it is unset. `init` copies
packaged configuration without replacing existing files. A wheel includes configuration,
source documentation and the road-routing Lua profile. Neither installation nor `init`
downloads source data. Review configured paths before running a producer.

Install `.[acquisition]` for provider clients. GDAL's Python bindings and native libraries
are additionally needed for terrain execution; match GDAL to your Rasterio environment.
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
- [Migration scope and evidence](docs/MIGRATION.md)
- [Executed checks and limitations](docs/VALIDATION.md)
- [Domain source notes](src/human/README.md) and [remaining research work](src/human/TODO.txt)
- [Agent and Graphify guidance](AGENTS.md)
