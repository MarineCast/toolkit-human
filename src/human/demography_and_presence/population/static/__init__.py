"""Compatibility exports for the supported static population pipeline."""
from human.pipelines.population.static import (
    get_population, PopulationResult, PopulationCoverageError, ResourceLimits,
    PopulationSources, read_sources, write_sources,
)
__all__ = ["get_population", "PopulationResult", "PopulationCoverageError", "ResourceLimits",
           "PopulationSources", "read_sources", "write_sources"]
