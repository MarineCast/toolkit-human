"""Compatibility aliases for the population exception hierarchy."""

from .exceptions import (
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
