"""Distance decay weights for viewshed source-target pairs."""

from human.legacy.viewshed.config.distance import (
    DistanceRuntime,
    DistanceWeightConfig,
    load_distance_runtime,
    load_distance_weight_config,
    resolved_max_distance_km,
)
from human.legacy.viewshed.weights.distance.cli import main
from human.legacy.viewshed.weights.distance.compute import distance_weight_values

__all__ = [
    "DistanceRuntime",
    "DistanceWeightConfig",
    "distance_weight_values",
    "load_distance_runtime",
    "load_distance_weight_config",
    "main",
    "resolved_max_distance_km",
]
