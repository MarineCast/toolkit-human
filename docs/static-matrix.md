# Static Human H3 matrix

`human export-static-matrix --manifest BUILD_MANIFEST --manifest BUILD_MANIFEST --output MATRIX.parquet`
exports the native population context, boat-launch access, public-shore access and places
catalog into one union of H3 resolution 7 cells. Inputs are explicit native build manifests;
selected artifact checksums are verified. Optional `--input-manifest` embeds a
checksum-verified inventory of retained/acquired raw sources and external geometry. Repeated components, duplicate cells, invalid
identity and incompatible resolutions are rejected. Existing output is never overwritten.

Variables retain component prefixes and native values. Missing component support remains
null. Population context retains U.S. 2020 and Canadian 2021 components and mixed-vintage
QC; its total is not a contemporaneous estimate. No marine transfer is applied. Access
sources are partial and occupied-only outputs cannot establish zero outside their support.
Places are point catalog record counts per cell; duplicates across providers may remain.
App destination ranking scores are excluded. Temporal activity, routing, reporting and
legacy viewshed products are outside this static contract.

The embedded `human_static_matrix` metadata retains native manifests, source rights,
attribution, source completeness, input checksums, config identity and limitations.
Counts use native source records; population uses allocated persons; native shoreline
lengths/ratios retain their source units. Other fields are labels, QC and availability;
consult the embedded native schemas and family DATA_SOURCES documents for exact units.
No new units, imputation or access/presence equivalence is introduced. All exports remain
research products and model-ineligible by default. Local export does not authorize remote
redistribution of source data.

Validation: `PYTHONPATH=src python -m pytest tests/test_static_matrix.py -q`.
