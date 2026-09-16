"""Explicit structural versions and separately opted-in research adapters."""

LEGACY_SCHEMAS = {"1.0.0", "2.0.0", "3.0.0-research"}
RESEARCH_ADAPTERS = {
    "4.0.0-research": "land_access_v4",
    "4.1.0-research": "source_cell_reference_v1",
}
FORMULA = "land_reporting_opportunity_v2_access_conditioned"


def validate_land_schema(manifest, *, structural_only=False, research_adapter=None):
    version = manifest.get("land_product_schema_version")
    if version is None:
        legacy_manifest_v1 = (
            "schema_version" not in manifest
            and manifest.get("manifest_schema_version") == 1
            and manifest.get("product") == "human.activity_and_effort.land_reporting_opportunity"
        )
        if manifest.get("schema_version") not in LEGACY_SCHEMAS and not legacy_manifest_v1:
            raise ValueError("Unknown legacy land schema; a tested migration is required")
        return "legacy"
    if version not in RESEARCH_ADAPTERS:
        raise ValueError(f"Unknown land schema {version!r}; a tested migration is required")
    if manifest.get("land_formula_version") != FORMULA:
        raise ValueError("Unsupported land formula identity; rebuild or use a matching adapter")
    if (
        version == "4.1.0-research"
        and manifest.get("land_semantic_version") != "source_cell_common_reference_v1"
    ):
        raise ValueError("Unsupported source-cell/reference support semantics")
    if not structural_only and research_adapter != RESEARCH_ADAPTERS[version]:
        raise ValueError(
            f"Access-conditioned land schema v4 ({version}) is research-only; explicitly select research_adapter={RESEARCH_ADAPTERS[version]!r}. This does not grant scientific model eligibility."
        )
    return version
