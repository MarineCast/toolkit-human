"""Human source-product scope; observation-opportunity combinations belong to modeling."""

HUMAN_ACTIVITY_FAMILIES = frozenset({"ferry"})

# These namespaces remain importable for existing research and artifact readers.
# They are excluded from active products, default workspace setup and study exports.
LEGACY_MODELING_MODULES = frozenset({
    "land_reporting_opportunity",
    "water_observation_opportunity",
    "observer_effort",
})
