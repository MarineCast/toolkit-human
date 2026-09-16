"""US TIGER geography loading, domain construction, and H3 assignment."""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

from ..common.geo import geometry_union, repair_geometries, select_intersecting
from ..common.io import download_file, read_zipped_vector
from ..common.water_distance import METERS_PER_MILE
from .config import UsPopulationConfig

LOGGER = logging.getLogger(__name__)


def load_state_boundaries(
    cfg: UsPopulationConfig,
    overwrite_downloads: bool = False,
) -> gpd.GeoDataFrame:
    """Download/load TIGER states and return configured state boundaries."""
    download_file(cfg.tiger.states_url, cfg.paths.states_zip, overwrite=overwrite_downloads)
    states = read_zipped_vector(cfg.paths.states_zip)
    if "STATEFP" not in states.columns:
        raise ValueError(f"Expected STATEFP in TIGER states. Columns: {list(states.columns)}")
    states["state_fips"] = states["STATEFP"].astype(str).str.zfill(2)
    selected = states[states["state_fips"].isin(cfg.state_fips)].copy()
    if selected.empty:
        raise ValueError(
            f"Could not find configured state FIPS values in TIGER states: {cfg.state_fips}"
        )
    selected["state_abbr"] = selected["state_fips"].map(cfg.state_abbr_by_fips)
    missing = set(cfg.state_fips) - set(selected["state_fips"])
    if missing:
        raise ValueError(
            f"Could not find configured state FIPS values in TIGER states: {sorted(missing)}"
        )
    return repair_geometries(selected.to_crs(cfg.output_crs))


def load_state_blocks(
    cfg: UsPopulationConfig,
    overwrite_downloads: bool = False,
) -> gpd.GeoDataFrame:
    """Download/load TIGER Census 2020 blocks for configured states."""
    pieces: list[gpd.GeoDataFrame] = []
    required = {"GEOID20", "STATEFP20", "COUNTYFP20", "TRACTCE20"}
    for state_fips in cfg.state_fips:
        zip_path = cfg.paths.blocks_zip(state_fips)
        download_file(
            cfg.tiger.blocks_urls[state_fips],
            zip_path,
            overwrite=overwrite_downloads,
        )
        blocks = read_zipped_vector(zip_path)
        missing = required - set(blocks.columns)
        if missing:
            raise ValueError(
                f"TIGER blocks for {state_fips} missing expected columns: {sorted(missing)}"
            )
        blocks = repair_geometries(blocks.to_crs(cfg.output_crs))
        blocks["geoid20"] = blocks["GEOID20"].astype(str)
        blocks["state_fips"] = blocks["STATEFP20"].astype(str).str.zfill(2)
        blocks["state_abbr"] = blocks["state_fips"].map(cfg.state_abbr_by_fips)
        pieces.append(blocks)

    combined = gpd.GeoDataFrame(pd.concat(pieces, ignore_index=True), crs=cfg.output_crs)
    if combined["geoid20"].duplicated().any():
        duplicate_count = int(combined["geoid20"].duplicated().sum())
        raise RuntimeError(f"TIGER block input contains {duplicate_count} duplicate GEOIDs.")
    return combined


def build_population_domain(
    cfg: UsPopulationConfig,
    state_boundaries: gpd.GeoDataFrame,
    water_polygons: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Build the configured-state domain within the maximum distance of target water."""
    max_distance_m = cfg.water_distance.max_distance_miles * METERS_PER_MILE
    water_area = repair_geometries(water_polygons.to_crs(cfg.area_crs))
    boundary_area = repair_geometries(state_boundaries.to_crs(cfg.area_crs))
    water_buffer = gpd.GeoDataFrame(
        {"domain": ["water_buffer"]},
        geometry=[geometry_union(water_area).buffer(max_distance_m)],
        crs=cfg.area_crs,
    )
    domain = gpd.overlay(
        boundary_area[["geometry"]],
        water_buffer[["geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    domain = repair_geometries(domain)
    if domain.empty:
        raise RuntimeError("The water-distance population domain is empty.")
    domain = domain.dissolve()
    domain_area_m2 = float(domain.geometry.area.sum())
    boundary_area_m2 = float(boundary_area.geometry.area.sum())
    domain_pct = domain_area_m2 / boundary_area_m2 * 100 if boundary_area_m2 else 0.0
    LOGGER.info(
        "Built population domain within %.1f miles of water covering %.2f%% of %s",
        cfg.water_distance.max_distance_miles,
        domain_pct,
        cfg.state_label,
    )
    if domain_pct >= 95:
        LOGGER.warning(
            "Water-bounded domain covers nearly all configured states (%.2f%%). "
            "Check that the water source contains only intended marine geometry.",
            domain_pct,
        )
    return domain.to_crs(cfg.output_crs)


def filter_blocks_to_domain(
    blocks: gpd.GeoDataFrame,
    domain: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Keep only blocks intersecting a population domain."""
    domain_union = geometry_union(domain.to_crs(blocks.crs))
    filtered = select_intersecting(blocks, domain_union)
    LOGGER.info(
        "Filtered Census blocks from %s to %s using water domain",
        len(blocks),
        len(filtered),
    )
    return filtered


def clip_blocks_to_domain(
    cfg: UsPopulationConfig,
    blocks: gpd.GeoDataFrame,
    domain: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Clip blocks to a domain and area-weight each block's population."""
    required = {"geoid20", "population_2020", "COUNTYFP20", "geometry"}
    missing = required - set(blocks.columns)
    if missing:
        raise ValueError(
            f"Blocks missing required columns before domain clipping: {sorted(missing)}"
        )

    blocks_area = repair_geometries(blocks.to_crs(cfg.area_crs))
    domain_area = repair_geometries(domain.to_crs(cfg.area_crs))
    blocks_area["original_block_area_m2"] = blocks_area.geometry.area
    blocks_area = blocks_area[blocks_area["original_block_area_m2"] > 0].copy()
    clipped = gpd.overlay(
        blocks_area,
        domain_area[["geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    clipped = repair_geometries(clipped)
    clipped["domain_block_area_m2"] = clipped.geometry.area
    clipped = clipped[clipped["domain_block_area_m2"] > 0].copy()
    clipped["population_2020"] = (
        clipped["population_2020"]
        * clipped["domain_block_area_m2"]
        / clipped["original_block_area_m2"]
    )
    LOGGER.info(
        "Clipped %s intersecting blocks to %s domain block parts",
        len(blocks),
        len(clipped),
    )
    return clipped.to_crs(cfg.output_crs)


def filter_h3_to_water_distance_domain(
    cfg: UsPopulationConfig,
    h3_water: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Keep H3 cells whose centroid distance is within the configured maximum."""
    before = len(h3_water)
    filtered = h3_water[
        h3_water["distance_to_water_miles"] <= cfg.water_distance.max_distance_miles
    ].copy()
    LOGGER.info(
        "Filtered H3 cells from %s to %s within %.1f miles of water",
        before,
        len(filtered),
        cfg.water_distance.max_distance_miles,
    )
    if filtered.empty:
        raise RuntimeError("No H3 cells remain after applying the water-distance filter.")
    return filtered


def select_h3_intersecting_water_buffer(
    cfg: UsPopulationConfig,
    h3_gdf: gpd.GeoDataFrame,
    state_boundaries: gpd.GeoDataFrame,
    water_polygons: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Select state H3 cells whose clipped geometry intersects the water buffer."""
    water_domain = build_population_domain(cfg, state_boundaries, water_polygons)
    h3_area = repair_geometries(h3_gdf.to_crs(cfg.area_crs))
    domain_geometry = geometry_union(water_domain.to_crs(cfg.area_crs))
    selected = select_intersecting(h3_area, domain_geometry)
    if selected.empty:
        raise RuntimeError("No H3 cells intersect the water buffer in configured states.")
    LOGGER.info(
        "Selected %s of %s state H3 cells intersecting the %.1f-mile water buffer",
        len(selected),
        len(h3_gdf),
        cfg.water_distance.max_distance_miles,
    )
    return selected.to_crs(cfg.output_crs)


def assign_h3_dominant_state(
    cfg: UsPopulationConfig,
    h3_grid: gpd.GeoDataFrame,
    state_boundaries: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Assign each H3 cell to the state with the largest clipped overlap area."""
    h3_area = repair_geometries(h3_grid.to_crs(cfg.area_crs))
    states_area = repair_geometries(
        state_boundaries[["state_fips", "state_abbr", "geometry"]].to_crs(cfg.area_crs)
    )
    overlaps = gpd.overlay(
        h3_area[["h3", "geometry"]],
        states_area,
        how="intersection",
        keep_geom_type=False,
    )
    overlaps = repair_geometries(overlaps)
    if overlaps.empty:
        raise RuntimeError("No H3/state overlaps were created.")
    overlaps["state_overlap_area_m2"] = overlaps.geometry.area
    dominant = overlaps.sort_values(
        ["h3", "state_overlap_area_m2"], ascending=[True, False]
    ).drop_duplicates("h3")[["h3", "state_fips", "state_abbr"]]
    result = h3_grid.drop(columns=["state_fips", "state_abbr"], errors="ignore").merge(
        dominant,
        on="h3",
        how="left",
        validate="one_to_one",
    )
    if result["state_fips"].isna().any():
        missing_count = int(result["state_fips"].isna().sum())
        raise RuntimeError(f"Could not assign dominant state to {missing_count} H3 cells.")
    return gpd.GeoDataFrame(result, geometry="geometry", crs=h3_grid.crs)
