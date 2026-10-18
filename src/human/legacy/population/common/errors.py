"""Compatibility aliases for the population exception hierarchy."""

from human.pipelines.population.errors import (
    PopulationConfigError,
    PopulationDataError,
    PopulationExternalServiceError,
    PopulationInputError,
    PopulationPipelineError,
    PopulationValidationError,
)

__all__ = [
    "PopulationConfigError",
    "PopulationDataError",
    "PopulationExternalServiceError",
    "PopulationInputError",
    "PopulationPipelineError",
    "PopulationValidationError",
]
