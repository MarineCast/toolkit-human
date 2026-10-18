"""Exception hierarchy for expected population-pipeline failures."""

from __future__ import annotations


class PopulationPipelineError(RuntimeError):
    """Base class for expected population-pipeline failures."""


class PopulationConfigError(PopulationPipelineError):
    """Raised when configuration is invalid or internally inconsistent."""


class PopulationInputError(PopulationPipelineError):
    """Raised when a required input is missing or unusable."""


class PopulationExternalServiceError(PopulationPipelineError):
    """Raised when an external data service cannot satisfy a request."""


class PopulationDataError(PopulationPipelineError):
    """Raised when source data cannot be parsed or reconciled safely."""


class PopulationValidationError(PopulationPipelineError):
    """Raised when a pipeline invariant or output contract is violated."""
