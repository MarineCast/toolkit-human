"""Thin orchestration for the British Columbia Census 2021 pipeline."""

from __future__ import annotations

import logging

from ..common.geo import geometry_union
from ..common.h3_grid import build_h3_grid_for_boundary
from ..common.io import atomic_write_parquet, ensure_dirs
from ..common.pipeline import PipelineResult, timed_stage
from ..common.water_distance import add_water_distance_features
from .allocation import (
    allocate_canada_population_to_h3,
    source_population_in_h3_allocation_domain,
    validate_canada_allocation,
)
from .config import CanadaPopulationConfig
from .geography import (
    candidate_domain,
    filter_h3_to_source_geographies,
    filter_source_geographies_to_candidate,
    load_bc_source_geography,
    load_project_water,
    prefilter_h3_to_water_distance,
)
from .output import (
    final_canada_frame,
    summarize_canada_output,
    validate_canada_final_output,
)
from .statcan import MissingCanadaInputError, join_candidate_population

LOGGER = logging.getLogger(__name__)


def run_canada_population_pipeline_result(
    cfg: CanadaPopulationConfig,
    overwrite: bool = False,
    debug: bool = False,
) -> PipelineResult:
    """Run BC 2021 population allocation and return paths plus QA summary."""
    if not cfg.source.enabled:
        raise MissingCanadaInputError("Canada population pipeline is disabled in configuration.")

    ensure_dirs([cfg.paths.raw_dir, cfg.paths.processed_dir])
    overwrite_downloads = cfg.runtime.overwrite_downloads or overwrite
    overwrite_output = cfg.runtime.overwrite_output or overwrite
    write_debug_geo = cfg.runtime.write_debug_geo or debug
    if cfg.paths.output_parquet.exists() and not overwrite_output:
        raise FileExistsError(
            f"Canada output exists and overwrite is false: {cfg.paths.output_parquet}"
        )

    with timed_stage("Loading configured project water polygons", logger=LOGGER):
        water = load_project_water(cfg)
        water_geometry = geometry_union(water.to_crs(cfg.area_crs))
    with timed_stage("Loading BC Statistics Canada DA geography", logger=LOGGER):
        all_geographies, geography_join_column = load_bc_source_geography(
            cfg,
            overwrite_downloads=overwrite_downloads,
        )
        LOGGER.info("Loaded %s total BC DA geographies", len(all_geographies))
    with timed_stage("Building Canada water-buffer candidate domain", logger=LOGGER):
        domain = candidate_domain(cfg, water_geometry, all_geographies)
    with timed_stage("Filtering DA geographies to candidate domain", logger=LOGGER):
        candidate_geographies = filter_source_geographies_to_candidate(
            all_geographies,
            domain,
        )
    with timed_stage("Fetching and joining DA population", logger=LOGGER):
        candidate_geographies = join_candidate_population(
            cfg,
            candidate_geographies,
            geography_join_column,
            overwrite=overwrite,
        )
        unavailable_count = int((~candidate_geographies["population_available"]).sum())
        if unavailable_count:
            LOGGER.warning(
                "Excluding %s candidate DA geographies whose official population value is "
                "unavailable; they are not treated as zero",
                unavailable_count,
            )
            candidate_geographies = candidate_geographies[
                candidate_geographies["population_available"]
            ].copy()
        if candidate_geographies.empty:
            raise RuntimeError(
                "No Canada candidate DA geographies have an available population value."
            )
    with timed_stage("Building Canada H3 candidate grid", logger=LOGGER):
        h3_candidate = build_h3_grid_for_boundary(
            domain.to_crs(cfg.output_crs),
            resolution=cfg.h3_resolution,
            area_crs=cfg.area_crs,
            output_crs=cfg.output_crs,
            clip_to_boundary=False,
        )
        LOGGER.info("H3 candidate cells built: %s", len(h3_candidate))
    if cfg.water_distance.filter_h3_to_source_geographies:
        with timed_stage("Filtering H3 grid to candidate DAs", logger=LOGGER):
            h3_candidate = filter_h3_to_source_geographies(
                h3_candidate,
                candidate_geographies,
            )
    with timed_stage("Prefiltering H3 grid by water distance", logger=LOGGER):
        h3_allocation = prefilter_h3_to_water_distance(
            cfg,
            h3_candidate,
            water_geometry,
        )
    with timed_stage("Allocating Canada population to H3", logger=LOGGER):
        h3_population = allocate_canada_population_to_h3(
            cfg,
            candidate_geographies,
            h3_allocation,
        )
    with timed_stage(
        "Computing independent allocation-domain source population",
        logger=LOGGER,
    ):
        source_population_domain = source_population_in_h3_allocation_domain(
            cfg,
            candidate_geographies,
            h3_allocation,
        )
    allocation_qa = validate_canada_allocation(
        h3_population,
        source_population_domain,
    )
    with timed_stage("Adding Canada water-distance features", logger=LOGGER):
        h3_water = add_water_distance_features(
            h3_population,
            water_geometry=water_geometry,
            area_crs=cfg.area_crs,
            output_crs=cfg.output_crs,
            population_column="population_2021",
            thresholds_miles=cfg.water_distance.distance_threshold_miles,
            cap_distance_miles=cfg.water_distance.final_max_distance_miles,
            distance_basis=cfg.water_distance.distance_basis,
            decay=cfg.water_distance.decay,
        )

    final_frame = final_canada_frame(cfg, h3_water)
    validate_canada_final_output(cfg, final_frame)
    summary = summarize_canada_output(
        cfg,
        all_geographies,
        candidate_geographies,
        h3_allocation,
        final_frame,
        allocation_qa,
    )

    with timed_stage("Writing Canada output", logger=LOGGER):
        output_path = atomic_write_parquet(
            final_frame,
            cfg.paths.output_parquet,
            overwrite=overwrite_output,
        )
    LOGGER.info("Wrote Canada flat Parquet: %s", output_path)

    debug_output_path = None
    if write_debug_geo:
        final_h3_ids = set(final_frame["h3"])
        debug_gdf = h3_water[h3_water["h3"].isin(final_h3_ids)].to_crs(cfg.output_crs)
        debug_output_path = atomic_write_parquet(
            debug_gdf,
            cfg.runtime.debug_output_path,
            overwrite=True,
        )
        LOGGER.info("Wrote Canada debug GeoParquet: %s", debug_output_path)

    return PipelineResult(
        output_path=output_path,
        debug_output_path=debug_output_path,
        summary=summary,
    )


def run_canada_population_pipeline(
    cfg: CanadaPopulationConfig,
    overwrite: bool = False,
    debug: bool = False,
) -> PipelineResult:
    """Public country-package entrypoint returning a structured result."""
    return run_canada_population_pipeline_result(
        cfg,
        overwrite=overwrite,
        debug=debug,
    )


# Country-package convenience alias.
run_pipeline = run_canada_population_pipeline
