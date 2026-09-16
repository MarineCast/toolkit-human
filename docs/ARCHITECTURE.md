# Architecture

`src/human/` owns acquisition, normalization, spatial/temporal aggregation, inspection and
publication for human-domain products. `human.core` contains the minimal infrastructure
copied from the originating application: configuration composition, geospatial helpers,
artifact checksums, dataset contracts and transactional publication. It imports no OrcaCast
modules. Shared copies do not establish a MarineCast core package.

The public entry points are `human` / `python -m human` and each family's
`download`, `build`, and `inspect` modules. Family configuration is under
`config/data/human/`; legacy geometry configuration is under `config/modeling/effort/`.
`src/human/resources/config` is the installed copy; keep both trees synchronized.

Species-specific land model ablations, shadow predictions and comparisons remain in
OrcaCast under `evaluation/sightings/land_opportunity`. Application consumers import `human`
or read documented products; there is no legacy namespace shim. The toolkit must install
and test without an OrcaCast checkout. A release-v1 reader in `utils/release_inputs.py`
consumes an externally supplied immutable observation release without importing its producer.

Legacy viewshed source was transferred intact to preserve the access/reporting calculation.
Do not silently replace it with the independent viewshed toolkit, multiply distance decay
twice, or infer parity from similarly named columns. No sibling filesystem imports are allowed.
