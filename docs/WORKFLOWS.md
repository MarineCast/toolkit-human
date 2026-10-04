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
regional terrain execution or application production integration. Native fixture coverage
depends on the explicitly provisioned runtimes below.
Build a wheel with `python -m pip wheel --no-deps --no-build-isolation . -w dist` and verify
installation from outside the checkout, including `human init` and shipped resources.

Feature catalog maintenance: `python scripts/update_human_feature_catalog.py`.
Graph navigation and the exact code-only refresh command are documented in `AGENTS.md`.

## Static H3 export

After building the static families, use the explicit [static matrix exporter](static-matrix.md)
to produce one H3 R7 Parquet from their native build manifests. This retains component
support and missingness; it does not aggregate time-varying products or transfer land
population to marine cells.

## Native validation environments

The full suite requires GDAL Python bindings (including NumPy support and
`ViewshedGenerate`) and the `gdal_viewshed` CLI. Installing Rasterio alone does not
provide `osgeo`. Linux CI uses Ubuntu 24.04's `gdal-bin` and `libgdal-dev`, then builds
bindings against that same native version in each Python 3.11/3.14 environment:

```bash
sudo apt-get install gdal-bin libgdal-dev
python -m pip install -e '.[test]' numpy wheel setuptools
python -m pip install --no-build-isolation "GDAL[numpy]==$(gdal-config --version).*"
python scripts/check_native_runtime.py gdal
python -m pytest -q
```

For a complete macOS terrain environment, prefer a consistent conda-forge stack
for Python, GDAL, Rasterio and PyArrow. Mixing Homebrew GDAL with PyPI PyArrow can
load two Arrow libraries and fail filesystem registration even when versions match.
The GDAL preflight checks this collision. Homebrew OSRM runs out of process and
can be used alongside that environment. The OSRM CI runner uses macOS 15 so current
Python 3.14 Rasterio wheels are available.
For example, create an isolated native environment, then install the toolkit without
replacing already-satisfied native dependencies:

```bash
conda create -n human-native -c conda-forge python=3.11 gdal=3.12.3 rasterio pyarrow pip
conda activate human-native
python -m pip install -e '.[test]'
python scripts/check_native_runtime.py gdal
python -m pytest -q
```

See [GDAL's binding installation guidance](https://gdal.org/en/stable/api/python/python_bindings.html).
The preflight prints both Rasterio's GDAL and the Python/native runtime versions;
the synthetic CLI/in-process parity test checks their interoperability.

Native road routing retains its OSRM **26.8.0** version requirement. Install that
runtime, including its `share/osrm/profiles` directory (Homebrew formula
`osrm-backend` on macOS). CI builds the checksum-verified 26.8.0 source from an
immutable Homebrew formula revision; installing the latest formula can select a
newer engine and intentionally fail the existing version guard. `HUMAN_OSRM_ROOT` selects the installation prefix;
otherwise the prefix is discovered from `osrm-extract` on `PATH`, following symlinks.
`HUMAN_OSMIUM` can select the `osmium` executable; otherwise it is found on `PATH`.
Osmium is needed for real extract inspection/merging, not the synthetic routing test.
That test builds tiny graphs and starts temporary loopback-only OSRM servers; it requires
permission to bind an ephemeral localhost port. It verifies road/bridge/tunnel reachability
and ferry/shuttle-train disconnection without live data or external routing services.
No package install downloads OSM data or builds a regional graph.

```bash
python scripts/check_native_runtime.py osrm
python -m pytest -q tests/human/accessibility/test_road_only_routing.py
```

CI requires GDAL on both Python versions and OSRM in a separate macOS job; failed
preflight checks are errors, not skips. Without OSRM, the local full suite explicitly
skips only its native profile fixture. The GDAL integration fixtures are required by
CI, including land smoke, canopy/endpoint batch invariance and CLI parity. Live source
acquisition, regional graph/terrain builds and consumer application integration remain
separate, unrun workflows. No lint/type checker is configured in this repository.

The wheel CI step uses a fresh virtual environment without system site packages,
installs the regular wheel with dependencies, and copies `scripts/check_installed_wheel.py`
and the import contract test outside the checkout before running them. It blocks
OrcaCast imports, asserts the package comes from that environment's site-packages,
and checks resources, all 35 family help routes and non-destructive workspace initialization.
These checks first run without GDAL or OSRM. The wheel environment then installs matching
GDAL bindings and runs copied native viewshed and land-smoke fixtures outside the checkout.
