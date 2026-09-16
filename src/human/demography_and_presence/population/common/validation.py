"""Shared conservation, schema, and feature validation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from .contracts import format_distance_label
from .exceptions import PopulationValidationError
from .water_distance import METERS_PER_MILE


def validate_population_totals(
    *,
    source_total: float,
    allocated_total: float,
    tolerance_pct: float,
    source_label: str,
    allocated_label: str,
) -> float:
    """Validate conservation and return percent difference."""
    source = float(source_total)
    allocated = float(allocated_total)
    if not np.isfinite([source, allocated]).all():
        raise PopulationValidationError("Population totals must be finite.")
    if source < 0 or allocated < 0:
        raise PopulationValidationError("Population totals must be non-negative.")
    if source == 0:
        if abs(allocated) > 1e-9:
            raise PopulationValidationError(
                f"{allocated_label} is {allocated:.6f} while {source_label} is zero."
            )
        return 0.0
    pct_difference = (allocated - source) / source * 100.0
    if abs(pct_difference) > tolerance_pct:
        raise PopulationValidationError(
            f"{allocated_label} differs from {source_label} by {pct_difference:.4f}%, "
            f"exceeding tolerance {tolerance_pct:.4f}%."
        )
    return pct_difference


def validate_output_contract(
    df: pd.DataFrame,
    *,
    expected_columns: Sequence[str],
    h3_resolution: int,
    population_column: str,
    thresholds_miles: tuple[float, ...],
    cap_distance_miles: float,
    expected_h3_count: int | None = None,
    enforce_raw_max_distance_miles: float | None = None,
    allow_geometry: bool = False,
    distance_basis: str = "centroid",
) -> None:
    """Validate a flat H3 output and all generated water-distance invariants."""
    if expected_h3_count is not None and len(df) != expected_h3_count:
        raise PopulationValidationError(f"Expected {expected_h3_count} H3 rows, found {len(df)}.")
    if not allow_geometry and "geometry" in df.columns:
        raise PopulationValidationError("Standard output must not include geometry.")

    missing = [column for column in expected_columns if column not in df.columns]
    extra = [
        column for column in df.columns if column not in expected_columns and column != "geometry"
    ]
    if missing or extra:
        raise PopulationValidationError(f"Output schema mismatch. Missing={missing}, extra={extra}")
    actual_columns = [column for column in df.columns if column != "geometry"]
    if actual_columns != list(expected_columns):
        raise PopulationValidationError("Output columns are not in the required contract order.")
    if df["h3"].isna().any():
        raise PopulationValidationError("Output contains null H3 values.")
    if df["h3"].duplicated().any():
        raise PopulationValidationError("Output contains duplicate H3 values.")
    if set(df["h3_resolution"].dropna().unique()) != {h3_resolution}:
        raise PopulationValidationError(f"Output h3_resolution values must equal {h3_resolution}.")

    if df["water_distance_basis"].isna().any() or set(
        df["water_distance_basis"].astype(str).unique()
    ) != {distance_basis}:
        raise PopulationValidationError(f"water_distance_basis must equal {distance_basis!r}.")

    population = pd.to_numeric(df[population_column], errors="coerce")
    if population.isna().any() or not np.isfinite(population.to_numpy()).all():
        raise PopulationValidationError(f"{population_column} must be finite and non-null.")
    if (population < 0).any():
        raise PopulationValidationError(f"{population_column} contains negative values.")

    distance_m = pd.to_numeric(df["distance_to_water_m"], errors="coerce")
    distance_miles = pd.to_numeric(df["distance_to_water_miles"], errors="coerce")
    capped = pd.to_numeric(df["distance_to_water_capped_miles"], errors="coerce")
    for name, values in {
        "distance_to_water_m": distance_m,
        "distance_to_water_miles": distance_miles,
        "distance_to_water_capped_miles": capped,
    }.items():
        if values.isna().any() or not np.isfinite(values.to_numpy()).all() or (values < 0).any():
            raise PopulationValidationError(f"{name} must be finite, non-null, and non-negative.")
    if not np.allclose(
        distance_m.to_numpy() / METERS_PER_MILE,
        distance_miles.to_numpy(),
        rtol=1e-10,
        atol=1e-10,
    ):
        raise PopulationValidationError("Distance meters and miles columns disagree.")
    expected_capped = np.minimum(distance_miles.to_numpy(), cap_distance_miles)
    if not np.allclose(capped.to_numpy(), expected_capped, rtol=1e-10, atol=1e-10):
        raise PopulationValidationError("Capped water distance disagrees with the configured cap.")
    if (
        enforce_raw_max_distance_miles is not None
        and (distance_miles > enforce_raw_max_distance_miles + 1e-10).any()
    ):
        raise PopulationValidationError(
            "Output contains rows beyond the configured final water-distance domain."
        )

    for threshold in thresholds_miles:
        label = format_distance_label(threshold)
        within_column = f"within_{label}mi_water"
        weight_column = f"water_proximity_weight_{label}mi"
        weighted_column = f"water_weighted_population_{label}mi"

        expected_within = distance_miles.to_numpy() <= threshold
        actual_within = df[within_column].fillna(False).astype(bool).to_numpy()
        if not np.array_equal(actual_within, expected_within):
            raise PopulationValidationError(
                f"{within_column} disagrees with distance_to_water_miles."
            )
        actual_weight = pd.to_numeric(df[weight_column], errors="coerce")
        expected_weight = np.clip(1.0 - distance_miles.to_numpy() / threshold, 0.0, 1.0)
        if actual_weight.isna().any() or not np.allclose(
            actual_weight.to_numpy(), expected_weight, rtol=1e-10, atol=1e-10
        ):
            raise PopulationValidationError(
                f"{weight_column} disagrees with linear distance decay."
            )
        actual_weighted = pd.to_numeric(df[weighted_column], errors="coerce")
        expected_weighted = population.to_numpy() * expected_weight
        if actual_weighted.isna().any() or not np.allclose(
            actual_weighted.to_numpy(), expected_weighted, rtol=1e-9, atol=1e-8
        ):
            raise PopulationValidationError(
                f"{weighted_column} disagrees with population × proximity weight."
            )


def flat_frame(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """Return a geometry-free dataframe in exact contract order."""
    return pd.DataFrame(df.drop(columns=["geometry"], errors="ignore"))[list(columns)].copy()


def summarize_distance_features(
    df: pd.DataFrame,
    *,
    population_column: str,
    thresholds_miles: tuple[float, ...],
) -> dict[str, float]:
    """Summarize raw and weighted population for each water-distance threshold."""
    summary: dict[str, float] = {}
    for threshold in thresholds_miles:
        label = format_distance_label(threshold)
        summary[f"population_within_{label}mi_water"] = float(
            df.loc[df[f"within_{label}mi_water"], population_column].sum()
        )
        summary[f"weighted_population_{label}mi"] = float(
            df[f"water_weighted_population_{label}mi"].sum()
        )
    return summary
