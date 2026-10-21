# Workflows

Install the package in the documented Python environment, configure `config/human.yaml`, and run `human get-human-data` or `from human import get_human_data; get_human_data()`.
`human get-human-data` collects missing configured sources. `--mode offline` requires retained verified snapshots. `human validate /path/to/Data/Human` checks the three final products. See [the pipeline guide](get-human-data.md) for source acquisition, missing-input behavior, pinned calendar rules and cleanup.

Run supported pipeline checks with:

```sh
PYTHONTZPATH='' python -m pytest -q -m 'not legacy' tests/test_census_acquisition.py tests/test_extracted_census.py tests/test_run_settings.py tests/test_pipeline.py tests/test_static_delivery.py tests/test_native_features.py tests/test_ports.py tests/test_collected_population.py tests/test_static_water_support.py tests/test_calendar_lookup.py tests/test_population_collection.py tests/test_population_static.py tests/test_statcan_gaf.py tests/test_packaging.py tests/human/test_feature_catalog.py tests/human/test_structure.py
python -m pyflakes src/human/pipelines src/human/utils/downloads.py src/human/utils/study.py src/human/cli.py tests/test_census_acquisition.py tests/test_extracted_census.py tests/test_run_settings.py
python scripts/check_pipeline_docs.py
python scripts/update_human_feature_catalog.py
python -m pip wheel --no-deps --no-build-isolation . -w /path/to/small/wheel-staging
```

Validate a regular wheel outside the checkout with `scripts/check_installed_wheel.py` and the copied `tests/test_census_acquisition.py`, `tests/test_run_settings.py`, `tests/test_pipeline.py` plus its fixture modules (`test_static_delivery.py`, `test_native_features.py`) under a `tests` package. A wheel test must not inherit the checkout's PYTHONPATH.

Tests use synthetic source fixtures and fake bounded HTTP responses. They verify all three output grains, source checksums, cleanup on success/failure, publication rollback, immutable receipts, semantic rejection and preserved scientific ancestry. They do not qualify live source availability, current source rights, historical snapshot recovery or a regional reproduction.

The base dependency check excludes tests marked `legacy`; the legacy viewshed packaging import is still exercised by the full native suite.

The retained full research suite still needs the GDAL and OSRM preflights, runtimes and timezone settings in [historical native workflows](legacy-workflows.md#native-validation-environments). Run those preflights before `PYTHONTZPATH='' python -m pytest -q`; report any missing runtime rather than claiming full validation. No native terrain/routing dependency is needed for `get_human_data` from normalized census/native source inputs.

For a final full suite, use the existing documented native runtime, run both `scripts/check_native_runtime.py gdal` and `osrm`, set PYTHONTZPATH='', and point HUMAN_WORKSPACE at an isolated fixture workspace containing the shipped configs. The historical autouse fixture may create small synthetic water/land files under HUMAN_WORKSPACE; never point it at a live source deployment for validation. Native OSRM tests require a temporary loopback listener. This is separate from the supported pipeline dependency gate.

The packaged five-source census recipe is sufficient configuration for a fresh source store, but provider access and hash identity must still pass. On 2026-10-17 US HEAD checks confirmed 514,605,141 combined archive bytes; both required Canadian endpoints returned HTTP403. No clean official-census regional acquisition success is claimed. A retained-normalization uncached calculation tests the engine, not raw acquisition. The original GAF CSV remains a separate retained audit input; its optional adapter does not silently replace the pinned BC Profile source.

## GitHub merge checks

As verified on 2026-10-20, the active `main-safeguards` ruleset requires GitHub Actions
checks `pipeline (3.11)` and `pipeline (3.14)` from [the supported CI workflow](../.github/workflows/ci.yml).
Both Python jobs include the four synthetic port regressions in the checkout and installed-wheel suites.
The branch must be current with `main`. PRs and resolved review threads remain required;
deletion and force pushes remain blocked. The separate `main-merge-access` ruleset
continues to restrict who can merge.

With user approval, these two check names replaced `test (3.11)`, `test (3.14)` and
`native-osrm` in [the repository ruleset](https://github.com/MarineCast/toolkit-human/rules/24472621).
The [legacy native workflow](../.github/workflows/legacy-native.yml) remains manually
dispatched and is no longer a mandatory merge check. This is a merge-policy change,
not evidence of a new native, regional or application validation run.
