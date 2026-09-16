"""US output contract, validation, and summary construction."""

from __future__ import annotations

import pandas as pd

from ..common.contracts import build_output_columns
from ..common.validation import (
    flat_frame,
    summarize_distance_features,
    validate_output_contract,
    validate_population_totals,
)
from .config import UsPopulationConfig

DEFAULT_THRESHOLDS_MILES = (25.0, 50.0, 75.0, 100.0)


def us_output_columns(thresholds_miles: tuple[float, ...]) -> list[str]:
    """Return the exact US flat-output schema for configured thresholds."""
    return build_output_columns(
        [
            "h3",
            "h3_resolution",
            "state_fips",
            "state_abbr",
            "population_2020",
            "population_2020_round",
            "population_density_2020_per_km2",
        ],
        thresholds_miles,
        ["source_dataset", "allocation_method", "crs_area"],
    )


US_DEFAULT_COLUMNS = us_output_columns(DEFAULT_THRESHOLDS_MILES)


def validate_population_allocation(
    blocks: pd.DataFrame,
    h3_population: pd.DataFrame,
    tolerance_pct: float = 0.5,
) -> dict[str, float]:
    """Validate allocated H3 population against source block population."""
    source_total = float(blocks["population_2020"].sum())
    allocated_total = float(h3_population["population_2020"].sum())
    pct_difference = validate_population_totals(
        source_total=source_total,
        allocated_total=allocated_total,
        tolerance_pct=tolerance_pct,
        source_label="source block population",
        allocated_label="allocated H3 population",
    )
    return {
        "source_block_population": source_total,
        "allocated_h3_population": allocated_total,
        "pct_difference": pct_difference,
    }


def final_us_frame(cfg: UsPopulationConfig, frame: pd.DataFrame) -> pd.DataFrame:
    """Return a geometry-free US dataframe in exact dynamic contract order."""
    return flat_frame(frame, us_output_columns(cfg.water_distance.distance_threshold_miles))


def validate_us_final_output(
    cfg: UsPopulationConfig,
    df: pd.DataFrame,
    *,
    expected_h3_count: int,
) -> None:
    """Validate the US flat output and generated water features."""
    validate_output_contract(
        df,
        expected_columns=us_output_columns(cfg.water_distance.distance_threshold_miles),
        h3_resolution=cfg.h3_resolution,
        population_column="population_2020",
        thresholds_miles=cfg.water_distance.distance_threshold_miles,
        cap_distance_miles=cfg.water_distance.max_distance_miles,
        expected_h3_count=expected_h3_count,
    )


def summarize_us_output(
    cfg: UsPopulationConfig,
    allocation_qa: dict[str, float],
    df: pd.DataFrame,
) -> dict[str, float | int | str]:
    """Build a compact US QA summary."""
    return {
        "country": "United States",
        "states": cfg.state_label,
        **allocation_qa,
        "n_h3_cells": int(len(df)),
        "n_populated_h3_cells": int((df["population_2020"] > 0).sum()),
        **summarize_distance_features(
            df,
            population_column="population_2020",
            thresholds_miles=cfg.water_distance.distance_threshold_miles,
        ),
    }


# Backwards-compatible names.
FINAL_COLUMNS = US_DEFAULT_COLUMNS
validate_final_output = validate_us_final_output
summarize_output = summarize_us_output
