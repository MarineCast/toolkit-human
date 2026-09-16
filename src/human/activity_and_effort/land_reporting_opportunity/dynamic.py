"""Build daily and weekly land reporting-opportunity components and composites."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import h3
import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import sparse

from human.activity_and_effort.observation_opportunity_contract import (
    LINEAGE_COLUMNS,
    SCHEMA_VERSION,
    ComponentState,
    add_lineage_pandas,
    component_provenance_json,
    lineage_values,
    validate_product_contract,
)
from human.utils.artifacts import atomic_write_json, sha256_file

from .components import HUMAN_STREAMS, PRIMARY_STREAM, STREAM_COMPONENTS
from .config import LandReportingConfig

EARTH_MEAN_RADIUS_KM = 6371.0088

WEATHER_COLUMNS = [
    "H3_INDEX",
    "DATE",
    "VISIBILITY_KM_MEAN",
    "WIND_SPEED_10M_MS_MEAN",
    "PRECIP_MM_DAY_ESTIMATE",
    "SAMPLE_COVERAGE_FRAC",
    "QC_STATE",
]
DAYLIGHT_COLUMNS = ["H3_INDEX", "DATE", "DAYLIGHT_FRACTION"]
CALENDAR_COLUMNS = ["date", "calendar_effort_weight"]

LAND_V2_ALIASES = {
    "LAND_EFFORT_PROXY": "LAND_OBSERVATION_OPPORTUNITY",
    "VERIFIED_LAND_EFFORT_PROXY": "VERIFIED_LAND_OBSERVATION_OPPORTUNITY",
}

CONDITION_COMPONENT_COLUMNS = (
    "ATMOSPHERIC_VISIBILITY_WEIGHT",
    "DAYLIGHT_WEIGHT",
    "WIND_WEIGHT",
    "PRECIPITATION_WEIGHT",
)
CONDITION_STATE_COLUMNS = tuple(
    f"{column.removesuffix('_WEIGHT')}_STATE" for column in CONDITION_COMPONENT_COLUMNS
)


@dataclass(frozen=True)
class DynamicBuildResult:
    daily_path: Path
    weekly_path: Path
    metadata_path: Path
    daily_rows: int
    weekly_rows: int
    start_date: str
    end_date: str
    target_cells: int


@dataclass
class DynamicBasis:
    target_order: pd.Index
    target_static: pd.DataFrame
    distance_km: np.ndarray
    weather_bases: list[dict[str, Any]]
    weather_daily: dict[str, pd.DataFrame]
    daylight_daily: dict[str, pd.DataFrame]
    calendar_daily: pd.DataFrame
    dates: pd.DatetimeIndex
    native_pairs: int
    grouped_rows: int


def stable_sigmoid(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype="float64")
    return 1.0 / (1.0 + np.exp(-np.clip(array, -60.0, 60.0)))


def iter_date_chunks(dates: pd.DatetimeIndex, chunk_days: int) -> Iterator[pd.DatetimeIndex]:
    for start in range(0, len(dates), chunk_days):
        yield dates[start : start + chunk_days]


def _source_target_distance_km(static: pd.DataFrame) -> np.ndarray:
    source_order = pd.Index(static["source_h3"].astype(str).unique())
    target_order = pd.Index(static["target_h3"].astype(str).unique())
    source_codes = pd.Categorical(static["source_h3"].astype(str), categories=source_order).codes
    target_codes = pd.Categorical(static["target_h3"].astype(str), categories=target_order).codes
    if (source_codes < 0).any() or (target_codes < 0).any():
        raise ValueError("A static viewshed pair was absent from its cell universe.")
    source_latlng = np.radians(
        np.asarray([h3.cell_to_latlng(cell) for cell in source_order], dtype="float64")
    )
    target_latlng = np.radians(
        np.asarray([h3.cell_to_latlng(cell) for cell in target_order], dtype="float64")
    )
    latitude_delta = target_latlng[target_codes, 0] - source_latlng[source_codes, 0]
    longitude_delta = target_latlng[target_codes, 1] - source_latlng[source_codes, 1]
    haversine = (
        np.sin(latitude_delta / 2.0) ** 2
        + np.cos(source_latlng[source_codes, 0])
        * np.cos(target_latlng[target_codes, 0])
        * np.sin(longitude_delta / 2.0) ** 2
    )
    distance = 2.0 * EARTH_MEAN_RADIUS_KM * np.arcsin(np.sqrt(np.clip(haversine, 0, 1)))
    if not np.isfinite(distance).all():
        raise ValueError("Source-target distance calculation produced non-finite values.")
    return distance


def _load_dynamic_inputs(
    cfg: LandReportingConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
    from .inputs import read_pinned_json, read_pinned_parquet

    weather_manifest = read_pinned_json(cfg, "surface_weather_manifest")
    daylight_manifest = read_pinned_json(cfg, "daylight_manifest")
    weather_timezone = weather_manifest.get("resolved_config", {}).get("timezone", "UTC")
    daylight_timezone = daylight_manifest.get("resolved_config", {}).get(
        "timezone", weather_timezone
    )
    if weather_timezone != daylight_timezone:
        raise ValueError("Weather and daylight must use the same daily calendar timezone")
    if weather_manifest.get("source_completeness") != "complete":
        raise ValueError("Surface weather must be coverage-complete before dynamic land build.")
    if daylight_manifest.get("source_completeness") not in {
        "complete",
        "not_applicable_deterministic",
    }:
        raise ValueError("Daylight manifest has unsupported completeness semantics.")
    weather = read_pinned_parquet(cfg, "weather_partition_", WEATHER_COLUMNS)
    daylight = read_pinned_parquet(cfg, "daylight_partition_", DAYLIGHT_COLUMNS)
    calendar = read_pinned_parquet(cfg, "calendar", CALENDAR_COLUMNS, exact=True)
    weather["DATE"] = pd.to_datetime(weather["DATE"], errors="coerce").dt.normalize()
    daylight["DATE"] = pd.to_datetime(daylight["DATE"], errors="coerce").dt.normalize()
    calendar = calendar.rename(columns={"date": "DATE"})
    calendar["DATE"] = pd.to_datetime(calendar["DATE"], errors="coerce").dt.normalize()
    for label, frame in (("weather", weather), ("daylight", daylight)):
        if frame.duplicated(["H3_INDEX", "DATE"]).any():
            raise ValueError(f"{label} is not unique by H3_INDEX and DATE.")
    if calendar["DATE"].isna().any() or not calendar["DATE"].is_unique:
        raise ValueError("Calendar is not unique by non-null DATE.")
    start = max(daylight["DATE"].min(), calendar["DATE"].min())
    end = min(daylight["DATE"].max(), calendar["DATE"].max())
    if cfg.start_date is not None:
        start, end = pd.Timestamp(cfg.start_date), pd.Timestamp(cfg.end_date)
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ValueError("Dynamic inputs have no shared temporal support.")
    return weather, daylight, calendar, pd.date_range(start, end, freq="D")


def prepare_dynamic_basis(
    cfg: LandReportingConfig,
    static: pd.DataFrame,
    source: pd.DataFrame,
    target_support: pd.DataFrame | None = None,
) -> DynamicBasis:
    required_static = {
        "source_h3",
        "target_h3",
        "weight_distance",
        "weight_static_viewability",
    }
    missing_static = sorted(required_static.difference(static.columns))
    if missing_static:
        raise ValueError(f"Static viewshed is missing columns: {missing_static}")
    required_source = {
        "source_h3",
        *(component for _name, component in STREAM_COMPONENTS if component is not None),
    }
    missing_source = sorted(required_source.difference(source.columns))
    if missing_source:
        raise ValueError(f"Land source components are missing columns: {missing_source}")
    if static.duplicated(["source_h3", "target_h3"]).any():
        raise ValueError("Static viewshed contains duplicate source-target pairs.")
    if source["source_h3"].duplicated().any():
        raise ValueError("Land source components are not unique by source_h3.")
    static_sources = set(static["source_h3"].astype(str))
    expected_sources = set(source["source_h3"].astype(str))
    if not static_sources.issubset(expected_sources):
        raise ValueError("Static land sources are missing activity-budget context.")

    weather, daylight, calendar, dates = _load_dynamic_inputs(cfg)
    # Source support can extend beyond the weather/daylight centroid bbox.
    # Daylight is deterministic; derive missing parents with the same canonical
    # solar formula, and retain an explicit supplement in this generation.
    required_daylight = {h3.cell_to_parent(cell, 4) for cell in expected_sources}
    missing_daylight = sorted(required_daylight - set(daylight.H3_INDEX))
    if missing_daylight:
        from meteorology.daylight.features import (
            build_daylight_features,
        )

        centroids = pd.DataFrame({"h3": missing_daylight})
        centroids[["centroid_lat", "centroid_lon"]] = [
            h3.cell_to_latlng(cell) for cell in missing_daylight
        ]
        supplement = build_daylight_features(
            centroids,
            str(dates.min().date()),
            str(dates.max().date()),
            default_weight="fraction",
            validate=True,
        ).rename(
            columns={"h3": "H3_INDEX", "date": "DATE", "daylight_fraction": "DAYLIGHT_FRACTION"}
        )
        supplement = supplement[DAYLIGHT_COLUMNS]
        supplement["DATE"] = pd.to_datetime(supplement["DATE"])
        supplement.to_parquet(
            cfg.daily_output_path.parent / "daylight_support_supplement.parquet", index=False
        )
        daylight = pd.concat([daylight, supplement], ignore_index=True)
    pairs = static[
        ["source_h3", "target_h3", "weight_distance", "weight_static_viewability"]
    ].copy()
    pairs["H3_INDEX"] = pairs["target_h3"].map(
        lambda cell: h3.cell_to_parent(str(cell), cfg.dynamic_output_h3_resolution)
    )
    target_child_counts = (
        (target_support if target_support is not None else pairs)[["target_h3", "H3_INDEX"]]
        .drop_duplicates()
        .groupby("H3_INDEX")
        .size()
    )
    pairs["TARGET_R7_CHILD_COUNT"] = pairs["H3_INDEX"].map(target_child_counts)
    pairs["WEATHER_H3_R5"] = pairs["source_h3"].map(lambda cell: h3.cell_to_parent(str(cell), 5))
    distances = _source_target_distance_km(pairs)
    pairs["DISTANCE_KM"] = distances
    pairs["DISTANCE_BIN_INDEX"] = np.rint(distances / cfg.dynamic_distance_bin_km).astype("int16")
    pairs = pairs.merge(source, on="source_h3", how="left", validate="many_to_one")
    if target_support is not None:
        weights = target_support.set_index("target_h3")["R6_WATER_AREA_WEIGHT"]
        if not set(pairs.target_h3).issubset(set(weights.index)):
            raise ValueError("General-land pairs lie outside canonical modeled water support")
        pairs["_BASE_STATIC_WEIGHT"] = pairs.weight_static_viewability * pairs.target_h3.map(
            weights
        )
    else:
        pairs["_BASE_STATIC_WEIGHT"] = (
            pairs["weight_static_viewability"] / pairs["TARGET_R7_CHILD_COUNT"]
        )
    weight_columns: dict[str, str] = {}
    context_columns: dict[str, str] = {}
    for stream, component in STREAM_COMPONENTS:
        weight_column = f"_{stream}_WEIGHT"
        context_column = f"_{stream}_CONTEXT_WEIGHT"
        if component is None:
            pairs[weight_column] = pairs["_BASE_STATIC_WEIGHT"]
            pairs[context_column] = pairs["_BASE_STATIC_WEIGHT"]
        else:
            pairs[weight_column] = pairs["_BASE_STATIC_WEIGHT"] * pairs[component]
            pairs[context_column] = pairs["_BASE_STATIC_WEIGHT"].where(pairs[component].notna())
        weight_columns[stream] = weight_column
        context_columns[stream] = context_column

    aggregation: dict[str, tuple[str, Any]] = {"PAIR_COUNT": ("source_h3", "size")}
    for stream in dict(STREAM_COMPONENTS):
        aggregation[f"{stream}_WEIGHT"] = (
            weight_columns[stream],
            lambda values: values.sum(min_count=1),
        )
        aggregation[f"{stream}_CONTEXT_WEIGHT"] = (
            context_columns[stream],
            lambda values: values.sum(min_count=1),
        )
    grouped = (
        pairs.groupby(["H3_INDEX", "WEATHER_H3_R5", "DISTANCE_BIN_INDEX"], as_index=False)
        .agg(**aggregation)
        .sort_values(["WEATHER_H3_R5", "H3_INDEX", "DISTANCE_BIN_INDEX"])
        .reset_index(drop=True)
    )
    if int(grouped["PAIR_COUNT"].sum()) != len(pairs):
        raise ValueError("Dynamic grouped basis did not conserve static viewshed pairs.")
    if grouped.duplicated(["H3_INDEX", "WEATHER_H3_R5", "DISTANCE_BIN_INDEX"]).any():
        raise ValueError("Dynamic grouped basis keys are not unique.")

    target_order = pd.Index(
        sorted((target_support if target_support is not None else grouped)["H3_INDEX"].unique()),
        name="H3_INDEX",
    )
    target_codes = {cell: index for index, cell in enumerate(target_order)}
    target_count = len(target_order)
    distance_bin_count = int(grouped["DISTANCE_BIN_INDEX"].max()) + 1
    distance_km = np.arange(distance_bin_count, dtype="float64") * cfg.dynamic_distance_bin_km
    static_totals = (
        grouped.groupby("H3_INDEX")
        .agg(
            **{
                f"{stream}_STATIC_RAW": (f"{stream}_WEIGHT", "sum")
                for stream in dict(STREAM_COMPONENTS)
            },
            **{
                f"{stream}_CONTEXT_STATIC_SUPPORT": (
                    f"{stream}_CONTEXT_WEIGHT",
                    "sum",
                )
                for stream in dict(STREAM_COMPONENTS)
            },
        )
        .reindex(target_order)
    )
    evaluated_column = "POPULATION_TRAVEL_EVALUATED_SELECTED_POPULATION_FRACTION"
    if evaluated_column in pairs:
        weighted_coverage = pairs["_BASE_STATIC_WEIGHT"] * pairs[evaluated_column]
        numerator = weighted_coverage.groupby(pairs.H3_INDEX).sum(min_count=1)
        denominator = pairs.groupby("H3_INDEX")["_BASE_STATIC_WEIGHT"].sum().replace(0, np.nan)
        static_totals["POPULATION_ORIGIN_EVALUATED_COVERAGE"] = numerator.div(denominator).reindex(
            target_order
        )
    physical_support = static_totals["PHYSICAL_VIEWABILITY_CONTEXT_STATIC_SUPPORT"].replace(
        0.0, np.nan
    )
    for stream in dict(STREAM_COMPONENTS):
        static_totals[f"{stream}_CONTEXT_STATIC_SUPPORT"] = static_totals[
            f"{stream}_CONTEXT_STATIC_SUPPORT"
        ].fillna(0.0)
        static_totals[f"{stream}_STATIC_CONTEXT_FRACTION"] = (
            static_totals[f"{stream}_CONTEXT_STATIC_SUPPORT"] / physical_support
        ).clip(0.0, 1.0)
    static_totals = static_totals.reset_index()
    static_totals["TARGET_R7_CHILD_COUNT"] = static_totals["H3_INDEX"].map(target_child_counts)
    static_totals["LAND_SOURCE_COUNT"] = (
        static_totals["H3_INDEX"].map(pairs.groupby("H3_INDEX")["source_h3"].nunique()).fillna(0)
    )
    static_totals["DISTANCE_KM_MEAN"] = static_totals["H3_INDEX"].map(
        pairs.groupby("H3_INDEX")["DISTANCE_KM"].mean()
    )
    static_totals["DISTANCE_DETECTION_WEIGHT"] = static_totals["H3_INDEX"].map(
        pairs.groupby("H3_INDEX")["weight_distance"].mean()
    )

    weather_bases: list[dict[str, Any]] = []
    for weather_cell, weather_basis in grouped.groupby("WEATHER_H3_R5", sort=False):
        row_codes = weather_basis["DISTANCE_BIN_INDEX"].to_numpy()
        column_codes = weather_basis["H3_INDEX"].map(target_codes).to_numpy()
        matrices = []
        context_totals = []
        for stream in dict(STREAM_COMPONENTS):
            values = weather_basis[f"{stream}_WEIGHT"]
            valid = values.notna() & values.ne(0.0)
            matrix = sparse.csr_matrix(
                (
                    values.loc[valid].to_numpy(dtype="float64"),
                    (row_codes[valid], column_codes[valid]),
                ),
                shape=(distance_bin_count, target_count),
            )
            matrix.eliminate_zeros()
            matrices.append(matrix)
            total = np.zeros(target_count, dtype="float64")
            context = weather_basis[f"{stream}_CONTEXT_WEIGHT"].fillna(0.0).to_numpy()
            np.add.at(total, column_codes, context)
            context_totals.append(total)
        weather_bases.append(
            {
                "weather_h3_r5": str(weather_cell),
                "daylight_h3_r4": h3.cell_to_parent(str(weather_cell), 4),
                "matrix_transpose": sparse.hstack(matrices, format="csr").transpose().tocsr(),
                "context_totals": np.concatenate(context_totals),
            }
        )

    return DynamicBasis(
        target_order=target_order,
        target_static=static_totals,
        distance_km=distance_km,
        weather_bases=weather_bases,
        weather_daily={
            str(cell): frame.set_index("DATE").sort_index()
            for cell, frame in weather.groupby("H3_INDEX")
        },
        daylight_daily={
            str(cell): frame.set_index("DATE").sort_index()
            for cell, frame in daylight.groupby("H3_INDEX")
        },
        calendar_daily=calendar.set_index("DATE").sort_index(),
        dates=dates,
        native_pairs=len(pairs),
        grouped_rows=len(grouped),
    )


def compute_dynamic_chunk(
    cfg: LandReportingConfig,
    basis: DynamicBasis,
    dates: pd.DatetimeIndex,
) -> dict[str, np.ndarray]:
    dates = pd.DatetimeIndex(dates).normalize()
    date_count = len(dates)
    target_count = len(basis.target_order)
    stream_count = len(STREAM_COMPONENTS)
    packed_width = stream_count * target_count
    raw = np.zeros((date_count, packed_width), dtype="float64")
    context = np.zeros_like(raw)
    core_physical_raw = np.zeros((date_count, target_count), dtype="float64")
    core_physical_context = np.zeros_like(core_physical_raw)
    visibility_component = np.zeros_like(core_physical_raw)
    daylight_component = np.zeros_like(core_physical_raw)
    wind_component = np.zeros_like(core_physical_raw)
    precipitation_component = np.zeros_like(core_physical_raw)
    diagnostic_support = {
        name: np.zeros_like(core_physical_raw) for name in CONDITION_COMPONENT_COLUMNS
    }

    for weather_basis in basis.weather_bases:
        weather = basis.weather_daily.get(weather_basis["weather_h3_r5"])
        daylight = basis.daylight_daily.get(weather_basis["daylight_h3_r4"])
        weather_dates = (
            weather.reindex(dates)
            if weather is not None
            else pd.DataFrame(np.nan, index=dates, columns=WEATHER_COLUMNS)
        )
        daylight_dates = (
            daylight.reindex(dates)
            if daylight is not None
            else pd.DataFrame({"DAYLIGHT_FRACTION": np.nan}, index=dates)
        )
        visibility = weather_dates["VISIBILITY_KM_MEAN"].to_numpy(dtype="float64")
        daylight_fraction = daylight_dates["DAYLIGHT_FRACTION"].to_numpy(dtype="float64")
        wind = weather_dates["WIND_SPEED_10M_MS_MEAN"].to_numpy(dtype="float64")
        precipitation = weather_dates["PRECIP_MM_DAY_ESTIMATE"].to_numpy(dtype="float64")
        core_available = (
            np.isfinite(visibility)
            & np.isfinite(daylight_fraction)
            & weather_dates["SAMPLE_COVERAGE_FRAC"].eq(1.0).to_numpy()
            & weather_dates["QC_STATE"].eq("COMPLETE").to_numpy()
        )
        extended_available = core_available & np.isfinite(wind) & np.isfinite(precipitation)
        nonnegative_visibility = np.maximum(visibility, 0.0)
        transition = np.maximum(
            cfg.visibility_minimum_transition_km,
            cfg.visibility_transition_fraction * nonnegative_visibility,
        )
        visibility_support = stable_sigmoid(
            (nonnegative_visibility[:, None] - basis.distance_km[None, :]) / transition[:, None]
        )
        visibility_support[visibility <= 0.0, :] = 0.0
        weather_quality = (
            weather_dates["SAMPLE_COVERAGE_FRAC"].eq(1.0).to_numpy()
            & weather_dates["QC_STATE"].eq("COMPLETE").to_numpy()
        )
        visibility_available = np.isfinite(visibility) & weather_quality
        visibility_support[~visibility_available, :] = 0.0
        core_support = visibility_support * daylight_fraction[:, None]
        core_support[~core_available, :] = 0.0
        wind_support = stable_sigmoid(
            (cfg.wind_support_midpoint_ms - wind) / cfg.wind_support_slope_ms
        )
        precipitation_support = 1.0 / (
            1.0 + np.maximum(precipitation, 0.0) / cfg.precipitation_support_half_mm_day
        )
        conditions = (
            wind_support**cfg.conditions_wind_exponent
            * precipitation_support**cfg.conditions_precipitation_exponent
        )
        extended_support = core_support * conditions[:, None]
        extended_support[~extended_available, :] = 0.0
        matrix = weather_basis["matrix_transpose"]
        physical_matrix = matrix[:target_count]
        core_physical_raw += (physical_matrix @ core_support.T).T
        raw += (matrix @ extended_support.T).T
        totals = weather_basis["context_totals"]
        for name, numerator, support, available in (
            (
                "ATMOSPHERIC_VISIBILITY_WEIGHT",
                visibility_component,
                visibility_support,
                visibility_available,
            ),
            (
                "DAYLIGHT_WEIGHT",
                daylight_component,
                daylight_fraction[:, None],
                np.isfinite(daylight_fraction),
            ),
            (
                "WIND_WEIGHT",
                wind_component,
                wind_support[:, None],
                np.isfinite(wind) & weather_quality,
            ),
            (
                "PRECIPITATION_WEIGHT",
                precipitation_component,
                precipitation_support[:, None],
                np.isfinite(precipitation) & weather_quality,
            ),
        ):
            supported = np.broadcast_to(support, core_support.shape).copy()
            supported[~available, :] = 0.0
            diagnostic_matrix = weather_basis.get("diagnostic_matrix_transpose", physical_matrix)
            numerator += (diagnostic_matrix @ supported.T).T
            diagnostic_support[name] += available[:, None] * totals[:target_count][None, :]
        core_physical_context += core_available[:, None] * totals[:target_count][None, :]
        context += extended_available[:, None] * totals[None, :]

    calendar = basis.calendar_daily.reindex(dates)["calendar_effort_weight"].to_numpy(
        dtype="float64"
    )
    calendar_available = np.isfinite(calendar)
    raw_blocks: dict[str, np.ndarray] = {}
    context_blocks: dict[str, np.ndarray] = {}
    static_context: dict[str, np.ndarray] = {}
    for index, (stream, _component) in enumerate(STREAM_COMPONENTS):
        start = index * target_count
        end = (index + 1) * target_count
        raw_blocks[stream] = raw[:, start:end]
        context_blocks[stream] = context[:, start:end]
        static_context[stream] = basis.target_static[f"{stream}_CONTEXT_STATIC_SUPPORT"].to_numpy(
            dtype="float64"
        )
        if stream in cfg.calendar_applied_streams:
            raw_blocks[stream] *= calendar[:, None]
            raw_blocks[stream][~calendar_available, :] = np.nan
            context_blocks[stream][~calendar_available, :] = 0.0

    def coverage(available: np.ndarray, total: np.ndarray) -> np.ndarray:
        return np.clip(
            np.divide(
                available,
                total[None, :],
                out=np.full_like(available, np.nan),
                where=total[None, :] > 0.0,
            ),
            0.0,
            1.0,
        )

    result: dict[str, np.ndarray] = {
        "DATE": dates.to_numpy(dtype="datetime64[ns]"),
        "CALENDAR_EFFORT_WEIGHT": calendar,
        "PHYSICAL_VIEWABILITY_CORE_RAW": core_physical_raw,
        "PHYSICAL_VIEWABILITY_CORE_COVERAGE": coverage(
            core_physical_context, static_context["PHYSICAL_VIEWABILITY"]
        ),
    }
    physical_static = basis.target_static["PHYSICAL_VIEWABILITY_CONTEXT_STATIC_SUPPORT"].to_numpy(
        dtype="float64"
    )
    physical_dynamic_coverage = coverage(context_blocks["PHYSICAL_VIEWABILITY"], physical_static)
    for name, numerator in (
        ("ATMOSPHERIC_VISIBILITY_WEIGHT", visibility_component),
        ("DAYLIGHT_WEIGHT", daylight_component),
        ("WIND_WEIGHT", wind_component),
        ("PRECIPITATION_WEIGHT", precipitation_component),
    ):
        values = np.divide(
            numerator,
            diagnostic_support[name],
            out=np.full_like(numerator, np.nan),
            where=diagnostic_support[name] > 0.0,
        )
        result[name] = np.clip(values, 0.0, 1.0)
        result[f"{name.removesuffix('_WEIGHT')}_COVERAGE"] = coverage(
            diagnostic_support[name], physical_static
        )
    result["DYNAMIC_CONDITION_COVERAGE"] = physical_dynamic_coverage
    for stream, _component in STREAM_COMPONENTS:
        values = raw_blocks[stream]
        available_context = context_blocks[stream]
        values[available_context <= 0.0] = np.nan
        result[f"{stream}_RAW"] = values
        result[f"{stream}_DYNAMIC_CONTEXT_COVERAGE"] = coverage(
            available_context, static_context[stream]
        )
    conditioned = "PHYSICAL_VIEWABILITY_PROVEN_ZERO" in basis.target_static
    if conditioned:
        for stream, _component in STREAM_COMPONENTS:
            column = f"{stream}_PROVEN_ZERO"
            if column not in basis.target_static:
                continue
            proven = basis.target_static[column].to_numpy(dtype=bool)
            result[f"{stream}_RAW"][:, proven] = 0.0
            result[f"{stream}_DYNAMIC_CONTEXT_COVERAGE"][:, proven] = 1.0
        physical_zero = basis.target_static.PHYSICAL_VIEWABILITY_PROVEN_ZERO.to_numpy(dtype=bool)
        core_physical_raw[core_physical_context <= 0] = np.nan
        core_physical_raw[:, physical_zero] = 0.0
        result["PHYSICAL_VIEWABILITY_CORE_COVERAGE"][:, physical_zero] = 1.0
        result["DYNAMIC_CONDITION_COVERAGE"][:, physical_zero] = 1.0
        for name in CONDITION_COMPONENT_COLUMNS:
            result[f"{name.removesuffix('_WEIGHT')}_COVERAGE"][:, physical_zero] = 1.0
    else:
        zero_physical = physical_static <= 0.0
        core_physical_raw[:, zero_physical] = 0.0
        for stream, _component in STREAM_COMPONENTS:
            result[f"{stream}_RAW"][:, zero_physical] = 0.0
    difference = result["PHYSICAL_VIEWABILITY_RAW"] - result["PHYSICAL_VIEWABILITY_CORE_RAW"]
    if np.any(difference[np.isfinite(difference)] > 1e-10):
        raise ValueError("Extended physical opportunity exceeded core opportunity.")
    for name, values in result.items():
        if name.endswith("_COVERAGE"):
            finite = values[np.isfinite(values)]
            if finite.size and ((finite < 0.0).any() or (finite > 1.0).any()):
                raise ValueError(f"{name} is outside [0, 1].")
    return result


def _scale_cap(values: np.ndarray, quantile: float) -> float:
    valid = values[np.isfinite(values) & (values >= 0.0)]
    if not valid.size:
        return 1.0  # numerical divisor only; all unavailable values remain null
    cap = float(np.quantile(np.log1p(valid), quantile))
    if cap == 0:
        return 1.0  # zero-only support is valid, including known disconnections
    if not math.isfinite(cap) or cap < 0:
        raise ValueError("A dynamic stream produced an invalid scale cap.")
    return cap


def _scale(values: np.ndarray, cap: float) -> np.ndarray:
    scaled = np.log1p(np.clip(values, 0.0, None)) / cap
    scaled[~np.isfinite(values)] = np.nan
    return np.clip(scaled, 0.0, 1.0)


def _reference_caps(cfg: LandReportingConfig, basis: DynamicBasis) -> dict[str, float]:
    reference_end = min(
        basis.dates.max(),
        basis.dates.min() + pd.Timedelta(weeks=cfg.dynamic_reference_weeks) - pd.Timedelta(days=1),
    )
    reference_dates = pd.date_range(basis.dates.min(), reference_end, freq="D")
    values: dict[str, list[np.ndarray]] = {stream: [] for stream, _component in STREAM_COMPONENTS}
    for dates in iter_date_chunks(reference_dates, cfg.dynamic_chunk_days):
        chunk = compute_dynamic_chunk(cfg, basis, dates)
        for stream in values:
            values[stream].append(chunk[f"{stream}_RAW"].ravel())
    return {
        stream: _scale_cap(np.concatenate(parts), cfg.scaling_quantile)
        for stream, parts in values.items()
    }


def _daily_status(
    raw: np.ndarray,
    dynamic_coverage: np.ndarray,
    access_fraction: np.ndarray,
    zero_static: np.ndarray,
) -> np.ndarray:
    return np.select(
        [
            ~np.isfinite(access_fraction) | (access_fraction <= 0.0),
            ~np.isfinite(raw) | ~np.isfinite(dynamic_coverage),
            dynamic_coverage < 1.0 - 1e-6,
            zero_static,
        ],
        [
            "unavailable_no_mapped_public_access_context",
            "unavailable_required_dynamic_context",
            "derived_partial_dynamic_support",
            "derived_zero_static_viewability",
        ],
        default="derived_represented_access_scenario",
    )


def _controlled_land_state(
    raw: np.ndarray,
    coverage: np.ndarray,
    *,
    unmapped: np.ndarray | None = None,
) -> np.ndarray:
    """Map a land component into the schema-v2 controlled state vocabulary."""

    states = np.select(
        [
            ~np.isfinite(raw),
            coverage < 1.0 - 1e-6,
            raw > 0.0,
        ],
        [
            ComponentState.UNKNOWN.value,
            ComponentState.PARTIAL.value,
            ComponentState.POSITIVE.value,
        ],
        default=ComponentState.DERIVED_ZERO.value,
    )
    if unmapped is not None:
        states[np.asarray(unmapped, dtype=bool)] = ComponentState.UNMAPPED.value
    return states


def _add_daily_v2_columns(
    frame: pd.DataFrame,
    *,
    lineage: dict[str, Any],
    component_provenance: str = "{}",
    source_coverage_state: str = "partial",
) -> pd.DataFrame:
    """Publish schema-v3 components while retaining v1 compatibility fields."""

    result = frame.copy()
    result["CALENDAR_CONTEXT_WEIGHT"] = result["CALENDAR_EFFORT_WEIGHT"]
    for stream, _component in STREAM_COMPONENTS:
        raw = result[f"{stream}_RAW"].to_numpy(dtype="float64")
        coverage = result[f"{stream}_DYNAMIC_CONTEXT_COVERAGE"].to_numpy(dtype="float64")
        unmapped = None
        if stream in LAND_V2_ALIASES:
            unmapped = (
                result[f"{stream}_STATIC_CONTEXT_FRACTION"].isna().to_numpy()
                | result[f"{stream}_STATIC_CONTEXT_FRACTION"].le(0.0).to_numpy()
            )
        result[f"{stream}_STATE"] = _controlled_land_state(raw, coverage, unmapped=unmapped)
    for legacy, canonical in LAND_V2_ALIASES.items():
        for suffix in (
            "STATIC_RAW",
            "STATIC_INDEX",
            "STATIC_CONTEXT_FRACTION",
            "RAW",
            "INDEX",
            "DYNAMIC_CONTEXT_COVERAGE",
            "STATE",
        ):
            result[f"{canonical}_{suffix}"] = result[f"{legacy}_{suffix}"]
        canonical_unmapped = result[f"{canonical}_STATE"].eq(ComponentState.UNMAPPED.value)
        for suffix in ("STATIC_RAW", "STATIC_INDEX", "RAW", "INDEX"):
            result.loc[canonical_unmapped, f"{canonical}_{suffix}"] = np.nan
        result[f"{canonical}_LOG1P"] = np.log1p(result[f"{canonical}_RAW"].clip(lower=0.0))
        result[f"{canonical}_SPATIAL_RANK"] = result.groupby("DATE", sort=False)[
            f"{canonical}_RAW"
        ].rank(method="average", pct=True)
    condition_coverage = result.get(
        "DYNAMIC_CONDITION_COVERAGE",
        result["PHYSICAL_VIEWABILITY_DYNAMIC_CONTEXT_COVERAGE"],
    ).to_numpy(dtype="float64")
    for value_column, state_column in zip(
        CONDITION_COMPONENT_COLUMNS, CONDITION_STATE_COLUMNS, strict=True
    ):
        if value_column in result:
            values = result[value_column].to_numpy(dtype="float64")
            result[state_column] = _controlled_land_state(
                values,
                result.get(
                    f"{value_column.removesuffix('_WEIGHT')}_COVERAGE",
                    pd.Series(condition_coverage, index=result.index),
                ).to_numpy(dtype="float64"),
            )
    calendar = result["CALENDAR_CONTEXT_WEIGHT"].to_numpy(dtype="float64")
    result["CALENDAR_CONTEXT_STATE"] = _controlled_land_state(
        calendar, np.where(np.isfinite(calendar), 1.0, 0.0)
    )
    if "DISTANCE_DETECTION_WEIGHT" in result:
        distance = result["DISTANCE_DETECTION_WEIGHT"].to_numpy(dtype="float64")
        result["DISTANCE_DETECTION_STATE"] = _controlled_land_state(
            distance, np.where(np.isfinite(distance), 1.0, 0.0)
        )
    result["PUBLIC_SHORE_ACCESS_MAPPED_STATIC_CONTEXT_FRACTION"] = result[
        "LAND_OBSERVATION_OPPORTUNITY_STATIC_CONTEXT_FRACTION"
    ]
    result["PUBLIC_SHORE_ACCESS_VERIFIED_STATIC_CONTEXT_FRACTION"] = result[
        "VERIFIED_LAND_OBSERVATION_OPPORTUNITY_STATIC_CONTEXT_FRACTION"
    ]
    for value_column, state_column in (
        (
            "PUBLIC_SHORE_ACCESS_MAPPED_STATIC_CONTEXT_FRACTION",
            "PUBLIC_SHORE_ACCESS_MAPPED_STATE",
        ),
        (
            "PUBLIC_SHORE_ACCESS_VERIFIED_STATIC_CONTEXT_FRACTION",
            "PUBLIC_SHORE_ACCESS_VERIFIED_STATE",
        ),
    ):
        access = result[value_column].to_numpy(dtype="float64")
        result[state_column] = _controlled_land_state(
            access, np.ones_like(access), unmapped=~np.isfinite(access) | (access <= 0.0)
        )
        result.loc[result[state_column].eq(ComponentState.UNMAPPED.value), value_column] = np.nan
    result["REPORTING_CAPTURE_WEIGHT"] = np.nan
    result["REPORTING_CAPTURE_STATE"] = ComponentState.SOURCE_UNAVAILABLE.value
    result["COMPONENT_PROVENANCE_JSON"] = component_provenance
    result["DATA_COVERAGE_STATE"] = np.where(
        condition_coverage >= 1.0 - 1e-6, "complete", "partial"
    )
    result["SOURCE_COVERAGE_STATE"] = source_coverage_state
    return add_lineage_pandas(result, lineage)


def _write_daily(
    cfg: LandReportingConfig,
    basis: DynamicBasis,
    caps: dict[str, float],
    *,
    overwrite: bool,
    lineage: dict[str, Any],
    component_provenance: str,
) -> tuple[int, dict[str, int]]:
    if cfg.daily_output_path.exists() and not overwrite:
        raise FileExistsError(f"Dynamic daily output exists: {cfg.daily_output_path}")
    cfg.daily_output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cfg.daily_output_path.with_name(
        f".{cfg.daily_output_path.stem}.{os.getpid()}.part{cfg.daily_output_path.suffix}"
    )
    if temporary.exists():
        temporary.unlink()
    writer: pq.ParquetWriter | None = None
    rows = 0
    status_counts: dict[str, int] = {}
    target_count = len(basis.target_order)
    static_arrays = {
        column: basis.target_static[column].to_numpy()
        for column in basis.target_static.columns
        if column != "H3_INDEX"
    }
    static_index_caps = {
        stream: _scale_cap(
            basis.target_static[f"{stream}_STATIC_RAW"].to_numpy(dtype="float64"),
            cfg.scaling_quantile,
        )
        for stream, _component in STREAM_COMPONENTS
    }
    try:
        for dates in iter_date_chunks(basis.dates, cfg.dynamic_chunk_days):
            chunk = compute_dynamic_chunk(cfg, basis, dates)
            date_count = len(dates)
            zero_static = np.tile(
                static_arrays["PHYSICAL_VIEWABILITY_STATIC_RAW"] <= 0.0,
                (date_count, 1),
            )
            access_fraction = np.tile(
                static_arrays[f"{cfg.primary_stream}_STATIC_CONTEXT_FRACTION"],
                (date_count, 1),
            )
            primary_status = _daily_status(
                chunk[f"{cfg.primary_stream}_RAW"],
                chunk[f"{cfg.primary_stream}_DYNAMIC_CONTEXT_COVERAGE"],
                access_fraction,
                zero_static,
            )
            columns: dict[str, Any] = {
                "DATE": np.repeat(chunk["DATE"], target_count),
                "H3_INDEX": np.tile(basis.target_order.to_numpy(), date_count),
                "H3_RESOLUTION": np.int8(cfg.dynamic_output_h3_resolution),
                "TARGET_R7_CHILD_COUNT": np.tile(
                    static_arrays["TARGET_R7_CHILD_COUNT"], date_count
                ).astype("int8"),
                "LAND_SOURCE_COUNT": np.tile(static_arrays["LAND_SOURCE_COUNT"], date_count).astype(
                    "int32"
                ),
                "CALENDAR_EFFORT_WEIGHT": np.repeat(
                    chunk["CALENDAR_EFFORT_WEIGHT"], target_count
                ).astype("float32"),
                "PHYSICAL_VIEWABILITY_CORE_RAW": chunk["PHYSICAL_VIEWABILITY_CORE_RAW"]
                .ravel()
                .astype("float32"),
                "PHYSICAL_VIEWABILITY_CORE_COVERAGE": chunk["PHYSICAL_VIEWABILITY_CORE_COVERAGE"]
                .ravel()
                .astype("float32"),
                "DISTANCE_KM_MEAN": np.tile(static_arrays["DISTANCE_KM_MEAN"], date_count).astype(
                    "float32"
                ),
                "DISTANCE_DETECTION_WEIGHT": np.tile(
                    static_arrays["DISTANCE_DETECTION_WEIGHT"], date_count
                ).astype("float32"),
                "DYNAMIC_CONDITION_COVERAGE": chunk["DYNAMIC_CONDITION_COVERAGE"]
                .ravel()
                .astype("float32"),
            }
            for column in (
                "MODELED_WATER_AREA_M2",
                "ACCESS_MAPPING_COMPLETENESS",
                "GENERAL_LAND_STATIC_VIEWABILITY_RAW",
                "EVALUATED_GEOMETRIC_SUPPORT_FRACTION",
            ):
                if column in static_arrays:
                    columns[column] = np.tile(static_arrays[column], date_count).astype("float64")
            for name, values in static_arrays.items():
                if name.startswith("TARGET_LOCAL_") or name.endswith(
                    "_REGIONAL_INVENTORY_SOURCE_FRACTION"
                ):
                    columns[name] = np.tile(values, date_count)
            for condition_column in CONDITION_COMPONENT_COLUMNS:
                columns[condition_column] = chunk[condition_column].ravel().astype("float32")
                coverage_column = f"{condition_column.removesuffix('_WEIGHT')}_COVERAGE"
                columns[coverage_column] = chunk[coverage_column].ravel().astype("float32")
            if "POPULATION_ORIGIN_EVALUATED_COVERAGE" in static_arrays:
                columns["POPULATION_ORIGIN_EVALUATED_COVERAGE"] = np.tile(
                    static_arrays["POPULATION_ORIGIN_EVALUATED_COVERAGE"], date_count
                ).astype("float32")
            for stream, _component in STREAM_COMPONENTS:
                static_raw = static_arrays[f"{stream}_STATIC_RAW"].astype("float64")
                static_fraction = static_arrays[f"{stream}_STATIC_CONTEXT_FRACTION"].astype(
                    "float64"
                )
                dynamic_raw = chunk[f"{stream}_RAW"]
                dynamic_coverage = chunk[f"{stream}_DYNAMIC_CONTEXT_COVERAGE"]
                columns[f"{stream}_STATIC_RAW"] = np.tile(static_raw, date_count).astype("float32")
                columns[f"{stream}_STATIC_INDEX"] = np.tile(
                    _scale(static_raw, static_index_caps[stream]), date_count
                ).astype("float32")
                columns[f"{stream}_STATIC_CONTEXT_FRACTION"] = np.tile(
                    static_fraction, date_count
                ).astype("float32")
                columns[f"{stream}_RAW"] = dynamic_raw.ravel().astype("float32")
                columns[f"{stream}_INDEX"] = (
                    _scale(dynamic_raw, caps[stream]).ravel().astype("float32")
                )
                columns[f"{stream}_DYNAMIC_CONTEXT_COVERAGE"] = dynamic_coverage.ravel().astype(
                    "float32"
                )
            columns["LAND_EFFORT_PROXY_STATUS"] = primary_status.ravel()
            columns["MEASUREMENT_STATUS"] = np.where(
                np.char.startswith(primary_status.ravel().astype(str), "unavailable"),
                "unavailable",
                "derived",
            )
            frame = _add_daily_v2_columns(
                pd.DataFrame(columns),
                lineage=lineage,
                component_provenance=component_provenance,
                source_coverage_state=cfg.source_completeness,
            )
            validate_product_contract(pl.from_pandas(frame), "land_daily_r6")
            if frame.duplicated(["DATE", "H3_INDEX"]).any():
                raise ValueError("Dynamic daily output chunk contains duplicate keys.")
            table = pa.Table.from_pandas(frame, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(
                    temporary, table.schema, compression="zstd", use_dictionary=True
                )
            writer.write_table(table, row_group_size=len(frame))
            rows += len(frame)
            for status, count in frame["LAND_EFFORT_PROXY_STATUS"].value_counts().items():
                status_counts[str(status)] = status_counts.get(str(status), 0) + int(count)
    finally:
        if writer is not None:
            writer.close()
    expected = len(basis.dates) * target_count
    if rows != expected:
        raise ValueError(f"Expected {expected} dynamic daily rows, wrote {rows}.")
    os.replace(temporary, cfg.daily_output_path)
    if pq.ParquetFile(cfg.daily_output_path).metadata.num_rows != expected:
        raise ValueError("Dynamic daily Parquet row count is incorrect.")
    return rows, status_counts


def aggregate_weekly(daily: pl.LazyFrame) -> pl.DataFrame:
    schema = daily.collect_schema()
    raw_columns = [
        name for name in schema.names() if name.endswith("_RAW") and "_STATIC_" not in name
    ]
    coverage_columns = [name for name in schema.names() if name.endswith("_COVERAGE")]
    static_columns = [
        name
        for name in schema.names()
        if name.endswith("_STATIC_RAW")
        or name == "GENERAL_LAND_STATIC_VIEWABILITY_RAW"
        or name.endswith("_STATIC_INDEX")
        or name.endswith("_STATIC_CONTEXT_FRACTION")
        or name.endswith("_REGIONAL_INVENTORY_SOURCE_FRACTION")
        or name == "TARGET_LOCAL_VERIFIED_REFERENCE_FRACTION"
        or name
        in {
            "TARGET_R7_CHILD_COUNT",
            "LAND_SOURCE_COUNT",
            "MODELED_WATER_AREA_M2",
            "ACCESS_MAPPING_COMPLETENESS",
            "EVALUATED_GEOMETRIC_SUPPORT_FRACTION",
        }
    ]
    mean_columns = [name for name in CONDITION_COMPONENT_COLUMNS if name in schema.names()]
    first_columns = [
        name
        for name in (
            "DISTANCE_KM_MEAN",
            "DISTANCE_DETECTION_WEIGHT",
            "COMPONENT_PROVENANCE_JSON",
            "SOURCE_COVERAGE_STATE",
        )
        if name in schema.names()
    ]
    state_columns = [
        name
        for name in schema.names()
        if name.endswith("_STATE") and name not in {"DATA_COVERAGE_STATE", "SOURCE_COVERAGE_STATE"}
    ]
    expressions: list[pl.Expr] = [
        pl.len().alias("PERIOD_DAY_COUNT"),
        pl.col("CALENDAR_EFFORT_WEIGHT").mean(),
        *[
            pl.col(column).first().alias(column)
            for column in LINEAGE_COLUMNS
            if column in schema.names()
        ],
        *[pl.col(column).first().alias(column) for column in static_columns],
        *[pl.col(column).first().alias(column) for column in first_columns],
        *[pl.col(column).mean().alias(column) for column in mean_columns],
        *[pl.col(column).mean().alias(column) for column in coverage_columns],
    ]
    if "DATA_COVERAGE_STATE" in schema.names():
        expressions.append(
            pl.when(pl.col("DATA_COVERAGE_STATE").eq("complete").all())
            .then(pl.lit("complete"))
            .otherwise(pl.lit("partial"))
            .alias("DATA_COVERAGE_STATE")
        )
    expressions.extend(
        pl.when(pl.col(column).eq(ComponentState.PROCESSING_FAILURE.value).any())
        .then(pl.lit(ComponentState.PROCESSING_FAILURE.value))
        .when(
            (pl.col(column).n_unique() == 1)
            & pl.col(column)
            .first()
            .is_in(
                [
                    ComponentState.UNKNOWN.value,
                    ComponentState.UNMAPPED.value,
                    ComponentState.SOURCE_UNAVAILABLE.value,
                    ComponentState.NOT_APPLICABLE.value,
                    ComponentState.OUTSIDE_JURISDICTION.value,
                ]
            )
        )
        .then(pl.col(column).first())
        .when(pl.len() < 7)
        .then(pl.lit(ComponentState.PARTIAL.value))
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
    for column in raw_columns:
        expressions.extend(
            [
                pl.when(pl.col(column).count() > 0)
                .then(pl.col(column).sum())
                .otherwise(pl.lit(None, dtype=pl.Float64))
                .alias(column),
                pl.col(column).count().alias(f"{column.removesuffix('_RAW')}_AVAILABLE_DAY_COUNT"),
                pl.col(column).mean().alias(f"{column.removesuffix('_RAW')}_AVAILABLE_DAY_MEAN"),
                pl.when(pl.col(column).count() > 0)
                .then(pl.col(column).sum())
                .otherwise(pl.lit(None, dtype=pl.Float64))
                .alias(f"{column.removesuffix('_RAW')}_AVAILABLE_DAY_SUM"),
            ]
        )
    return (
        daily.with_columns(pl.col("DATE").dt.truncate("1w").alias("WEEK_START"))
        .group_by("WEEK_START", "H3_INDEX", "H3_RESOLUTION")
        .agg(expressions)
        .sort("WEEK_START", "H3_INDEX")
        .collect()
        .with_columns(
            (pl.col("PERIOD_DAY_COUNT") < 7).alias("INCOMPLETE_WEEK"),
            pl.lit(7).alias("EXPECTED_DAY_COUNT"),
        )
    )


def _write_weekly(
    cfg: LandReportingConfig,
    basis: DynamicBasis,
    *,
    overwrite: bool,
    lineage: dict[str, Any],
) -> tuple[int, dict[str, float]]:
    if cfg.weekly_output_path.exists() and not overwrite:
        raise FileExistsError(f"Dynamic weekly output exists: {cfg.weekly_output_path}")
    weekly = aggregate_weekly(pl.scan_parquet(cfg.daily_output_path))
    raw_columns = [f"{stream}_RAW" for stream, _component in STREAM_COMPONENTS]
    first_complete_week = (
        weekly.filter(pl.col("PERIOD_DAY_COUNT") == 7).select(pl.col("WEEK_START").min()).item()
    )
    has_complete_week = first_complete_week is not None
    if first_complete_week is None:
        first_complete_week = weekly.get_column("WEEK_START").min()
    reference_end = first_complete_week + pd.Timedelta(weeks=cfg.dynamic_reference_weeks - 1)
    reference = weekly.filter(
        ((pl.col("PERIOD_DAY_COUNT") == 7) if has_complete_week else pl.lit(True))
        & pl.col("WEEK_START").is_between(first_complete_week, reference_end)
    )
    caps: dict[str, float] = {}
    index_expressions: list[pl.Expr] = []
    for stream, _component in STREAM_COMPONENTS:
        raw_column = f"{stream}_RAW"
        values = reference.get_column(raw_column).to_numpy()
        cap = _scale_cap(values.astype("float64"), cfg.scaling_quantile)
        caps[stream] = cap
        index_expressions.append(
            (pl.col(raw_column).clip(lower_bound=0.0).log1p() / cap)
            .clip(0.0, 1.0)
            .alias(f"{stream}_INDEX")
        )
    weekly = weekly.with_columns(index_expressions).with_columns(
        pl.when(pl.col("PHYSICAL_VIEWABILITY_STATIC_INDEX") <= 0.0)
        .then(pl.lit("derived_zero_static_viewability"))
        .when(
            pl.col(f"{cfg.primary_stream}_STATIC_CONTEXT_FRACTION").is_null()
            | (pl.col(f"{cfg.primary_stream}_STATIC_CONTEXT_FRACTION") <= 0.0)
        )
        .then(pl.lit("unavailable_no_mapped_public_access_context"))
        .when(pl.col(f"{cfg.primary_stream}_RAW").is_null())
        .then(pl.lit("unavailable_required_dynamic_context"))
        .when(pl.col(f"{cfg.primary_stream}_AVAILABLE_DAY_COUNT") < pl.col("PERIOD_DAY_COUNT"))
        .then(pl.lit("derived_partial_required_dynamic_context"))
        .when(pl.col("PERIOD_DAY_COUNT") < 7)
        .then(pl.lit("derived_partial_calendar_week"))
        .otherwise(pl.lit("derived_represented_access_scenario"))
        .alias("LAND_EFFORT_PROXY_STATUS"),
        pl.when(pl.col(f"{cfg.primary_stream}_RAW").is_null())
        .then(pl.lit("unavailable"))
        .otherwise(pl.lit("derived"))
        .alias("MEASUREMENT_STATUS"),
    )
    weekly = weekly.with_columns(
        pl.col("CALENDAR_EFFORT_WEIGHT").alias("CALENDAR_CONTEXT_WEIGHT"),
        pl.lit(None, dtype=pl.Float32).alias("REPORTING_CAPTURE_WEIGHT"),
        pl.lit(ComponentState.SOURCE_UNAVAILABLE.value).alias("REPORTING_CAPTURE_STATE"),
    )
    for stream, _component in STREAM_COMPONENTS:
        raw_column = f"{stream}_RAW"
        available_days = f"{stream}_AVAILABLE_DAY_COUNT"
        static_fraction = f"{stream}_STATIC_CONTEXT_FRACTION"
        state = pl.when(pl.col(f"{stream}_STATE").eq(ComponentState.PROCESSING_FAILURE.value)).then(
            pl.lit(ComponentState.PROCESSING_FAILURE.value)
        )
        if stream in LAND_V2_ALIASES:
            state = state.when(
                pl.col(static_fraction).is_null() | (pl.col(static_fraction) <= 0.0)
            ).then(pl.lit(ComponentState.UNMAPPED.value))
        state = (
            state.when(pl.col(raw_column).is_null())
            .then(pl.lit(ComponentState.UNKNOWN.value))
            .when(
                (pl.col(available_days) < 7)
                | pl.col(f"{stream}_STATE").eq(ComponentState.PARTIAL.value)
            )
            .then(pl.lit(ComponentState.PARTIAL.value))
            .when(pl.col(raw_column) > 0.0)
            .then(pl.lit(ComponentState.POSITIVE.value))
            .otherwise(pl.lit(ComponentState.DERIVED_ZERO.value))
            .alias(f"{stream}_STATE")
        )
        weekly = weekly.with_columns(state)
    for legacy, canonical in LAND_V2_ALIASES.items():
        weekly = weekly.with_columns(pl.col(f"{legacy}_STATE").alias(f"{canonical}_STATE"))
    for stream, _component in STREAM_COMPONENTS:
        weekly = weekly.with_columns(
            pl.col(f"{stream}_STATE").alias(f"{stream}_AVAILABLE_DAY_SUM_STATE")
        )
    for legacy, canonical in LAND_V2_ALIASES.items():
        weekly = weekly.with_columns(
            pl.col(f"{canonical}_STATE").alias(f"{canonical}_AVAILABLE_DAY_SUM_STATE"),
            pl.when(pl.col(f"{canonical}_STATE") == ComponentState.UNMAPPED.value)
            .then(pl.lit(None, dtype=pl.Float64))
            .otherwise(pl.col(f"{legacy}_INDEX"))
            .alias(f"{canonical}_INDEX"),
            pl.col(f"{canonical}_RAW").clip(lower_bound=0.0).log1p().alias(f"{canonical}_LOG1P"),
            (
                pl.col(f"{canonical}_RAW").rank(method="average").over("WEEK_START")
                / pl.col(f"{canonical}_RAW").count().over("WEEK_START")
            ).alias(f"{canonical}_SPATIAL_RANK"),
        )
    weekly = weekly.with_columns(
        pl.when(pl.col(f"{cfg.primary_stream}_STATE") == ComponentState.PARTIAL.value)
        .then(pl.lit("derived_partial_evaluated_support"))
        .otherwise(pl.col("LAND_EFFORT_PROXY_STATUS"))
        .alias("LAND_EFFORT_PROXY_STATUS")
    )
    for column, value in lineage.items():
        if column not in weekly.columns:
            weekly = weekly.with_columns(pl.lit(value).alias(column))
    validate_product_contract(weekly, "land_weekly_r6")
    cfg.weekly_output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cfg.weekly_output_path.with_name(
        f".{cfg.weekly_output_path.stem}.{os.getpid()}.part{cfg.weekly_output_path.suffix}"
    )
    weekly.write_parquet(temporary, compression="zstd", statistics=True)
    os.replace(temporary, cfg.weekly_output_path)
    return weekly.height, caps


def build_dynamic_products(
    cfg: LandReportingConfig,
    static: pd.DataFrame,
    source: pd.DataFrame,
    *,
    overwrite: bool,
    routing_policy: str = "legacy_routing_unverified",
    access_kernel: pd.DataFrame | None = None,
    target_support: pd.DataFrame | None = None,
) -> DynamicBuildResult:
    basis = prepare_dynamic_basis(cfg, static, source, target_support)
    if access_kernel is not None:
        from .conditioned_basis import condition_basis

        basis = condition_basis(cfg, basis, source, access_kernel, target_support)
    manifests = {
        "surface_weather": json.loads(cfg.surface_weather_manifest_path.read_text()),
        "daylight": json.loads(cfg.daylight_manifest_path.read_text()),
        "calendar": json.loads(cfg.calendar_manifest_path.read_text()),
        "public_access": json.loads(cfg.public_shore_manifest_path.read_text()),
        "population_travel": json.loads(cfg.population_travel_manifest_path.read_text()),
        "transport": json.loads(cfg.transport_manifest_path.read_text()),
    }
    lineage = lineage_values(
        generation_id=cfg.daily_output_path.parent.name,
        config_hash=cfg.human.config_hash,
        source_hashes={
            "static_viewshed": sha256_file(cfg.static_weights_path),
            "static_viewshed_metadata": sha256_file(
                cfg.static_weights_path.with_name(f"{cfg.static_weights_path.stem}_metadata.json")
            ),
            "land_source_components": sha256_file(cfg.source_output_path),
            **(
                {
                    role: sha256_file(cfg.source_output_path.parent / f"{role}.parquet")
                    for role in (
                        "observation_sites",
                        "observer_samples",
                        "access_kernel",
                        "target_water_support",
                    )
                }
                if access_kernel is not None
                else {}
            ),
            "public_access_manifest": sha256_file(cfg.public_shore_manifest_path),
            "input_inventory_manifest": sha256_file(cfg.raw_manifest_path),
            "surface_weather_manifest": sha256_file(cfg.surface_weather_manifest_path),
            "daylight_manifest": sha256_file(cfg.daylight_manifest_path),
            "calendar_manifest": sha256_file(cfg.calendar_manifest_path),
        },
        source_vintages={
            name: payload.get("temporal_coverage", payload.get("date_coverage", {}))
            for name, payload in manifests.items()
        },
    )
    component_provenance = component_provenance_json(
        {
            "static_viewshed": {
                "source_vintage": "viewshed_generation",
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": True,
                "schema_version": "viewshed_static_pair_v2",
            },
            "surface_weather": {
                "source_vintage": manifests["surface_weather"].get("temporal_coverage", {}),
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": False,
            },
            "daylight": {
                "source_vintage": manifests["daylight"].get("temporal_coverage", {}),
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": False,
            },
            "calendar_context": {
                "source_vintage": manifests["calendar"].get("temporal_coverage", {}),
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": False,
            },
            "population_and_access": {
                "source_vintage": "see SOURCE_VINTAGES_JSON and upstream manifests",
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": True,
            },
        }
    )
    daily_caps = _reference_caps(cfg, basis)
    daily_rows, daily_status_counts = _write_daily(
        cfg,
        basis,
        daily_caps,
        overwrite=overwrite,
        lineage=lineage,
        component_provenance=component_provenance,
    )
    weekly_rows, weekly_caps = _write_weekly(cfg, basis, overwrite=overwrite, lineage=lineage)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "land_product_schema_version": "4.1.0-research" if access_kernel is not None else None,
        "product": "human.land_reporting_opportunity.dynamic_h3_r6",
        "status": "research_access_conditioned_scenarios",
        "routing_policy": routing_policy,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "date_coverage": {
            "start_date": basis.dates.min().date().isoformat(),
            "end_date": basis.dates.max().date().isoformat(),
            "dates": len(basis.dates),
        },
        "spatial_support": {
            "native_source_resolution": 7,
            "native_target_resolution": 7,
            "output_target_resolution": cfg.dynamic_output_h3_resolution,
            "target_cells": len(basis.target_order),
            "native_pairs": basis.native_pairs,
            "grouped_basis_rows": basis.grouped_rows,
            "target_aggregation": (
                "water-area average over canonical logical R7 children"
                if target_support is not None
                else "legacy equal-child mean"
            ),
            "date_boundary": "Source-local calendar dates; Monday-start local weeks",
            "calendar_timezone": manifests["surface_weather"]
            .get("resolved_config", {})
            .get("timezone", "UTC"),
            "access_mapping_completeness": "unknown",
            "condition_diagnostics": "available-support mean over positive physical-kernel bins; irrelevant zero bins excluded; independent component coverage",
            "support_contracts": {
                "geometry": "all planned samples complete; full canonical water includes evaluated zeros; incomplete certificates rejected",
                "required_weather": "positive physical kernel and nonzero or unresolved activity; known zero needs no weather",
                "zero_required_weather": "coverage 1 means no unmet requirements; local condition diagnostics remain null when no contributing support",
                "partial_raw": "sum of available contributions, never divided by coverage",
            },
            "access_kernel_algorithm": (
                "source_cell_radius_reference_kernel_v3" if access_kernel is not None else None
            ),
            "distance_bin_km": cfg.dynamic_distance_bin_km,
        },
        "dynamic_input_coverage": {
            "required_weather_h3_r5_cells": len(basis.weather_bases),
            "missing_weather_h3_r5_cells": sum(
                item["weather_h3_r5"] not in basis.weather_daily for item in basis.weather_bases
            ),
            "missing_weather_h3_r5_ids": [
                item["weather_h3_r5"]
                for item in basis.weather_bases
                if item["weather_h3_r5"] not in basis.weather_daily
            ],
            "daylight_supplement": (
                str(cfg.daily_output_path.parent / "daylight_support_supplement.parquet")
                if (cfg.daily_output_path.parent / "daylight_support_supplement.parquet").exists()
                else None
            ),
            "missing_daylight_h3_r4_mappings": sum(
                item["daylight_h3_r4"] not in basis.daylight_daily for item in basis.weather_bases
            ),
        },
        "formula": {
            "version": cfg.composite_formula_version,
            "primary_stream": cfg.primary_stream,
            "calendar_applied_streams": list(cfg.calendar_applied_streams),
            "include_potentially_endogenous": cfg.include_potentially_endogenous,
            "physical": "static viewshed * distance-aware visibility * daylight * wind * precipitation",
            "reachable_population": "sum origin population * exp(-travel minutes / decay)",
            "broad_reachability": "physical * population-travel opportunity * road proximity * calendar",
            "primary_observation_opportunity": "sum source activity * site allocation * sample allocation * joint LOS/distance/daily-condition kernel, water-area averaged over logical R7 children",
            "verified_scenario": "source budget allocated among represented authoritative verified-public sites",
        },
        "streams": {
            stream: {"source_component": component, "published_standalone": True}
            for stream, component in STREAM_COMPONENTS
        },
        "primary_observation_opportunity": {
            "raw_column": "LAND_OBSERVATION_OPPORTUNITY_RAW",
            "index_column": "LAND_OBSERVATION_OPPORTUNITY_INDEX",
            "status_column": "LAND_OBSERVATION_OPPORTUNITY_STATE",
            "interpretation": "relative observation opportunity at represented mapped-public access sites",
        },
        "deprecated_compatibility_aliases": {
            "LAND_EFFORT_PROXY_*": "LAND_OBSERVATION_OPPORTUNITY_*",
            "VERIFIED_LAND_EFFORT_PROXY_*": "VERIFIED_LAND_OBSERVATION_OPPORTUNITY_*",
            "CALENDAR_EFFORT_WEIGHT": "CALENDAR_CONTEXT_WEIGHT",
        },
        "lineage": lineage,
        "component_provenance": json.loads(component_provenance),
        "scaling": {
            "degenerate_reference_policy": "unit divisor when reference q99 is zero or unavailable; nulls remain null",
            "formula": "clip(log1p(raw) / q99 fixed-reference log1p raw, 0, 1)",
            "quantile": cfg.scaling_quantile,
            "reference_weeks": cfg.dynamic_reference_weeks,
            "daily_log1p_caps": daily_caps,
            "weekly_log1p_caps": weekly_caps,
        },
        "inputs": [
            {"path": str(cfg.static_weights_path), "sha256": sha256_file(cfg.static_weights_path)},
            {"path": str(cfg.source_output_path), "sha256": sha256_file(cfg.source_output_path)},
            {
                "path": str(cfg.surface_weather_manifest_path),
                "sha256": sha256_file(cfg.surface_weather_manifest_path),
            },
            {
                "path": str(cfg.daylight_manifest_path),
                "sha256": sha256_file(cfg.daylight_manifest_path),
            },
            {"path": str(cfg.calendar_path), "sha256": sha256_file(cfg.calendar_path)},
            {
                "path": str(cfg.calendar_manifest_path),
                "sha256": sha256_file(cfg.calendar_manifest_path),
            },
        ],
        "artifacts": [
            {
                "path": str(cfg.daily_output_path),
                "sha256": sha256_file(cfg.daily_output_path),
                "rows": daily_rows,
            },
            {
                "path": str(cfg.weekly_output_path),
                "sha256": sha256_file(cfg.weekly_output_path),
                "rows": weekly_rows,
            },
        ],
        "daily_primary_status_counts": daily_status_counts,
        "sightings_used_to_construct_or_scale_proxy": False,
        "measurement_contract": {
            "direct_observer_effort_available": False,
            "unknown_access_is_zero": False,
            "unknown_access_is_not_extrapolated": True,
            "components_published_standalone": True,
            "disturbance_combined_with_reporting_opportunity": False,
        },
        "known_limitations": [
            "The composite is relative reporting opportunity, not observed observer-hours or detection probability.",
            "Mapped and verified access are scenarios, not uncertainty bounds; unknown access is not asserted to be zero.",
            f"Routing policy: {routing_policy}; static routing is contemporary context, not historical travel behavior.",
            "Daily weather summaries do not identify conditions during actual viewing hours.",
            "Wind and precipitation are viewing-condition proxies; Beaufort state, swell, observer skill, and equipment are unmeasured.",
            "Calendar multipliers and component response curves are uncalibrated research assumptions.",
        ],
    }
    atomic_write_json(cfg.dynamic_metadata_path, metadata, overwrite=True)
    return DynamicBuildResult(
        daily_path=cfg.daily_output_path,
        weekly_path=cfg.weekly_output_path,
        metadata_path=cfg.dynamic_metadata_path,
        daily_rows=daily_rows,
        weekly_rows=weekly_rows,
        start_date=basis.dates.min().date().isoformat(),
        end_date=basis.dates.max().date().isoformat(),
        target_cells=len(basis.target_order),
    )
