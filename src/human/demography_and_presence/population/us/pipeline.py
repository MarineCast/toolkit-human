"""Thin orchestration for the US Census 2020 population pipeline."""

from __future__ import annotations

import logging

from ..common.exceptions import PopulationDataError
from ..common.h3_grid import build_h3_grid_for_boundary
from ..common.io import atomic_write_parquet, ensure_dirs
from ..common.pipeline import PipelineResult, timed_stage
from .allocation import allocate_population_to_h3
from .census import fetch_block_population
from .config import UsPopulationConfig
from .geography import (
    assign_h3_dominant_state,
    load_state_blocks,
    load_state_boundaries,
    select_h3_intersecting_water_buffer,
)
from .output import (
    final_us_frame,
    summarize_us_output,
    validate_population_allocation,
    validate_us_final_output,
)
from .water import (
    add_us_water_distance_features,
    load_water_polygons,
    prepare_target_water_polygons,
)

LOGGER = logging.getLogger(__name__)


def _join_block_population(blocks, block_population):
    if blocks["geoid20"].duplicated().any():
        raise PopulationDataError("TIGER blocks contain duplicate geoid20 values.")
    if block_population["geoid20"].duplicated().any():
        raise PopulationDataError("Census population contains duplicate geoid20 values.")
    joined = blocks.merge(
        block_population,
        on="geoid20",
        how="left",
        validate="one_to_one",
    )
    missing_mask = joined["population_2020"].isna()
    if missing_mask.any():
        examples = joined.loc[missing_mask, "geoid20"].astype(str).head(10).tolist()
        raise PopulationDataError(
            f"Missing Census population for {int(missing_mask.sum())} TIGER blocks after join. "
            f"Examples: {examples}"
        )
    return joined


def run_us_population_pipeline(
    config: UsPopulationConfig,
    overwrite: bool = False,
    debug: bool = False,
) -> PipelineResult:
    """Run the full US Census population-to-H3 water-distance pipeline."""
    ensure_dirs([config.paths.raw_dir, config.paths.processed_dir])
    overwrite_downloads = config.runtime.overwrite_downloads or overwrite
    overwrite_raw_cache = config.runtime.overwrite_raw_cache or overwrite
    overwrite_output = config.runtime.overwrite_output or overwrite
    write_debug_geo = config.runtime.write_debug_geo or debug

    if config.paths.output_parquet.exists() and not overwrite_output:
        raise FileExistsError(
            f"Output exists and overwrite is false: {config.paths.output_parquet}"
        )

    with timed_stage("Loading configured state boundaries", logger=LOGGER):
        state_boundaries = load_state_boundaries(
            config,
            overwrite_downloads=overwrite_downloads,
        )
    with timed_stage("Loading and scoping water polygons", logger=LOGGER):
        water = load_water_polygons(config)
        target_water = prepare_target_water_polygons(config, water, state_boundaries)
    with timed_stage("Loading configured-state Census blocks", logger=LOGGER):
        blocks = load_state_blocks(config, overwrite_downloads=overwrite_downloads)
    with timed_stage("Fetching and joining Census block population", logger=LOGGER):
        block_population = fetch_block_population(
            config,
            blocks,
            overwrite=overwrite_raw_cache,
        )
        blocks = _join_block_population(blocks, block_population)
    with timed_stage("Building configured-state H3 grid", logger=LOGGER):
        h3_grid = build_h3_grid_for_boundary(
            state_boundaries,
            resolution=config.h3_resolution,
            area_crs=config.area_crs,
            output_crs=config.output_crs,
        )
        h3_grid = assign_h3_dominant_state(config, h3_grid, state_boundaries)
    with timed_stage("Allocating block population to H3", logger=LOGGER):
        h3_population = allocate_population_to_h3(config, blocks, h3_grid)
        allocation_qa = validate_population_allocation(blocks, h3_population)
    with timed_stage("Adding water-distance features", logger=LOGGER):
        h3_water = add_us_water_distance_features(config, h3_population, target_water)

    expected_h3_count = len(h3_grid)
    if config.water_distance.filter_output_to_water_buffer:
        selected_h3 = select_h3_intersecting_water_buffer(
            config,
            h3_grid,
            state_boundaries,
            target_water,
        )
        selected_ids = set(selected_h3["h3"])
        h3_water = h3_water[h3_water["h3"].isin(selected_ids)].copy()
        if len(h3_water) != len(selected_h3):
            raise RuntimeError(
                "Selected H3 count mismatch after water-buffer filter: "
                f"{len(h3_water)} rows for {len(selected_h3)} selected cells."
            )
        expected_h3_count = len(selected_h3)
    else:
        LOGGER.info(
            "Keeping full configured-state US H3 output (%s cells); "
            "water-distance weights carry the coastal signal",
            expected_h3_count,
        )

    final_frame = final_us_frame(config, h3_water)
    validate_us_final_output(
        config,
        final_frame,
        expected_h3_count=expected_h3_count,
    )
    summary = summarize_us_output(config, allocation_qa, final_frame)

    output_path = atomic_write_parquet(
        final_frame,
        config.paths.output_parquet,
        overwrite=overwrite_output,
    )
    LOGGER.info("Wrote final flat Parquet: %s", output_path)

    debug_output_path = None
    if write_debug_geo:
        debug_gdf = h3_water.to_crs(config.output_crs)
        debug_output_path = atomic_write_parquet(
            debug_gdf,
            config.runtime.debug_output_path,
            overwrite=True,
        )
        LOGGER.info("Wrote debug GeoParquet: %s", debug_output_path)

    return PipelineResult(
        output_path=output_path,
        debug_output_path=debug_output_path,
        summary=summary,
    )


# Country-package convenience alias.
run_pipeline = run_us_population_pipeline
