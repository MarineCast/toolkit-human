"""British Columbia 2021 Census population pipeline."""

from .config import (
    CanadaPathsConfig,
    CanadaPopulationConfig,
    CanadaRuntimeConfig,
    CanadaSourceConfig,
    CanadaWaterDistanceConfig,
    CanadaWaterScopeConfig,
    WaterAttributeFilter,
    WaterAttributeRule,
    load_canada_config,
)
from .pipeline import run_canada_population_pipeline, run_pipeline
from .statcan import CanadaApiError, MissingCanadaInputError

__all__ = [
    "CanadaApiError",
    "CanadaPathsConfig",
    "CanadaPopulationConfig",
    "CanadaRuntimeConfig",
    "CanadaSourceConfig",
    "CanadaWaterDistanceConfig",
    "CanadaWaterScopeConfig",
    "MissingCanadaInputError",
    "WaterAttributeFilter",
    "WaterAttributeRule",
    "load_canada_config",
    "run_canada_population_pipeline",
    "run_pipeline",
]
