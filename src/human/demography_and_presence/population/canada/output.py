"""Canada output contract, validation, and summary construction."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from ..common.contracts import build_output_columns, format_distance_label
from ..common.validation import (
    flat_frame,
    summarize_distance_features,
    validate_output_contract,
)
from .config import CanadaPopulationConfig

DEFAULT_THRESHOLDS_MILES = (25.0, 50.0, 75.0, 100.0)


def canada_output_columns(thresholds_miles: tuple[float, ...]) -> list[str]:
    """Return the exact Canada flat-output schema for configured thresholds."""
    return build_output_columns(
        [
            "h3",
            "h3_resolution",
            "country",
            "province_name",
            "province_abbr",
            "province_code",
            "population_2021",
            "population_2021_round",
            "population_density_2021_per_km2",
        ],
        thresholds_miles,
        [
            "source_dataset",
            "source_geography_level",
            "allocation_method",
            "crs_area",
        ],
    )


CANADA_DEFAULT_COLUMNS = canada_output_columns(DEFAULT_THRESHOLDS_MILES)


def final_canada_frame(
    cfg: CanadaPopulationConfig,
    h3_water: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Filter final rows to the configured maximum distance and flatten them."""
    final = h3_water[
        h3_water["distance_to_water_miles"] <= cfg.water_distance.final_max_distance_miles
    ].copy()
    if final.empty:
        raise RuntimeError(
            "No Canada H3 cells remain within the configured final water distance "
            f"({cfg.water_distance.final_max_distance_miles:g} miles)."
        )
    return flat_frame(
        final,
        canada_output_columns(cfg.water_distance.distance_threshold_miles),
    )


def validate_canada_final_output(
    cfg: CanadaPopulationConfig,
    df: pd.DataFrame,
) -> None:
    """Validate the Canada flat output and generated water features."""
    validate_output_contract(
        df,
        expected_columns=canada_output_columns(cfg.water_distance.distance_threshold_miles),
        h3_resolution=cfg.h3_resolution,
        population_column="population_2021",
        thresholds_miles=cfg.water_distance.distance_threshold_miles,
        cap_distance_miles=cfg.water_distance.final_max_distance_miles,
        enforce_raw_max_distance_miles=cfg.water_distance.final_max_distance_miles,
    )


def summarize_canada_output(
    cfg: CanadaPopulationConfig,
    all_geographies: gpd.GeoDataFrame,
    candidate_geographies: gpd.GeoDataFrame,
    h3_candidate: gpd.GeoDataFrame,
    final_df: pd.DataFrame,
    allocation_qa: dict[str, float],
) -> dict[str, float | int | str]:
    """Build a Canada QA summary using distance-aware key names."""
    candidate_label = format_distance_label(cfg.water_distance.candidate_distance_miles)
    final_label = format_distance_label(cfg.water_distance.final_max_distance_miles)
    return {
        "country": "Canada",
        "province": cfg.source.province_name,
        "source_dataset": "Statistics Canada 2021 Census Profile",
        "source_geography_level": cfg.source.geography_level,
        "candidate_distance_miles": cfg.water_distance.candidate_distance_miles,
        "final_max_distance_miles": cfg.water_distance.final_max_distance_miles,
        "n_source_geographies_total_bc": int(len(all_geographies)),
        f"n_source_geographies_candidate_{candidate_label}mi": int(len(candidate_geographies)),
        f"source_population_candidate_{candidate_label}mi": float(
            candidate_geographies["population_2021"].sum()
        ),
        "n_h3_candidate_cells": int(len(h3_candidate)),
        f"n_h3_final_{final_label}mi_cells": int(len(final_df)),
        f"population_final_{final_label}mi": float(final_df["population_2021"].sum()),
        **allocation_qa,
        **summarize_distance_features(
            final_df,
            population_column="population_2021",
            thresholds_miles=cfg.water_distance.distance_threshold_miles,
        ),
    }


# Backwards-compatible constant name.
CANADA_COLUMNS = CANADA_DEFAULT_COLUMNS
