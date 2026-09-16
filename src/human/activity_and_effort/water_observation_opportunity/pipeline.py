"""Numerical helpers for component-first dynamic water opportunity products."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import h3
import numpy as np
import pandas as pd
import polars as pl
from scipy import sparse

from human.activity_and_effort.observation_opportunity_contract import (
    ComponentState,
)

EARTH_MEAN_RADIUS_KM = 6371.0088

AIS_ACTIVITY_COLUMNS = {
    "ALL_VESSEL_AIS_ACTIVITY_HOURS_PROXY": "ACTIVE_VESSEL_HOURS_PROXY",
    "PASSENGER_AIS_ACTIVITY_HOURS_PROXY": "PASSENGER_VESSEL_HOURS_PROXY",
    "RECREATIONAL_AIS_ACTIVITY_HOURS_PROXY": "RECREATIONAL_VESSEL_HOURS_PROXY",
    "FISHING_AIS_ACTIVITY_HOURS_PROXY": "FISHING_VESSEL_HOURS_PROXY",
}
COMMERCIAL_INPUT_COLUMNS = (
    "TOWING_VESSEL_HOURS_PROXY",
    "CARGO_VESSEL_HOURS_PROXY",
    "TANKER_VESSEL_HOURS_PROXY",
)

AIS_TARGET_COLUMNS = {
    "ALL_VESSEL_AIS_ACTIVITY_HOURS_PROXY": ("WATER_ALL_VESSEL_AIS_OBSERVATION_OPPORTUNITY_RAW"),
    "PASSENGER_AIS_ACTIVITY_HOURS_PROXY": ("WATER_PASSENGER_AIS_OBSERVATION_OPPORTUNITY_RAW"),
    "RECREATIONAL_AIS_ACTIVITY_HOURS_PROXY": ("WATER_RECREATIONAL_AIS_OBSERVATION_OPPORTUNITY_RAW"),
    "COMMERCIAL_AIS_ACTIVITY_HOURS_PROXY": ("WATER_COMMERCIAL_AIS_OBSERVATION_OPPORTUNITY_RAW"),
    "FISHING_AIS_ACTIVITY_HOURS_PROXY": ("WATER_FISHING_AIS_OBSERVATION_OPPORTUNITY_RAW"),
}

FERRY_TARGET_COLUMNS = {
    "FERRY_RIDER_HOURS": "WATER_FERRY_RIDER_OBSERVATION_OPPORTUNITY_RAW",
    "FERRY_PLATFORM_HOURS": "WATER_FERRY_PLATFORM_OBSERVATION_OPPORTUNITY_RAW",
}


@dataclass(frozen=True)
class KernelBasis:
    source_order: pd.Index
    target_order: pd.Index
    distance_km: np.ndarray
    matrices: tuple[sparse.csr_matrix, ...]
    static_target_support: np.ndarray


def stable_sigmoid(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype="float64")
    return 1.0 / (1.0 + np.exp(-np.clip(values, -60.0, 60.0)))


def haversine_km(source_cells: list[str], target_cells: list[str]) -> np.ndarray:
    if len(source_cells) != len(target_cells):
        raise ValueError("Source and target distance vectors must have equal length.")
    source = np.radians(np.asarray([h3.cell_to_latlng(cell) for cell in source_cells]))
    target = np.radians(np.asarray([h3.cell_to_latlng(cell) for cell in target_cells]))
    latitude_delta = target[:, 0] - source[:, 0]
    longitude_delta = target[:, 1] - source[:, 1]
    haversine = (
        np.sin(latitude_delta / 2.0) ** 2
        + np.cos(source[:, 0]) * np.cos(target[:, 0]) * np.sin(longitude_delta / 2.0) ** 2
    )
    return 2.0 * EARTH_MEAN_RADIUS_KM * np.arcsin(np.sqrt(np.clip(haversine, 0, 1)))


def add_distance_bins(
    kernel: pl.DataFrame,
    *,
    source_column: str,
    target_column: str,
    distance_bin_km: float,
) -> pl.DataFrame:
    distances = haversine_km(
        kernel.get_column(source_column).cast(pl.String).to_list(),
        kernel.get_column(target_column).cast(pl.String).to_list(),
    )
    bins = np.rint(distances / distance_bin_km).astype("int16")
    return kernel.with_columns(
        pl.Series("DISTANCE_KM", distances),
        pl.Series("DISTANCE_BIN_INDEX", bins),
    )


def kernel_basis(
    kernel: pl.DataFrame,
    *,
    source_column: str,
    target_column: str,
    weight_column: str,
    distance_bin_km: float,
) -> KernelBasis:
    required = {source_column, target_column, weight_column, "DISTANCE_BIN_INDEX"}
    missing = sorted(required.difference(kernel.columns))
    if missing:
        raise ValueError(f"Kernel is missing columns: {missing}")
    source_order = pd.Index(sorted(kernel.get_column(source_column).unique().to_list()))
    target_order = pd.Index(sorted(kernel.get_column(target_column).unique().to_list()))
    source_codes = pd.Categorical(
        kernel.get_column(source_column).to_list(), categories=source_order
    ).codes
    target_codes = pd.Categorical(
        kernel.get_column(target_column).to_list(), categories=target_order
    ).codes
    weights = kernel.get_column(weight_column).cast(pl.Float64).to_numpy()
    bin_codes = kernel.get_column("DISTANCE_BIN_INDEX").to_numpy()
    maximum_bin = int(bin_codes.max()) if len(bin_codes) else -1
    matrices = []
    for distance_bin in range(maximum_bin + 1):
        selected = bin_codes == distance_bin
        matrix = sparse.csr_matrix(
            (weights[selected], (source_codes[selected], target_codes[selected])),
            shape=(len(source_order), len(target_order)),
        )
        matrix.eliminate_zeros()
        matrices.append(matrix)
    full = sum(matrices, sparse.csr_matrix((len(source_order), len(target_order))))
    static_target_support = np.asarray(full.sum(axis=0)).ravel()
    return KernelBasis(
        source_order=source_order,
        target_order=target_order,
        distance_km=np.arange(maximum_bin + 1, dtype="float64") * distance_bin_km,
        matrices=tuple(matrices),
        static_target_support=static_target_support,
    )


def area_weighted_parent_kernel(
    static: pl.DataFrame, support: pd.DataFrame, *, distance_bin_km: float, aggregate_sources: bool
) -> pl.DataFrame:
    """Bin fine-pair distances before averaging over canonical target water area.

    Coarse source totals use an explicit uniform allocation over represented
    source children. Unmodeled target children remain in the area denominator.
    """
    from human.activity_and_effort.land_reporting_opportunity.support import (
        canonical_target_support,
    )

    area = canonical_target_support(support)
    fine = add_distance_bins(
        static,
        source_column="source_h3",
        target_column="target_h3",
        distance_bin_km=distance_bin_km,
    ).to_pandas()
    fine = fine.merge(
        area[["target_h3", "H3_INDEX", "R6_WATER_AREA_WEIGHT"]],
        on="target_h3",
        how="left",
        validate="many_to_one",
    )
    if fine.R6_WATER_AREA_WEIGHT.isna().any():
        raise ValueError("Static water targets require positive canonical parent water support")
    fine["source_parent"] = fine.source_h3.map(lambda c: h3.cell_to_parent(c, 6))
    counts = fine[["source_h3", "source_parent"]].drop_duplicates().groupby("source_parent").size()
    fine["weight"] = fine.weight_static_viewability * fine.R6_WATER_AREA_WEIGHT
    if aggregate_sources:
        fine["weight"] /= fine.source_parent.map(counts)
        fine["source_h3"] = fine.source_parent
    return pl.from_pandas(
        fine.groupby(["source_h3", "H3_INDEX", "DISTANCE_BIN_INDEX"], as_index=False).weight.sum()
    )


def static_distance_summaries(
    basis: KernelBasis,
    *,
    distance_detection_weights: np.ndarray,
) -> dict[str, np.ndarray]:
    """Summarize distance diagnostics without applying them to the kernel again."""

    detection = np.asarray(distance_detection_weights, dtype="float64")
    if detection.shape != basis.distance_km.shape:
        raise ValueError("Distance-detection weights must align with kernel distance bins.")
    distance_numerator = np.zeros(len(basis.target_order), dtype="float64")
    detection_numerator = np.zeros_like(distance_numerator)
    for distance_km, detection_weight, matrix in zip(
        basis.distance_km, detection, basis.matrices, strict=True
    ):
        support = np.asarray(matrix.sum(axis=0)).ravel()
        distance_numerator += support * float(distance_km)
        detection_numerator += support * float(detection_weight)
    denominator = basis.static_target_support
    return {
        "DISTANCE_KM_MEAN": np.divide(
            distance_numerator,
            denominator,
            out=np.full_like(distance_numerator, np.nan),
            where=denominator > 0.0,
        ),
        "DISTANCE_DETECTION_WEIGHT": np.divide(
            detection_numerator,
            denominator,
            out=np.full_like(detection_numerator, np.nan),
            where=denominator > 0.0,
        ),
    }


def condition_arrays(
    *,
    visibility_km: np.ndarray,
    daylight_fraction: np.ndarray,
    wind_speed_ms: np.ndarray,
    precipitation_mm_day: np.ndarray,
    weather_complete: np.ndarray,
    wind_midpoint_ms: float,
    wind_slope_ms: float,
    precipitation_half_mm_day: float,
) -> dict[str, np.ndarray]:
    visibility = np.maximum(np.asarray(visibility_km, dtype="float64"), 0.0)
    daylight = np.array(daylight_fraction, dtype="float64", copy=True)
    wind = np.asarray(wind_speed_ms, dtype="float64")
    precipitation = np.asarray(precipitation_mm_day, dtype="float64")
    complete = np.asarray(weather_complete, dtype=bool)
    available = (
        complete
        & np.isfinite(visibility)
        & np.isfinite(daylight)
        & np.isfinite(wind)
        & np.isfinite(precipitation)
    )
    wind_weight = stable_sigmoid((wind_midpoint_ms - wind) / wind_slope_ms)
    precipitation_weight = 1.0 / (1.0 + np.maximum(precipitation, 0.0) / precipitation_half_mm_day)
    # Daylight has its own deterministic source, independent of weather coverage.
    for values, raw in (
        (visibility, visibility),
        (wind_weight, wind),
        (precipitation_weight, precipitation),
    ):
        values[~complete | ~np.isfinite(raw)] = np.nan
    return {
        "visibility_km": visibility,
        "daylight_weight": daylight,
        "wind_weight": wind_weight,
        "precipitation_weight": precipitation_weight,
        "available": available,
    }


def distance_visibility_weight(
    visibility_km: np.ndarray,
    distance_km: float,
    *,
    transition_fraction: float,
    minimum_transition_km: float,
) -> np.ndarray:
    visibility = np.asarray(visibility_km, dtype="float64")
    nonnegative = np.maximum(visibility, 0.0)
    transition = np.maximum(minimum_transition_km, transition_fraction * nonnegative)
    result = stable_sigmoid((nonnegative - float(distance_km)) / transition)
    result[visibility <= 0.0] = 0.0
    result[~np.isfinite(visibility)] = np.nan
    return result


def propagate_component(
    activity: np.ndarray,
    conditions: dict[str, np.ndarray],
    basis: KernelBasis,
    *,
    visibility_transition_fraction: float,
    visibility_minimum_transition_km: float,
    wind_exponent: float,
    precipitation_exponent: float,
    return_coverage: bool = False,
) -> np.ndarray | tuple[np.ndarray, np.ndarray]:
    """Propagate one additive activity component through separate conditions."""

    activity = np.asarray(activity, dtype="float64")
    if activity.shape != conditions["visibility_km"].shape:
        raise ValueError("Activity and condition arrays must have equal date/source shape.")
    result = np.zeros((activity.shape[0], len(basis.target_order)), dtype="float64")
    evaluated = np.zeros_like(result)
    base = (
        activity
        * conditions["daylight_weight"]
        * conditions["wind_weight"] ** wind_exponent
        * conditions["precipitation_weight"] ** precipitation_exponent
    )
    for distance, matrix in zip(basis.distance_km, basis.matrices):
        if matrix.nnz == 0:
            continue
        visibility = distance_visibility_weight(
            conditions["visibility_km"],
            float(distance),
            transition_fraction=visibility_transition_fraction,
            minimum_transition_km=visibility_minimum_transition_km,
        )
        values = base * visibility
        # A known multiplicative zero requires no value for the other factors.
        structural_zero = (activity == 0) | (conditions["daylight_weight"] == 0) | (visibility == 0)
        values = np.where(structural_zero, 0.0, values)
        evaluated += np.isfinite(values).astype(float) @ matrix
        values = np.nan_to_num(values, nan=0.0)
        result += values @ matrix
    support = basis.static_target_support[None, :]
    coverage = np.divide(evaluated, support, out=np.ones_like(evaluated), where=support > 0)
    result[(evaluated == 0) & (support > 0)] = np.nan
    return (result, coverage) if return_coverage else result


def propagated_condition_summaries(
    conditions: dict[str, np.ndarray],
    basis: KernelBasis,
    *,
    visibility_transition_fraction: float,
    visibility_minimum_transition_km: float,
) -> dict[str, np.ndarray]:
    """Return separate static-support-weighted condition summaries at targets."""

    shape = (conditions["visibility_km"].shape[0], len(basis.target_order))
    visibility_sum = np.zeros(shape, dtype="float64")
    full = sum(
        basis.matrices,
        sparse.csr_matrix((len(basis.source_order), len(basis.target_order))),
    )
    available = conditions["available"].astype("float64")
    coverage = np.asarray(available @ full)
    for distance, matrix in zip(basis.distance_km, basis.matrices):
        if matrix.nnz:
            visibility = distance_visibility_weight(
                conditions["visibility_km"],
                float(distance),
                transition_fraction=visibility_transition_fraction,
                minimum_transition_km=visibility_minimum_transition_km,
            )
            visibility_sum += np.nan_to_num(visibility, nan=0.0) @ matrix
    denominator = basis.static_target_support[None, :]

    def normalized(numerator: np.ndarray, divisor: np.ndarray = denominator) -> np.ndarray:
        return np.divide(
            numerator,
            divisor,
            out=np.full_like(numerator, np.nan),
            where=divisor > 0.0,
        )

    result = {"DYNAMIC_CONDITION_COVERAGE": normalized(coverage)}
    for output, key in (
        ("ATMOSPHERIC_VISIBILITY_WEIGHT", "visibility_km"),
        ("DAYLIGHT_WEIGHT", "daylight_weight"),
        ("WIND_WEIGHT", "wind_weight"),
        ("PRECIPITATION_WEIGHT", "precipitation_weight"),
    ):
        values = conditions[key]
        support = np.isfinite(values).astype(float) @ full
        numerator = (
            visibility_sum if key == "visibility_km" else np.nan_to_num(values, nan=0.0) @ full
        )
        result[output] = normalized(numerator, support)
        result[output + "_COVERAGE"] = normalized(support)
    return result


def apply_coverage_semantics(
    values: np.ndarray,
    coverage_complete: np.ndarray,
    *,
    derived: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Keep positive partial contributions and refuse unsupported zero claims."""

    values = np.asarray(values, dtype="float64").copy()
    complete = np.asarray(coverage_complete, dtype=bool)
    if complete.ndim == 1:
        complete = complete[:, None]
    states = np.full(values.shape, ComponentState.UNKNOWN.value, dtype="<U24")
    positive = np.isfinite(values) & (values > 0.0)
    states[positive & np.broadcast_to(complete, values.shape)] = ComponentState.POSITIVE.value
    states[positive & ~np.broadcast_to(complete, values.shape)] = ComponentState.PARTIAL.value
    zero_complete = np.isfinite(values) & (values == 0.0) & np.broadcast_to(complete, values.shape)
    states[zero_complete] = (
        ComponentState.DERIVED_ZERO.value if derived else ComponentState.OBSERVED_ZERO.value
    )
    unsupported_zero = (
        np.isfinite(values) & (values == 0.0) & ~np.broadcast_to(complete, values.shape)
    )
    values[unsupported_zero] = np.nan
    return values, states


def add_transformations(frame: pd.DataFrame, columns: list[str], period: str) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        result[f"{column.removesuffix('_RAW')}_LOG1P"] = np.log1p(result[column].clip(lower=0.0))
        result[f"{column.removesuffix('_RAW')}_SPATIAL_RANK"] = result.groupby(period, sort=False)[
            column
        ].rank(method="average", pct=True)
    return result


def aggregate_weekly(
    daily: pl.LazyFrame,
    *,
    additive_columns: list[str],
    mean_columns: list[str],
    state_columns: list[str],
) -> pl.DataFrame:
    schema = daily.collect_schema().names()
    expressions: list[pl.Expr] = [pl.len().alias("PERIOD_DAY_COUNT")]
    expressions.extend(
        pl.col(column).first().alias(column)
        for column in (
            "H3_RESOLUTION",
            "GENERATION_ID",
            "CONFIG_HASH",
            "SOURCE_HASHES_JSON",
            "KNOWLEDGE_TIME_UTC",
            "SOURCE_VINTAGES_JSON",
            "HISTORICAL_RECONSTRUCTION",
            "COMPONENT_PROVENANCE_JSON",
            "SOURCE_COVERAGE_STATE",
            "AIS_SOURCE_COVERAGE_STATE",
            "AIS_TEMPORAL_COVERAGE_SCOPE",
            "AIS_SPATIAL_COVERAGE_STATE",
            "AIS_RECEIVER_COVERAGE_METHOD",
            "AIS_ABSENT_POLICY",
            "AIS_PARENT_H3_R6",
            "AIS_SPATIAL_ALLOCATION_METHOD",
        )
        if column in schema
    )
    if "DATA_COVERAGE_STATE" in schema:
        expressions.append(
            pl.when(pl.col("DATA_COVERAGE_STATE").eq("complete").all())
            .then(pl.lit("complete"))
            .otherwise(pl.lit("partial"))
            .alias("DATA_COVERAGE_STATE")
        )
    expressions.extend(
        pl.col(column).first().alias(column)
        for column in schema
        if column.endswith("_CAUSAL_ROLE") or column == "PRODUCT_ROLE"
    )
    if "AIS_SOURCE_COVERAGE_COMPLETE" in schema:
        expressions.extend(
            [
                pl.col("AIS_SOURCE_COVERAGE_COMPLETE").all(),
                pl.col("AIS_SOURCE_COVERAGE_STATUS").first(),
            ]
        )
    if "AIS_TEMPORAL_COVERAGE_COMPLETE" in schema:
        expressions.append(pl.col("AIS_TEMPORAL_COVERAGE_COMPLETE").all())
    if "FERRY_SOURCE_COVERAGE_COMPLETE" in schema:
        expressions.extend(
            [
                pl.col("FERRY_SOURCE_COVERAGE_COMPLETE").all(),
                pl.col("FERRY_SOURCE_COVERAGE_STATE").first(),
            ]
        )
    for column in additive_columns:
        expressions.extend(
            [
                pl.when(pl.col(column).count() > 0)
                .then(pl.col(column).sum())
                .otherwise(pl.lit(None, dtype=pl.Float64))
                .alias(column),
                pl.col(column).count().alias(f"{column.removesuffix('_RAW')}_AVAILABLE_DAY_COUNT"),
            ]
        )
    expressions.extend(pl.col(column).mean().alias(column) for column in mean_columns)
    expressions.extend(
        pl.when(pl.col(column).eq(ComponentState.PROCESSING_FAILURE.value).any())
        .then(pl.lit(ComponentState.PROCESSING_FAILURE.value))
        .when(pl.col(column).n_unique() == 1)
        .then(pl.col(column).first())
        .when(
            pl.col(column)
            .is_in(
                [
                    ComponentState.PARTIAL.value,
                    ComponentState.UNKNOWN.value,
                    ComponentState.UNMAPPED.value,
                    ComponentState.OUTSIDE_JURISDICTION.value,
                ]
            )
            .any()
        )
        .then(pl.lit(ComponentState.PARTIAL.value))
        .when(pl.col(column).eq(ComponentState.POSITIVE.value).any())
        .then(pl.lit(ComponentState.POSITIVE.value))
        .otherwise(pl.lit(ComponentState.DERIVED_ZERO.value))
        .alias(column)
        for column in state_columns
    )
    result = (
        daily.with_columns(pl.col("DATE").dt.truncate("1w").alias("WEEK_START"))
        .group_by("WEEK_START", "H3_INDEX")
        .agg(expressions)
        .sort("WEEK_START", "H3_INDEX")
        .collect(engine="streaming")
    )
    result = result.with_columns((pl.col("PERIOD_DAY_COUNT") < 7).alias("INCOMPLETE_WEEK"))
    for column in additive_columns:
        if column.endswith("_RAW"):
            result = result.with_columns(
                pl.col(column)
                .clip(lower_bound=0.0)
                .log1p()
                .alias(f"{column.removesuffix('_RAW')}_LOG1P"),
                (
                    pl.col(column).rank(method="average").over("WEEK_START")
                    / pl.col(column).count().over("WEEK_START")
                ).alias(f"{column.removesuffix('_RAW')}_SPATIAL_RANK"),
            )
    return result
