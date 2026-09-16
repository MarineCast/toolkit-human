"""Area-weighted US Census block population allocation to H3 cells."""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from tqdm.auto import tqdm

from ..common.geo import geometry_union, query_intersection_indices, repair_geometries
from .config import UsPopulationConfig

LOGGER = logging.getLogger(__name__)


def allocate_population_to_h3(
    cfg: UsPopulationConfig,
    blocks: gpd.GeoDataFrame,
    h3_grid: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Allocate block population to intersecting H3 cells by overlap area."""
    required_block_columns = {
        "geoid20",
        "population_2020",
        "STATEFP20",
        "COUNTYFP20",
        "geometry",
    }
    missing = required_block_columns - set(blocks.columns)
    if missing:
        raise ValueError(f"Missing required block columns: {sorted(missing)}")
    required_h3_columns = {
        "h3",
        "h3_resolution",
        "state_fips",
        "state_abbr",
        "geometry",
    }
    missing = required_h3_columns - set(h3_grid.columns)
    if missing:
        raise ValueError(f"Missing required H3 columns: {sorted(missing)}")

    population = pd.to_numeric(blocks["population_2020"], errors="coerce")
    if population.isna().any() or not np.isfinite(population.to_numpy()).all():
        raise ValueError("population_2020 must be finite and non-null before allocation.")
    if (population < 0).any():
        raise ValueError("population_2020 must be non-negative before allocation.")

    blocks_area = repair_geometries(blocks.to_crs(cfg.area_crs))
    h3_area = repair_geometries(h3_grid.to_crs(cfg.area_crs))
    blocks_area["block_area_m2"] = blocks_area.geometry.area
    h3_area["h3_area_m2"] = h3_area.geometry.area

    blocks_area = blocks_area[
        (blocks_area["population_2020"] > 0) & (blocks_area["block_area_m2"] > 0)
    ].copy()
    blocks_area["STATEFP20"] = blocks_area["STATEFP20"].astype(str).str.zfill(2)
    blocks_area["COUNTYFP20"] = blocks_area["COUNTYFP20"].astype(str).str.zfill(3)

    block_columns = [
        "geoid20",
        "STATEFP20",
        "COUNTYFP20",
        "population_2020",
        "block_area_m2",
        "geometry",
    ]
    h3_columns = [
        "h3",
        "h3_resolution",
        "h3_area_m2",
        "state_fips",
        "state_abbr",
        "geometry",
    ]
    pieces: list[pd.DataFrame] = []
    county_keys = (
        blocks_area[["STATEFP20", "COUNTYFP20"]]
        .drop_duplicates()
        .sort_values(["STATEFP20", "COUNTYFP20"])
    )
    county_records = list(county_keys.itertuples(index=False, name=None))

    for state_fips, county_fips in tqdm(county_records, desc="Allocating counties"):
        county_blocks = blocks_area.loc[
            (blocks_area["STATEFP20"] == state_fips) & (blocks_area["COUNTYFP20"] == county_fips),
            block_columns,
        ].copy()
        if county_blocks.empty:
            continue
        county_geometry = geometry_union(county_blocks)
        candidate_indices = query_intersection_indices(h3_area, county_geometry)
        candidate_h3 = h3_area.iloc[candidate_indices][h3_columns].copy()
        candidate_h3 = candidate_h3[candidate_h3.geometry.intersects(county_geometry)].copy()
        if candidate_h3.empty:
            LOGGER.warning(
                "No H3 candidates for state %s county %s",
                state_fips,
                county_fips,
            )
            continue

        overlaps = gpd.overlay(
            county_blocks,
            candidate_h3,
            how="intersection",
            keep_geom_type=False,
        )
        if overlaps.empty:
            continue
        overlaps = repair_geometries(overlaps)
        overlaps["overlap_area_m2"] = overlaps.geometry.area
        overlaps = overlaps[overlaps["overlap_area_m2"] > 0].copy()
        overlaps["population_allocated"] = (
            overlaps["population_2020"] * overlaps["overlap_area_m2"] / overlaps["block_area_m2"]
        )
        pieces.append(
            overlaps.groupby("h3", as_index=False).agg(
                population_2020=("population_allocated", "sum"),
                source_blocks=("geoid20", "nunique"),
                overlap_area_m2=("overlap_area_m2", "sum"),
            )
        )

    if pieces:
        allocated = pd.concat(pieces, ignore_index=True)
        allocated = allocated.groupby("h3", as_index=False).agg(
            population_2020=("population_2020", "sum"),
            source_blocks=("source_blocks", "sum"),
            overlap_area_m2=("overlap_area_m2", "sum"),
        )
    else:
        allocated = pd.DataFrame(
            columns=["h3", "population_2020", "source_blocks", "overlap_area_m2"]
        )

    result = h3_area.merge(allocated, on="h3", how="left", validate="one_to_one")
    result["population_2020"] = result["population_2020"].fillna(0.0)
    result["source_blocks"] = result["source_blocks"].fillna(0).astype("int64")
    result["overlap_area_m2"] = result["overlap_area_m2"].fillna(0.0)
    result["population_2020_round"] = result["population_2020"].round().astype("int64")
    result["population_density_2020_per_km2"] = result["population_2020"] / (
        result["h3_area_m2"] / 1_000_000
    )
    result["source_dataset"] = "US Census 2020 Decennial PL block population"
    result["allocation_method"] = "block_to_h3_area_weighted"
    result["crs_area"] = cfg.area_crs
    return result.to_crs(cfg.output_crs)
