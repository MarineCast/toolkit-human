"""Canonical logical-parent water support and explicit evaluated missingness."""

from __future__ import annotations

import h3
import numpy as np
import pandas as pd

WATER_SUPPORT_VERSION = "logical_h3_parent_modeled_water_area_v1"


def canonical_target_support(targets: pd.DataFrame) -> pd.DataFrame:
    """Validate a pair-independent R7 target inventory; retain zero-area children."""
    support = targets[["target_h3", "target_water_area_m2"]].copy()
    if support.target_h3.duplicated().any():
        raise ValueError("Canonical target support has duplicate children")
    area = support.target_water_area_m2
    if (~np.isfinite(area) | (area < 0)).any():
        raise ValueError("Canonical modeled water area must be finite and nonnegative")
    if any(h3.get_resolution(cell) != 7 for cell in support.target_h3):
        raise ValueError("Canonical target support requires H3 R7 children")
    support["H3_INDEX"] = support.target_h3.map(lambda cell: h3.cell_to_parent(cell, 6))
    support["MODELED_WATER_AREA_M2"] = support.groupby("H3_INDEX").target_water_area_m2.transform(
        "sum"
    )
    support["R6_WATER_AREA_WEIGHT"] = support.target_water_area_m2.div(
        support.MODELED_WATER_AREA_M2.replace(0, np.nan)
    )
    support["WATER_SUPPORT_VERSION"] = WATER_SUPPORT_VERSION
    return support.sort_values("target_h3").reset_index(drop=True)


def aggregate_supported_children(
    children: pd.DataFrame,
    support: pd.DataFrame,
    *,
    value_column: str = "OPPORTUNITY_RAW",
) -> pd.DataFrame:
    """Retain evaluated contributions without extrapolating into missing support.

    Absent rows remain unavailable. An explicitly evaluated zero is a valid
    contribution and retains its water area in both denominator and coverage.
    """
    if children.target_h3.duplicated().any():
        raise ValueError("Child opportunity must be unique by target_h3")
    if not set(children.target_h3).issubset(set(support.target_h3)):
        raise ValueError("Child opportunity lies outside canonical water support")
    joined = support.merge(
        children[["target_h3", value_column]], on="target_h3", how="left", validate="one_to_one"
    )
    joined["_contribution"] = joined[value_column] * joined.R6_WATER_AREA_WEIGHT
    joined["_available_area"] = joined.target_water_area_m2.where(joined[value_column].notna(), 0)
    result = (
        joined.groupby("H3_INDEX")
        .agg(
            **{value_column: ("_contribution", lambda values: values.sum(min_count=1))},
            MODELED_WATER_AREA_M2=("target_water_area_m2", "sum"),
            EVALUATED_WATER_AREA_M2=("_available_area", "sum"),
        )
        .reset_index()
    )
    result["EVALUATED_GEOMETRIC_SUPPORT_FRACTION"] = result.EVALUATED_WATER_AREA_M2.div(
        result.MODELED_WATER_AREA_M2.replace(0, np.nan)
    )
    result["STATE"] = np.select(
        [
            result[value_column].isna(),
            result.EVALUATED_GEOMETRIC_SUPPORT_FRACTION < 1 - 1e-12,
            result[value_column] > 0,
        ],
        ["unknown", "partial", "positive"],
        default="derived_zero",
    )
    return result
