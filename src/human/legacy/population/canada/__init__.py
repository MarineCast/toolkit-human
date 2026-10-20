"""British Columbia 2021 Census population pipeline."""

from human.legacy.population.canada.config import (
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
from human.legacy.population.canada.pipeline import run_canada_population_pipeline, run_pipeline
from human.legacy.population.canada.statcan import CanadaApiError, MissingCanadaInputError

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
