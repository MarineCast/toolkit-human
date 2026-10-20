"""Standalone static population product; legacy coastal population is separate."""
from .api import get_population, PopulationResult, PopulationCoverageError, ResourceLimits
from .sources import PopulationSources, read_sources, write_sources

__all__ = ["get_population", "PopulationResult", "PopulationCoverageError", "ResourceLimits",
           "PopulationSources", "read_sources", "write_sources"]
