"""US water-polygon preparation and shared distance-feature adapter."""

from __future__ import annotations

import logging

import geopandas as gpd

from ..common.geo import geometry_union, repair_geometries, select_intersecting
from ..common.io import read_vector
from ..common.water_distance import (
    METERS_PER_MILE,
)
from ..common.water_distance import (
    add_water_distance_features as add_shared_water_distance_features,
)
from .config import UsPopulationConfig

LOGGER = logging.getLogger(__name__)


def load_water_polygons(cfg: UsPopulationConfig) -> gpd.GeoDataFrame:
    """Load configured marine water polygons in the area CRS."""
    water = read_vector(cfg.paths.water_polygons_path)
    if water.crs is None:
        raise ValueError(f"Water polygons have no CRS: {cfg.paths.water_polygons_path}")
    water = repair_geometries(water.to_crs(cfg.area_crs))
    if water.empty:
        raise ValueError(f"Water polygons are empty after repair: {cfg.paths.water_polygons_path}")
    return water


def prepare_target_water_polygons(
    cfg: UsPopulationConfig,
    water_polygons: gpd.GeoDataFrame,
    boundary_gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Limit target water polygons to the configured vicinity of the states."""
    max_distance_m = cfg.water_distance.max_distance_miles * METERS_PER_MILE
    water_area = repair_geometries(water_polygons.to_crs(cfg.area_crs))
    boundary_area = repair_geometries(boundary_gdf.to_crs(cfg.area_crs))
    boundary_buffer = geometry_union(boundary_area).buffer(max_distance_m)
    target_water = select_intersecting(water_area, boundary_buffer)
    if target_water.empty:
        raise RuntimeError("No target water polygons intersect the state water vicinity.")

    target_water = gpd.overlay(
        target_water,
        gpd.GeoDataFrame({"geometry": [boundary_buffer]}, crs=cfg.area_crs),
        how="intersection",
        keep_geom_type=False,
    )
    target_water = repair_geometries(target_water)
    if target_water.empty:
        raise RuntimeError("Target water polygons are empty after vicinity clipping.")
    LOGGER.info(
        "Scoped water polygons from %s to %s feature(s) near %s",
        len(water_area),
        len(target_water),
        cfg.state_label,
    )
    return target_water.to_crs(cfg.area_crs)


def add_us_water_distance_features(
    cfg: UsPopulationConfig,
    h3_population: gpd.GeoDataFrame,
    water_polygons: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Add shared water-distance features to US H3 population cells."""
    water_area = repair_geometries(water_polygons.to_crs(cfg.area_crs))
    return add_shared_water_distance_features(
        h3_population,
        water_geometry=geometry_union(water_area),
        area_crs=cfg.area_crs,
        output_crs=cfg.output_crs,
        population_column="population_2020",
        thresholds_miles=cfg.water_distance.distance_threshold_miles,
        cap_distance_miles=cfg.water_distance.max_distance_miles,
        distance_basis=cfg.water_distance.distance_basis,
        decay=cfg.water_distance.decay,
    )


# Backwards-compatible name used by the original root module.
add_water_distance_features = add_us_water_distance_features
