# Workflows

1. Install the package and optional provider dependencies needed for your source.
2. Run `human --workspace /path/to/workspace init`; review every source and output path.
3. Provision external geometry/weather/observation inputs where required.
4. Run `human --workspace /path/to/workspace download FAMILY --help`, then the explicit
   acquisition command. Downloads may access remote services or materialize snapshots.
5. Run `human --workspace /path/to/workspace build FAMILY --help`, then build from the
   prepared inputs. Overwrite and partial-source flags remain explicit per-family choices.
6. Inspect products with `human --workspace /path/to/workspace inspect FAMILY`.

`human families` lists available families. Observer effort consumes prepared activity products
and exposes build/inspect only, with no download command. Legacy geometry remains accessible through
`python -m human.viewshed.cli.main --help`. Read the selected stage before execution: terrain
work may invoke GDAL, create large caches, publish products and clean intermediates.
Land routing may require OSRM/container tooling; installation alone does not provision it.

Run `python -m pytest -q` from this checkout after installing `.[test]`.
Tests create clearly marked synthetic geometry fixtures and remove their files afterward.
They do not validate regional source coverage, network acquisition, source rights,
OSRM/container availability, full terrain execution or application production integration.
Build a wheel with `python -m pip wheel --no-deps --no-build-isolation . -w dist` and verify
installation from outside the checkout, including `human init` and shipped resources.

Feature catalog maintenance: `python scripts/update_human_feature_catalog.py`.
Graph navigation and the exact code-only refresh command are documented in `AGENTS.md`.
