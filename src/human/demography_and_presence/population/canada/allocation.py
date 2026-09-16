"""Area-weighted Canada dissemination-area population allocation to H3."""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from tqdm.auto import tqdm

from ..common.geo import geometry_union, query_intersection_indices, repair_geometries
from ..common.h3_grid import cell_to_parent_compat
from ..common.validation import validate_population_totals
from .config import CanadaPopulationConfig

LOGGER = logging.getLogger(__name__)


def allocate_canada_population_to_h3(
    cfg: CanadaPopulationConfig,
    source_geographies: gpd.GeoDataFrame,
    h3_grid: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Area-weight source population into H3 cells in parent-cell chunks."""
    required_source = {"source_geo_id", "population_2021", "geometry"}
    missing = required_source - set(source_geographies.columns)
    if missing:
        raise ValueError(f"Canada source geography is missing columns: {sorted(missing)}")
    required_h3 = {"h3", "h3_resolution", "geometry"}
    missing = required_h3 - set(h3_grid.columns)
    if missing:
        raise ValueError(f"Canada H3 grid is missing columns: {sorted(missing)}")

    population = pd.to_numeric(source_geographies["population_2021"], errors="coerce")
    if population.isna().any() or not np.isfinite(population.to_numpy()).all():
        raise ValueError("population_2021 must be finite and non-null before allocation.")
    if (population < 0).any():
        raise ValueError("population_2021 must be non-negative before allocation.")

    geographies = repair_geometries(source_geographies.to_crs(cfg.area_crs)).copy()
    h3_area = repair_geometries(h3_grid.to_crs(cfg.area_crs)).copy()
    geographies["census_geo_area_m2"] = geographies.geometry.area
    h3_area["h3_area_m2"] = h3_area.geometry.area
    geography_domain = geographies[geographies["census_geo_area_m2"] > 0].copy()
    geography_population = geography_domain[geography_domain["population_2021"] > 0].copy()
    if geography_population.empty:
        raise RuntimeError(
            "No positive-population Canada source geographies remain for allocation."
        )

    parent_resolution = cfg.runtime.allocation_chunk_parent_resolution
    h3_area["allocation_parent"] = h3_area["h3"].map(
        lambda cell: cell_to_parent_compat(str(cell), parent_resolution)
    )
    allocation_pieces: list[pd.DataFrame] = []
    h3_with_source_overlap: set[str] = set()
    parent_groups = h3_area.groupby("allocation_parent", sort=True)
    parent_count = int(h3_area["allocation_parent"].nunique())
    LOGGER.info(
        "Allocating Canada population across %s H3 parent chunks at resolution %s",
        parent_count,
        parent_resolution,
    )

    for _, h3_chunk in tqdm(
        parent_groups,
        total=parent_count,
        desc="Allocating Canada H3 parent chunks",
    ):
        chunk_geometry = geometry_union(h3_chunk)
        domain_indices = query_intersection_indices(geography_domain, chunk_geometry)
        if not domain_indices:
            continue
        geography_chunk = geography_domain.iloc[domain_indices].copy()
        geography_chunk = geography_chunk[
            geography_chunk.geometry.intersects(chunk_geometry)
        ].copy()
        if geography_chunk.empty:
            continue

        h3_domain_hits = gpd.sjoin(
            h3_chunk[["h3", "geometry"]],
            geography_chunk[["geometry"]],
            how="inner",
            predicate="intersects",
        )
        if not h3_domain_hits.empty:
            h3_with_source_overlap.update(h3_domain_hits["h3"].astype(str).unique())

        population_chunk = geography_chunk[geography_chunk["population_2021"] > 0][
            ["source_geo_id", "population_2021", "census_geo_area_m2", "geometry"]
        ].copy()
        if population_chunk.empty:
            continue
        overlaps = gpd.overlay(
            population_chunk,
            h3_chunk[["h3", "h3_resolution", "h3_area_m2", "geometry"]],
            how="intersection",
            keep_geom_type=False,
        )
        if overlaps.empty:
            continue
        overlaps = repair_geometries(overlaps)
        overlaps["overlap_area_m2"] = overlaps.geometry.area
        overlaps = overlaps[overlaps["overlap_area_m2"] > 0].copy()
        if overlaps.empty:
            continue
        overlaps["population_allocated"] = (
            overlaps["population_2021"]
            * overlaps["overlap_area_m2"]
            / overlaps["census_geo_area_m2"]
        )
        allocation_pieces.append(
            overlaps.groupby("h3", as_index=False).agg(
                population_2021=("population_allocated", "sum")
            )
        )

    if not allocation_pieces:
        raise RuntimeError("No Canada source geography/H3 overlaps were created.")
    allocated = pd.concat(allocation_pieces, ignore_index=True)
    allocated = allocated.groupby("h3", as_index=False).agg(
        population_2021=("population_2021", "sum")
    )

    if not h3_with_source_overlap:
        raise RuntimeError("No Canada H3 cells intersect candidate source geographies.")
    result = h3_area[h3_area["h3"].astype(str).isin(h3_with_source_overlap)].copy()
    LOGGER.info(
        "Kept %s H3 cells intersecting candidate DA geographies",
        len(result),
    )
    result = result.merge(allocated, on="h3", how="left", validate="one_to_one")
    result["population_2021"] = result["population_2021"].fillna(0.0)
    result["population_2021_round"] = result["population_2021"].round().astype("int64")
    result["population_density_2021_per_km2"] = result["population_2021"] / (
        result["h3_area_m2"] / 1_000_000
    )
    result["country"] = "Canada"
    result["province_name"] = cfg.source.province_name
    result["province_abbr"] = cfg.source.province_abbr
    result["province_code"] = cfg.source.province_code
    result["source_dataset"] = (
        "Statistics Canada 2021 Census Profile population; counts may use random rounding"
    )
    result["source_geography_level"] = cfg.source.geography_level
    result["allocation_method"] = "canada_census_geo_to_h3_area_weighted"
    result["crs_area"] = cfg.area_crs
    return result.to_crs(cfg.output_crs)


def source_population_in_h3_allocation_domain(
    cfg: CanadaPopulationConfig,
    source_geographies: gpd.GeoDataFrame,
    h3_grid: gpd.GeoDataFrame,
) -> float:
    """Compute source population represented by the unioned H3 allocation domain."""
    geographies = repair_geometries(source_geographies.to_crs(cfg.area_crs)).copy()
    h3_area = repair_geometries(h3_grid.to_crs(cfg.area_crs)).copy()
    geographies["census_geo_area_m2"] = geographies.geometry.area
    geographies = geographies[
        (geographies["population_2021"] > 0) & (geographies["census_geo_area_m2"] > 0)
    ].copy()
    if geographies.empty or h3_area.empty:
        return 0.0

    h3_domain_geometry = geometry_union(h3_area)
    geography_indices = query_intersection_indices(geographies, h3_domain_geometry)
    if not geography_indices:
        return 0.0
    selected = geographies.iloc[geography_indices].copy()
    selected = selected[selected.geometry.intersects(h3_domain_geometry)].copy()
    if selected.empty:
        return 0.0

    domain = gpd.GeoDataFrame(
        {"domain": ["h3_allocation"]},
        geometry=[h3_domain_geometry],
        crs=cfg.area_crs,
    )
    overlaps = gpd.overlay(
        selected[["source_geo_id", "population_2021", "census_geo_area_m2", "geometry"]],
        domain[["geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    if overlaps.empty:
        return 0.0
    overlaps = repair_geometries(overlaps)
    overlaps["overlap_area_m2"] = overlaps.geometry.area
    overlaps = overlaps[overlaps["overlap_area_m2"] > 0].copy()
    if overlaps.empty:
        return 0.0
    overlaps["population_in_domain"] = (
        overlaps["population_2021"] * overlaps["overlap_area_m2"] / overlaps["census_geo_area_m2"]
    )
    return float(overlaps["population_in_domain"].sum())


def validate_canada_allocation(
    h3_population: gpd.GeoDataFrame,
    source_population_allocation_domain: float,
    tolerance_pct: float = 0.5,
) -> dict[str, float]:
    """Validate allocated population against an independently computed source total."""
    source_total = float(source_population_allocation_domain)
    allocated_total = float(h3_population["population_2021"].sum())
    pct_difference = validate_population_totals(
        source_total=source_total,
        allocated_total=allocated_total,
        tolerance_pct=tolerance_pct,
        source_label="source population in H3 allocation domain",
        allocated_label="allocated Canada H3 population",
    )
    return {
        "source_population_allocation_domain": source_total,
        "allocated_h3_population_candidate": allocated_total,
        "allocation_pct_difference": pct_difference,
    }
