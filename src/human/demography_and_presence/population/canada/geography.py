"""Canada water scoping, source geography, and candidate-domain operations."""

from __future__ import annotations

import logging

import geopandas as gpd
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from ..common.geo import (
    geometry_union,
    normalized_values,
    repair_geometries,
    select_intersecting,
)
from ..common.io import read_vector
from ..common.water_distance import METERS_PER_MILE
from .config import CanadaPopulationConfig, WaterAttributeFilter
from .statcan import load_canada_geography, local_or_download_path

LOGGER = logging.getLogger(__name__)


def _attribute_mask(
    frame: gpd.GeoDataFrame,
    attribute_filter: WaterAttributeFilter,
):
    if attribute_filter.column not in frame.columns:
        return None
    allowed = normalized_values(attribute_filter.values)
    return frame[attribute_filter.column].astype(str).str.strip().str.upper().isin(allowed)


def load_project_water(cfg: CanadaPopulationConfig) -> gpd.GeoDataFrame:
    """Load and scope project water polygons using ordered configuration filters."""
    water = read_vector(cfg.water_polygons_path)
    if water.crs is None:
        raise ValueError(f"Water polygons have no CRS: {cfg.water_polygons_path}")
    scoped = water.copy()

    for exclusion in cfg.water_scope.excludes:
        mask = _attribute_mask(scoped, exclusion)
        if mask is None:
            continue
        excluded_count = int(mask.sum())
        if excluded_count:
            scoped = scoped.loc[~mask].copy()
            LOGGER.info(
                "Excluded %s water feature(s) using %s in %s",
                excluded_count,
                exclusion.values,
                exclusion.column,
            )

    matched_filter: WaterAttributeFilter | None = None
    for include_filter in cfg.water_scope.filters:
        mask = _attribute_mask(scoped, include_filter)
        if mask is not None and mask.any():
            scoped = scoped.loc[mask].copy()
            matched_filter = include_filter
            break

    if matched_filter is not None:
        LOGGER.info(
            "Scoped Canada water polygons to %s feature(s) using %s in %s",
            len(scoped),
            matched_filter.values,
            matched_filter.column,
        )
    elif cfg.water_scope.allow_unfiltered:
        LOGGER.warning(
            "No Canada water-scope filter matched; using all %s non-excluded features. "
            "Add canada.water_scope to make this selection explicit.",
            len(scoped),
        )
    else:
        available = sorted(str(column) for column in scoped.columns if column != "geometry")
        raise ValueError(
            "No configured Canada water-scope filter matched the water dataset. "
            f"Available columns: {available}"
        )

    scoped = repair_geometries(scoped.to_crs(cfg.area_crs))
    if scoped.empty:
        raise RuntimeError("Canada water geometry is empty after scoping and repair.")
    return scoped


def load_bc_source_geography(
    cfg: CanadaPopulationConfig,
    *,
    overwrite_downloads: bool = False,
) -> tuple[gpd.GeoDataFrame, str]:
    """Load official British Columbia DA boundary geography."""
    geography_path = local_or_download_path(
        cfg.source.geography_path,
        cfg.source.geography_download_url,
        cfg.paths.raw_dir,
        cfg.source.geography_download_filename,
        overwrite=overwrite_downloads,
    )
    return load_canada_geography(
        geography_path,
        cfg.source.province_code,
        list(cfg.source.geography_join_column_candidates),
        cfg.area_crs,
        cfg.source.statcan_dguid_prefix,
    )


def candidate_domain(
    cfg: CanadaPopulationConfig,
    water_geometry: BaseGeometry,
    geographies: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Build a water-buffer candidate domain clipped to padded BC geography bounds."""
    candidate_m = cfg.water_distance.candidate_distance_miles * METERS_PER_MILE
    geography_area = geographies.to_crs(cfg.area_crs)
    minx, miny, maxx, maxy = geography_area.total_bounds
    bounds = box(
        minx - candidate_m,
        miny - candidate_m,
        maxx + candidate_m,
        maxy + candidate_m,
    )
    scoped_buffer = water_geometry.buffer(candidate_m).intersection(bounds)
    domain = gpd.GeoDataFrame(
        {"domain": ["bc_candidate"]},
        geometry=[scoped_buffer],
        crs=cfg.area_crs,
    )
    domain = repair_geometries(domain)
    if domain.empty:
        raise RuntimeError(
            "BC candidate domain is empty after the configured water-distance prefilter."
        )
    LOGGER.info(
        "Built BC candidate domain at %.1f miles; bounds=%s; area_km2=%.1f",
        cfg.water_distance.candidate_distance_miles,
        tuple(round(float(value), 2) for value in domain.total_bounds),
        float(domain.geometry.area.sum() / 1_000_000),
    )
    return domain


def filter_source_geographies_to_candidate(
    geographies: gpd.GeoDataFrame,
    domain: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Filter source geographies to those intersecting the candidate domain."""
    domain_geometry = geometry_union(domain.to_crs(geographies.crs))
    candidates = select_intersecting(geographies, domain_geometry)
    if candidates.empty:
        raise RuntimeError("No BC source geographies intersect the candidate domain.")
    LOGGER.info(
        "Selected %s of %s BC source geographies in the candidate domain",
        len(candidates),
        len(geographies),
    )
    return candidates


def prefilter_h3_to_water_distance(
    cfg: CanadaPopulationConfig,
    h3_grid: gpd.GeoDataFrame,
    water_geometry: BaseGeometry,
) -> gpd.GeoDataFrame:
    """Keep H3 cells within the allocation safety distance from water."""
    h3_area = h3_grid.to_crs(cfg.area_crs).copy()
    distance_miles = h3_area.geometry.centroid.distance(water_geometry) / METERS_PER_MILE
    filtered = h3_area.loc[distance_miles <= cfg.water_distance.h3_prefilter_distance_miles].copy()
    if filtered.empty:
        raise RuntimeError("No Canada H3 cells remain after water-distance prefiltering.")
    LOGGER.info(
        "Prefiltered Canada H3 grid from %s to %s cells within %.1f miles of water",
        len(h3_grid),
        len(filtered),
        cfg.water_distance.h3_prefilter_distance_miles,
    )
    return filtered.to_crs(cfg.output_crs)


def filter_h3_to_source_geographies(
    h3_grid: gpd.GeoDataFrame,
    source_geographies: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Keep H3 cells intersecting candidate source geographies."""
    h3_area = h3_grid.to_crs(source_geographies.crs).copy()
    joined = gpd.sjoin(
        h3_area[["h3", "h3_resolution", "h3_area_m2", "geometry"]],
        source_geographies[["geometry"]],
        how="inner",
        predicate="intersects",
    )
    if joined.empty:
        raise RuntimeError("No Canada H3 cells intersect candidate DA geographies.")
    filtered = joined.drop(columns=["index_right"], errors="ignore").drop_duplicates("h3").copy()
    LOGGER.info(
        "Filtered H3 grid from %s to %s cells intersecting candidate DAs",
        len(h3_grid),
        len(filtered),
    )
    return gpd.GeoDataFrame(
        filtered,
        geometry="geometry",
        crs=source_geographies.crs,
    ).to_crs(h3_grid.crs)
