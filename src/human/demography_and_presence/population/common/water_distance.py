"""Shared water-distance and weighted-population feature construction."""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
from shapely.geometry.base import BaseGeometry

from .contracts import format_distance_label
from .geo import repair_geometries, require_crs

METERS_PER_MILE = 1609.344


def add_water_distance_features(
    h3_population: gpd.GeoDataFrame,
    *,
    water_geometry: BaseGeometry,
    area_crs: str,
    output_crs: str,
    population_column: str,
    thresholds_miles: tuple[float, ...],
    cap_distance_miles: float,
    distance_basis: str = "centroid",
    decay: str = "linear",
) -> gpd.GeoDataFrame:
    """Add centroid distance, threshold flags, linear weights, and weighted population.

    ``water_geometry`` must already be expressed in ``area_crs``.
    """
    require_crs(h3_population, "H3 population grid")
    if distance_basis != "centroid":
        raise ValueError("Only centroid water distance basis is currently supported.")
    if decay != "linear":
        raise ValueError("Only linear water-distance decay is currently supported.")
    if population_column not in h3_population.columns:
        raise ValueError(f"Missing population column: {population_column}")
    if not thresholds_miles:
        raise ValueError("At least one water-distance threshold is required.")
    if cap_distance_miles <= 0 or not math.isfinite(cap_distance_miles):
        raise ValueError("cap_distance_miles must be finite and positive.")
    if water_geometry is None or water_geometry.is_empty:
        raise ValueError("Water geometry is empty.")

    h3_area = repair_geometries(h3_population.to_crs(area_crs)).copy()
    population = np.asarray(h3_area[population_column], dtype="float64")
    if not np.isfinite(population).all() or (population < 0).any():
        raise ValueError(f"{population_column} must contain finite, non-negative values.")

    centroids = h3_area.geometry.centroid
    h3_area["distance_to_water_m"] = centroids.distance(water_geometry)
    h3_area["distance_to_water_miles"] = h3_area["distance_to_water_m"] / METERS_PER_MILE
    h3_area["distance_to_water_capped_miles"] = np.minimum(
        h3_area["distance_to_water_miles"], cap_distance_miles
    )

    for threshold in thresholds_miles:
        if threshold <= 0 or not math.isfinite(threshold):
            raise ValueError(f"Water-distance threshold must be positive, got {threshold!r}.")
        label = format_distance_label(threshold)
        within_column = f"within_{label}mi_water"
        weight_column = f"water_proximity_weight_{label}mi"
        weighted_column = f"water_weighted_population_{label}mi"
        h3_area[within_column] = h3_area["distance_to_water_miles"] <= threshold
        h3_area[weight_column] = np.clip(
            1.0 - h3_area["distance_to_water_miles"] / threshold,
            0.0,
            1.0,
        )
        h3_area[weighted_column] = h3_area[population_column] * h3_area[weight_column]

    h3_area["water_distance_basis"] = distance_basis
    return h3_area.to_crs(output_crs)
