# Human package structure

The supported product is the three-Parquet dataset built by `human.get_human_data()`. The package root has only `__init__.py`, `__main__.py`, `main.py` and `cli.py`; implementations live in their owning folders.

```text
human/
  main.py                  # collect/build/validate/publish orchestration
  cli.py                   # CLI parsing, workspace initialization, preflight
  pipelines/
    population/            # census acquisition, extracted-input registration, streaming tables, allocation, water support
      static/              # query engine and spatial source contracts
    calendar/              # jurisdiction/local-civil-date lookup
    native/                # original source inventory and native features
      boat_launches/       # WA/BC/OSM source collection and facility/H3 builder
      shore_access/        # facilities and classification geometry
      places/              # destinations and park source context
      ports.py             # pinned NGA physical point inventory, R7 count and narrow migration
    delivery/              # settings, exact schemas, serialization and validation
  utils/                   # reusable downloads, source I/O, checksums, study resolution
  core/                    # typed configuration, geometry/data/artifact infrastructure
  resources/               # packaged configs, output contract and feature catalog
  legacy/                  # retained research producers, outside supported output scope
```

Pipeline-specific code remains with its pipeline: census tables/errors are in population; native classification/summary rules are in native; local-day construction is in calendar. Shared acquisition, artifact and registry helpers do not import legacy research. Active code does not depend on `human.legacy`.

The native collector keeps configuration file paths separate from Python module paths. Existing source/configured data paths, dataset/schema identifiers, numerical definitions and source pins remain unchanged by the code layout. The Canadian archive placement change is separately documented in [census sources](census-sources.md).

`human.legacy` groups the older population harmonization, ferry/activity, routing, reporting-opportunity and viewshed research implementations. These remain directly importable by their new names for tests and research; they are not extra supported outcomes of the main pipeline. Unimplemented `.gitkeep` roadmap packages were removed. Their roadmaps remain in `docs/legacy/`.

The pass-through modules from the earlier refactor were removed rather than retained as a second import tree. See [import migration](import-migration.md) for intentional internal-path changes. The public `human.get_human_data`, `human.get_population`, `human.HumanData`, `human` command and `python -m human` remain available. Source imports/configuration never require an application checkout. Data publication/cleanup and external source qualification remain in [the run guide](get-human-data.md) and [final delivery](final-delivery.md).
