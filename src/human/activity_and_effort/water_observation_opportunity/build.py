"""Build schema-v3 component-first water observation-opportunity products."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Sequence

import h3
import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from human.activity_and_effort.observation_opportunity_contract import (
    SCHEMA_VERSION,
    STATIC_PAIR_CANONICAL_MAPPING,
    ComponentState,
    ProductRole,
    add_lineage_pandas,
    component_provenance_json,
    lineage_values,
    static_geometry_arrow_schema,
    static_pair_arrow_schema,
    validate_component_table,
)
from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_json,
    load_manifest,
    manifest_payload,
    sha256_file,
)
from human.viewshed.config import load_app_config
from human.viewshed.config.distance import load_distance_weight_config
from human.viewshed.finalize.final_artifacts import (
    static_scientific_config_hash,
    validate_static_artifact_metadata,
)
from human.viewshed.weights.distance.compute import distance_weight_values
from human.utils.release_inputs import resolve_sightings_release_artifact

from .config import DEFAULT_CONFIG_PATH, WaterObservationConfig, load_water_observation_config
from .generations import new_generation, publish
from .pipeline import (
    AIS_ACTIVITY_COLUMNS,
    AIS_TARGET_COLUMNS,
    COMMERCIAL_INPUT_COLUMNS,
    FERRY_TARGET_COLUMNS,
    KernelBasis,
    add_distance_bins,
    add_transformations,
    aggregate_weekly,
    apply_coverage_semantics,
    condition_arrays,
    haversine_km,
    kernel_basis,
    propagate_component,
    propagated_condition_summaries,
    static_distance_summaries,
)

SOURCE_CONDITION_COLUMNS = [
    "VISIBILITY_KM",
    "DAYLIGHT_WEIGHT",
    "WIND_WEIGHT",
    "PRECIPITATION_WEIGHT",
    "DYNAMIC_CONDITION_COVERAGE",
]
TARGET_CONDITION_COLUMNS = [
    "ATMOSPHERIC_VISIBILITY_WEIGHT",
    "DAYLIGHT_WEIGHT",
    "WIND_WEIGHT",
    "PRECIPITATION_WEIGHT",
    "DYNAMIC_CONDITION_COVERAGE",
]


def _atomic_parquet(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        frame.write_parquet(temporary, compression="zstd", statistics=True)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _append_table(
    writer: pq.ParquetWriter | None,
    frame: pd.DataFrame,
    path: Path,
) -> pq.ParquetWriter:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    if writer is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = pq.ParquetWriter(path, table.schema, compression="zstd", use_dictionary=True)
    elif writer.schema != table.schema:
        table = table.cast(writer.schema)
    writer.write_table(table, row_group_size=min(len(frame), 500_000))
    return writer


def _pivot(
    frame: pd.DataFrame,
    *,
    dates: pd.DatetimeIndex,
    cells: pd.Index,
    value: str,
    date_column: str = "DATE",
    cell_column: str = "H3_INDEX",
) -> np.ndarray:
    if frame.empty:
        return np.full((len(dates), len(cells)), np.nan, dtype="float64")
    work = frame[[date_column, cell_column, value]].copy()
    work[date_column] = pd.to_datetime(work[date_column], errors="coerce").dt.normalize()
    if work.duplicated([date_column, cell_column]).any():
        raise ValueError(f"{value} is not unique by {date_column} and {cell_column}.")
    return (
        work.pivot(index=date_column, columns=cell_column, values=value)
        .reindex(index=dates, columns=cells)
        .to_numpy(dtype="float64")
    )


def _read_conditions(
    cfg: WaterObservationConfig,
    *,
    source_cells: pd.Index,
    dates: pd.DatetimeIndex,
) -> dict[str, np.ndarray]:
    weather_parents = pd.Index([h3.cell_to_parent(str(cell), 5) for cell in source_cells])
    daylight_parents = pd.Index([h3.cell_to_parent(str(cell), 4) for cell in source_cells])
    minimum = dates.min().date()
    maximum = dates.max().date()
    weather = (
        pl.scan_parquet(cfg.surface_weather_path)
        .filter(
            pl.col("H3_INDEX").is_in(weather_parents.unique().tolist())
            & pl.col("DATE").str.to_date().is_between(minimum, maximum)
        )
        .select(
            "H3_INDEX",
            "DATE",
            "VISIBILITY_KM_MEAN",
            "WIND_SPEED_10M_MS_MEAN",
            "PRECIP_MM_DAY_ESTIMATE",
            "SAMPLE_COVERAGE_FRAC",
            "QC_STATE",
        )
        .collect(engine="streaming")
        .to_pandas()
    )
    daylight = (
        pl.scan_parquet(cfg.daylight_path)
        .filter(
            pl.col("H3_INDEX").is_in(daylight_parents.unique().tolist())
            & pl.col("DATE").str.to_date().is_between(minimum, maximum)
        )
        .select("H3_INDEX", "DATE", "DAYLIGHT_FRACTION")
        .collect(engine="streaming")
        .to_pandas()
    )
    weather_cells = weather_parents.unique()
    daylight_cells = daylight_parents.unique()
    visibility_by_parent = _pivot(
        weather, dates=dates, cells=weather_cells, value="VISIBILITY_KM_MEAN"
    )
    wind_by_parent = _pivot(
        weather, dates=dates, cells=weather_cells, value="WIND_SPEED_10M_MS_MEAN"
    )
    precipitation_by_parent = _pivot(
        weather, dates=dates, cells=weather_cells, value="PRECIP_MM_DAY_ESTIMATE"
    )
    coverage_by_parent = _pivot(
        weather, dates=dates, cells=weather_cells, value="SAMPLE_COVERAGE_FRAC"
    )
    qc = weather.assign(_QC_COMPLETE=weather["QC_STATE"].eq("COMPLETE"))
    qc_by_parent = _pivot(qc, dates=dates, cells=weather_cells, value="_QC_COMPLETE")
    daylight_by_parent = _pivot(
        daylight, dates=dates, cells=daylight_cells, value="DAYLIGHT_FRACTION"
    )
    weather_codes = weather_cells.get_indexer(weather_parents)
    daylight_codes = daylight_cells.get_indexer(daylight_parents)
    return condition_arrays(
        visibility_km=visibility_by_parent[:, weather_codes],
        daylight_fraction=daylight_by_parent[:, daylight_codes],
        wind_speed_ms=wind_by_parent[:, weather_codes],
        precipitation_mm_day=precipitation_by_parent[:, weather_codes],
        weather_complete=(coverage_by_parent[:, weather_codes] == 1.0)
        & (qc_by_parent[:, weather_codes] == 1.0),
        wind_midpoint_ms=cfg.wind_support_midpoint_ms,
        wind_slope_ms=cfg.wind_support_slope_ms,
        precipitation_half_mm_day=cfg.precipitation_support_half_mm_day,
    )


def _read_ais_year(
    cfg: WaterObservationConfig,
    *,
    source_cells: pd.Index,
    dates: pd.DatetimeIndex,
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, list[str]]:
    required = {
        *AIS_ACTIVITY_COLUMNS.values(),
        *COMMERCIAL_INPUT_COLUMNS,
        "DATE",
        "H3_INDEX",
        "SOURCE_TEMPORAL_COVERAGE_FRACTION",
        "SOURCE_COVERAGE_COMPLETE",
        "SOURCE_COVERAGE_STATUS",
    }
    scan = pl.scan_parquet(cfg.ais_daily_path)
    missing = sorted(required.difference(scan.collect_schema().names()))
    if missing:
        raise ValueError(f"AIS daily artifact is missing columns: {missing}")
    frame = (
        scan.filter(
            pl.col("H3_INDEX").is_in(source_cells.tolist())
            & pl.col("DATE").is_between(dates.min().date(), dates.max().date())
        )
        .select(sorted(required))
        .with_columns(
            pl.sum_horizontal(*[pl.col(name) for name in COMMERCIAL_INPUT_COLUMNS]).alias(
                "COMMERCIAL_AIS_ACTIVITY_HOURS_PROXY"
            )
        )
        .collect(engine="streaming")
        .to_pandas()
    )
    activity = {
        output: _pivot(frame, dates=dates, cells=source_cells, value=input_name)
        for output, input_name in AIS_ACTIVITY_COLUMNS.items()
    }
    activity["COMMERCIAL_AIS_ACTIVITY_HOURS_PROXY"] = _pivot(
        frame,
        dates=dates,
        cells=source_cells,
        value="COMMERCIAL_AIS_ACTIVITY_HOURS_PROXY",
    )
    coverage = (
        frame.groupby("DATE", sort=True)["SOURCE_TEMPORAL_COVERAGE_FRACTION"]
        .max()
        .reindex(dates.date)
        .to_numpy(dtype="float64")
    )
    complete = (
        frame.groupby("DATE", sort=True)["SOURCE_COVERAGE_COMPLETE"]
        .all()
        .reindex(dates.date)
        .fillna(False)
        .to_numpy(dtype=bool)
    )
    statuses = (
        frame.groupby("DATE", sort=True)["SOURCE_COVERAGE_STATUS"]
        .first()
        .reindex(dates.date)
        .fillna("unknown")
        .astype(str)
        .tolist()
    )
    return activity, coverage, complete, statuses


def _load_ferry_daily(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    required = {"service_date", "source_h3", "ferry_rider_hours", "ferry_vessel_hours"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Ferry daily artifact is missing columns: {missing}")
    frame = frame[list(required)].copy()
    frame["service_date"] = pd.to_datetime(frame["service_date"], errors="coerce").dt.normalize()
    frame["source_h3"] = frame["source_h3"].astype(str)
    return frame


def _read_ferry_arrays(
    ferry: pd.DataFrame,
    *,
    source_cells: pd.Index,
    dates: pd.DatetimeIndex,
) -> dict[str, np.ndarray]:
    selected = ferry[
        ferry["source_h3"].isin(source_cells)
        & ferry["service_date"].between(dates.min(), dates.max())
    ]
    if selected.duplicated(["service_date", "source_h3"]).any():
        selected = selected.groupby(["service_date", "source_h3"], as_index=False).agg(
            ferry_rider_hours=("ferry_rider_hours", lambda x: x.sum(min_count=1)),
            ferry_vessel_hours=("ferry_vessel_hours", lambda x: x.sum(min_count=1)),
        )
    return {
        "FERRY_RIDER_HOURS": _pivot(
            selected,
            dates=dates,
            cells=source_cells,
            value="ferry_rider_hours",
            date_column="service_date",
            cell_column="source_h3",
        ),
        "FERRY_PLATFORM_HOURS": _pivot(
            selected,
            dates=dates,
            cells=source_cells,
            value="ferry_vessel_hours",
            date_column="service_date",
            cell_column="source_h3",
        ),
    }


def _canonical_water_support(cfg: WaterObservationConfig) -> pd.DataFrame:
    """Inventory every water pixel independently of the sparse pair table."""
    from collections import Counter

    import rasterio
    from pyproj import Transformer

    from human.viewshed.prepare.area.raster_stack import (
        ensure_canonical_raster_stack,
    )

    app = load_app_config(cfg.viewshed_config_path)
    stack = ensure_canonical_raster_stack(app, include_canopy=False)
    counts = Counter()
    with rasterio.open(stack.water_mask_path) as water:
        if not water.crs.is_projected:
            raise ValueError("Canonical water-area raster must use a metric projected CRS")
        converter = Transformer.from_crs(water.crs, 4326, always_xy=True)
        area = abs(water.transform.a * water.transform.e - water.transform.b * water.transform.d)
        for _, window in water.block_windows(1):
            rows, cols = np.nonzero(water.read(1, window=window) == 1)
            xs, ys = rasterio.transform.xy(water.window_transform(window), rows, cols)
            lon, lat = converter.transform(xs, ys)
            counts.update(h3.latlng_to_cell(y, x, 7) for x, y in zip(lon, lat, strict=True))
    return pd.DataFrame(
        {"target_h3": list(counts), "target_water_area_m2": [n * area for n in counts.values()]}
    )


def _r6_basis(
    cfg: WaterObservationConfig, support: pd.DataFrame | None = None
) -> tuple[KernelBasis, dict[str, Any]]:
    from .pipeline import area_weighted_parent_kernel

    support = _canonical_water_support(cfg) if support is None else support
    static = pl.read_parquet(
        cfg.water_static_weights_path,
        columns=["source_h3", "target_h3", "weight_static_viewability"],
    )
    kernel = area_weighted_parent_kernel(
        static, support, distance_bin_km=cfg.distance_bin_km, aggregate_sources=True
    )
    return kernel_basis(
        kernel,
        source_column="source_h3",
        target_column="H3_INDEX",
        weight_column="weight",
        distance_bin_km=cfg.distance_bin_km,
    ), {
        "algorithm": "fine_distance_canonical_water_area_v1",
        "source_allocation": "uniform_over_modeled_R7_children_of_R6_total",
        "target_quantity": "canonical_modeled_water_area_average",
        "canonical_water_children": len(support),
    }


def _r7_source_order(cfg: WaterObservationConfig) -> pd.Index:
    """Return the canonical water observer/source universe at native H3 R7."""

    return pd.Index(
        pl.scan_parquet(cfg.water_static_weights_path)
        .select("source_h3")
        .unique()
        .sort("source_h3")
        .collect(engine="streaming")
        .get_column("source_h3")
        .to_list()
    )


def _expand_ais_to_r7_sources(
    activity: dict[str, np.ndarray],
    *,
    r6_sources: pd.Index,
    r7_sources: pd.Index,
) -> dict[str, np.ndarray]:
    """Conservatively distribute R6 AIS activity over modeled R7 source children."""

    r7_parents = pd.Index([h3.cell_to_parent(str(cell), 6) for cell in r7_sources])
    parent_codes = r6_sources.get_indexer(r7_parents)
    if (parent_codes < 0).any():
        missing = sorted(set(r7_parents[parent_codes < 0]))
        raise ValueError(f"R7 water sources have no modeled AIS R6 parent: {missing[:5]}")
    parent_counts = pd.Series(r7_parents).value_counts()
    divisors = r7_parents.map(parent_counts).to_numpy(dtype="float64")
    return {
        name: values[:, parent_codes] / divisors[np.newaxis, :] for name, values in activity.items()
    }


def _ferry_basis(
    cfg: WaterObservationConfig,
    *,
    ferry_source_cells: set[str],
    support: pd.DataFrame | None = None,
) -> KernelBasis | None:
    from .pipeline import area_weighted_parent_kernel

    support = _canonical_water_support(cfg) if support is None else support
    static = pl.read_parquet(
        cfg.water_static_weights_path,
        columns=["source_h3", "target_h3", "weight_static_viewability"],
    ).filter(pl.col("source_h3").is_in(sorted(ferry_source_cells)))
    if static.is_empty():
        return None
    kernel = area_weighted_parent_kernel(
        static, support, distance_bin_km=cfg.distance_bin_km, aggregate_sources=False
    )
    return kernel_basis(
        kernel,
        source_column="source_h3",
        target_column="H3_INDEX",
        weight_column="weight",
        distance_bin_km=cfg.distance_bin_km,
    )


def _propagate(
    cfg: WaterObservationConfig,
    activity: np.ndarray,
    conditions: dict[str, np.ndarray],
    basis: KernelBasis,
) -> np.ndarray:
    return propagate_component(
        activity,
        conditions,
        basis,
        visibility_transition_fraction=cfg.visibility_transition_fraction,
        visibility_minimum_transition_km=cfg.visibility_minimum_transition_km,
        wind_exponent=cfg.conditions_wind_exponent,
        precipitation_exponent=cfg.conditions_precipitation_exponent,
        return_coverage=True,
    )


def _source_year_frame(
    cfg: WaterObservationConfig,
    dates: pd.DatetimeIndex,
    source_order: pd.Index,
    ais_activity: dict[str, np.ndarray],
    ais_coverage: np.ndarray,
    ais_complete: np.ndarray,
    ais_status: list[str],
    conditions: dict[str, np.ndarray],
    ferry_r7: dict[str, np.ndarray],
    lineage: dict[str, Any],
    component_provenance: str,
) -> pd.DataFrame:
    date_count = len(dates)
    source_count = len(source_order)
    spatial_complete = cfg.ais_spatial_coverage_state == "complete"
    effective_complete = ais_complete & spatial_complete
    source_coverage_state = np.where(
        np.asarray(ais_coverage) > 0.0, ComponentState.PARTIAL.value, ComponentState.UNKNOWN.value
    )
    columns: dict[str, Any] = {
        "DATE": np.repeat(dates.to_numpy(dtype="datetime64[ns]"), source_count),
        "H3_INDEX": np.tile(source_order.to_numpy(), date_count),
        "H3_RESOLUTION": np.int8(7),
        "AIS_PARENT_H3_R6": np.tile(
            np.asarray([h3.cell_to_parent(str(cell), 6) for cell in source_order]),
            date_count,
        ),
        "AIS_SPATIAL_ALLOCATION_METHOD": "uniform_within_modeled_h3_r6_parent",
        "AIS_SOURCE_TEMPORAL_COVERAGE_FRACTION": np.repeat(ais_coverage, source_count),
        "AIS_TEMPORAL_COVERAGE_COMPLETE": np.repeat(ais_complete, source_count),
        "AIS_SOURCE_COVERAGE_COMPLETE": np.repeat(effective_complete, source_count),
        "AIS_SOURCE_COVERAGE_STATUS": np.repeat(np.asarray(ais_status), source_count),
        "AIS_SOURCE_COVERAGE_STATE": np.repeat(source_coverage_state, source_count),
        "AIS_TEMPORAL_COVERAGE_SCOPE": cfg.ais_temporal_scope,
        "AIS_SPATIAL_COVERAGE_STATE": cfg.ais_spatial_coverage_state,
        "AIS_RECEIVER_COVERAGE_METHOD": cfg.ais_receiver_coverage_method,
        "AIS_ABSENT_POLICY": cfg.absent_ais_policy,
        "FERRY_SOURCE_COVERAGE_COMPLETE": False,
        "FERRY_SOURCE_COVERAGE_STATE": ComponentState.PARTIAL.value,
        "VISIBILITY_KM": conditions["visibility_km"].ravel(),
        "DAYLIGHT_WEIGHT": conditions["daylight_weight"].ravel(),
        "WIND_WEIGHT": conditions["wind_weight"].ravel(),
        "PRECIPITATION_WEIGHT": conditions["precipitation_weight"].ravel(),
        "DYNAMIC_CONDITION_COVERAGE": conditions["available"].astype("float32").ravel(),
    }
    for name, values in ais_activity.items():
        safe, states = apply_coverage_semantics(values, effective_complete, derived=False)
        columns[name] = safe.ravel()
        columns[f"{name}_STATE"] = states.ravel()
    for name, values in ferry_r7.items():
        present = np.isfinite(values) & (values > 0.0)
        columns[name] = np.where(present, values, np.nan).ravel()
        columns[f"{name}_STATE"] = np.where(
            present, ComponentState.PARTIAL.value, ComponentState.UNKNOWN.value
        ).ravel()
    for name, values in (
        ("VISIBILITY", conditions["visibility_km"]),
        ("DAYLIGHT", conditions["daylight_weight"]),
        ("WIND", conditions["wind_weight"]),
        ("PRECIPITATION", conditions["precipitation_weight"]),
    ):
        columns[f"{name}_STATE"] = np.select(
            [~np.isfinite(values), values > 0.0],
            [ComponentState.UNKNOWN.value, ComponentState.POSITIVE.value],
            default=ComponentState.DERIVED_ZERO.value,
        ).ravel()
    columns["SEA_STATE_WEIGHT"] = np.nan
    columns["SEA_STATE_STATE"] = ComponentState.SOURCE_UNAVAILABLE.value
    columns["WHALE_WATCH_ACTIVITY_HOURS"] = np.nan
    columns["WHALE_WATCH_ACTIVITY_HOURS_STATE"] = ComponentState.SOURCE_UNAVAILABLE.value
    columns["REPORTING_CAPTURE_WEIGHT"] = np.nan
    columns["REPORTING_CAPTURE_STATE"] = ComponentState.SOURCE_UNAVAILABLE.value
    columns["ALL_VESSEL_AIS_CAUSAL_ROLE"] = cfg.causal_roles["all_vessel_ais"]
    columns["PASSENGER_AIS_CAUSAL_ROLE"] = cfg.causal_roles["passenger_ais"]
    columns["RECREATIONAL_AIS_CAUSAL_ROLE"] = cfg.causal_roles["recreational_ais"]
    columns["COMMERCIAL_AIS_CAUSAL_ROLE"] = cfg.causal_roles["commercial_ais"]
    columns["FISHING_AIS_CAUSAL_ROLE"] = cfg.causal_roles["fishing_ais"]
    columns["FERRY_CAUSAL_ROLE"] = cfg.causal_roles["ferry"]
    columns["WHALE_WATCH_CAUSAL_ROLE"] = cfg.causal_roles["whale_watch"]
    columns["COMPONENT_PROVENANCE_JSON"] = component_provenance
    columns["DATA_COVERAGE_STATE"] = "partial"
    columns["SOURCE_COVERAGE_STATE"] = "partial"
    return add_lineage_pandas(pd.DataFrame(columns), lineage)


def _target_year_frame(
    cfg: WaterObservationConfig,
    *,
    dates: pd.DatetimeIndex,
    ais_basis: KernelBasis,
    ais_activity: dict[str, np.ndarray],
    ais_coverage: np.ndarray,
    ais_complete: np.ndarray,
    ais_status: list[str],
    ais_conditions: dict[str, np.ndarray],
    ferry_basis: KernelBasis | None,
    ferry_activity: dict[str, np.ndarray] | None,
    ferry_conditions: dict[str, np.ndarray] | None,
    lineage: dict[str, Any],
    component_provenance: str,
    distance_summaries: dict[str, np.ndarray],
) -> pd.DataFrame:
    date_count = len(dates)
    target_count = len(ais_basis.target_order)
    spatial_complete = cfg.ais_spatial_coverage_state == "complete"
    effective_complete = ais_complete & spatial_complete
    source_coverage_state = np.where(
        np.asarray(ais_coverage) > 0.0, ComponentState.PARTIAL.value, ComponentState.UNKNOWN.value
    )
    legacy_distance_adjusted = np.tile(ais_basis.static_target_support, date_count)
    columns: dict[str, Any] = {
        "DATE": np.repeat(dates.to_numpy(dtype="datetime64[ns]"), target_count),
        "H3_INDEX": np.tile(ais_basis.target_order.to_numpy(), date_count),
        "H3_RESOLUTION": np.int8(6),
        "LINE_OF_SIGHT_SUPPORT": np.nan,
        "LINE_OF_SIGHT_STATE": ComponentState.SOURCE_UNAVAILABLE.value,
        "PHYSICAL_VIEWABILITY_RAW": np.nan,
        "PHYSICAL_VIEWABILITY_STATE": ComponentState.SOURCE_UNAVAILABLE.value,
        "DISTANCE_ADJUSTED_VIEWABILITY_RAW": legacy_distance_adjusted,
        "DISTANCE_ADJUSTED_VIEWABILITY_STATE": np.where(
            legacy_distance_adjusted > 0.0,
            ComponentState.POSITIVE.value,
            ComponentState.DERIVED_ZERO.value,
        ),
        "DISTANCE_KM_MEAN": np.tile(distance_summaries["DISTANCE_KM_MEAN"], date_count),
        "DISTANCE_DETECTION_WEIGHT": np.tile(
            distance_summaries["DISTANCE_DETECTION_WEIGHT"], date_count
        ),
        "DISTANCE_DETECTION_STATE": np.select(
            [
                ~np.isfinite(np.tile(distance_summaries["DISTANCE_DETECTION_WEIGHT"], date_count)),
                np.tile(distance_summaries["DISTANCE_DETECTION_WEIGHT"], date_count) > 0.0,
            ],
            [
                ComponentState.NOT_APPLICABLE.value,
                ComponentState.POSITIVE.value,
            ],
            default=ComponentState.DERIVED_ZERO.value,
        ),
        "AIS_SOURCE_TEMPORAL_COVERAGE_FRACTION": np.repeat(ais_coverage, target_count),
        "AIS_TEMPORAL_COVERAGE_COMPLETE": np.repeat(ais_complete, target_count),
        "AIS_SOURCE_COVERAGE_COMPLETE": np.repeat(effective_complete, target_count),
        "AIS_SOURCE_COVERAGE_STATUS": np.repeat(np.asarray(ais_status), target_count),
        "AIS_SOURCE_COVERAGE_STATE": np.repeat(source_coverage_state, target_count),
        "AIS_TEMPORAL_COVERAGE_SCOPE": cfg.ais_temporal_scope,
        "AIS_SPATIAL_COVERAGE_STATE": cfg.ais_spatial_coverage_state,
        "AIS_RECEIVER_COVERAGE_METHOD": cfg.ais_receiver_coverage_method,
        "AIS_ABSENT_POLICY": cfg.absent_ais_policy,
        "FERRY_SOURCE_COVERAGE_COMPLETE": False,
        "FERRY_SOURCE_COVERAGE_STATE": ComponentState.PARTIAL.value,
    }
    state_columns: list[str] = []
    transformation_columns: list[str] = []
    for source_column, target_column in AIS_TARGET_COLUMNS.items():
        propagated, evaluated_coverage = _propagate(
            cfg, ais_activity[source_column], ais_conditions, ais_basis
        )
        safe, states = apply_coverage_semantics(
            propagated,
            effective_complete[:, None] & (evaluated_coverage >= 1 - 1e-12),
            derived=True,
        )
        columns[target_column] = safe.ravel()
        columns[target_column.removesuffix("_RAW") + "_CONDITION_COVERAGE"] = (
            evaluated_coverage.ravel()
        )
        state_column = f"{target_column.removesuffix('_RAW')}_STATE"
        columns[state_column] = states.ravel()
        state_columns.append(state_column)
        transformation_columns.append(target_column)
    summaries = propagated_condition_summaries(
        ais_conditions,
        ais_basis,
        visibility_transition_fraction=cfg.visibility_transition_fraction,
        visibility_minimum_transition_km=cfg.visibility_minimum_transition_km,
    )
    for name, values in summaries.items():
        columns[name] = values.ravel()
    for value_column, state_column in (
        ("ATMOSPHERIC_VISIBILITY_WEIGHT", "ATMOSPHERIC_VISIBILITY_STATE"),
        ("DAYLIGHT_WEIGHT", "DAYLIGHT_STATE"),
        ("WIND_WEIGHT", "WIND_STATE"),
        ("PRECIPITATION_WEIGHT", "PRECIPITATION_STATE"),
    ):
        values = np.asarray(columns[value_column], dtype="float64")
        condition_coverage = summaries[value_column + "_COVERAGE"].ravel()
        columns[state_column] = np.select(
            [
                ~np.isfinite(values),
                condition_coverage < 1.0,
                values > 0.0,
            ],
            [
                ComponentState.UNKNOWN.value,
                ComponentState.PARTIAL.value,
                ComponentState.POSITIVE.value,
            ],
            default=ComponentState.DERIVED_ZERO.value,
        )
    ferry_lookup = {}
    if ferry_basis is not None and ferry_activity is not None and ferry_conditions is not None:
        target_codes = ais_basis.target_order.get_indexer(ferry_basis.target_order)
        if (target_codes < 0).any():
            raise ValueError(
                "Ferry propagation produced targets outside the water target universe."
            )
        for source_column, target_column in FERRY_TARGET_COLUMNS.items():
            native, evaluated_coverage = _propagate(
                cfg, ferry_activity[source_column], ferry_conditions, ferry_basis
            )
            aligned = np.full((date_count, target_count), np.nan, dtype="float64")
            aligned[:, target_codes] = native
            positive = np.isfinite(aligned) & (aligned > 0.0)
            aligned[~positive] = np.nan
            ferry_lookup[target_column] = aligned
            columns[target_column] = aligned.ravel()
            columns[f"{target_column.removesuffix('_RAW')}_STATE"] = np.where(
                positive, ComponentState.PARTIAL.value, ComponentState.UNKNOWN.value
            ).ravel()
            state_columns.append(f"{target_column.removesuffix('_RAW')}_STATE")
            transformation_columns.append(target_column)
    else:
        for target_column in FERRY_TARGET_COLUMNS.values():
            columns[target_column] = np.nan
            columns[f"{target_column.removesuffix('_RAW')}_STATE"] = ComponentState.UNKNOWN.value
            state_columns.append(f"{target_column.removesuffix('_RAW')}_STATE")
            transformation_columns.append(target_column)
    unavailable = {
        "WATER_WHALE_WATCH_OBSERVATION_OPPORTUNITY_RAW": "WATER_WHALE_WATCH_OBSERVATION_OPPORTUNITY_STATE",
        "SEA_STATE_WEIGHT": "SEA_STATE_STATE",
        "REPORTING_CAPTURE_WEIGHT": "REPORTING_CAPTURE_STATE",
    }
    for value_column, state_column in unavailable.items():
        columns[value_column] = np.nan
        columns[state_column] = ComponentState.SOURCE_UNAVAILABLE.value
        state_columns.append(state_column)
        if value_column.endswith("_RAW"):
            transformation_columns.append(value_column)
    columns["PRIMARY_WATER_COMPOSITE"] = np.nan
    columns["PRIMARY_WATER_COMPOSITE_STATE"] = ComponentState.NOT_APPLICABLE.value
    columns["PRODUCT_ROLE"] = ProductRole.CANONICAL_COMPONENT.value
    columns["ALL_VESSEL_AIS_CAUSAL_ROLE"] = cfg.causal_roles["all_vessel_ais"]
    columns["PASSENGER_AIS_CAUSAL_ROLE"] = cfg.causal_roles["passenger_ais"]
    columns["RECREATIONAL_AIS_CAUSAL_ROLE"] = cfg.causal_roles["recreational_ais"]
    columns["COMMERCIAL_AIS_CAUSAL_ROLE"] = cfg.causal_roles["commercial_ais"]
    columns["FISHING_AIS_CAUSAL_ROLE"] = cfg.causal_roles["fishing_ais"]
    columns["FERRY_CAUSAL_ROLE"] = cfg.causal_roles["ferry"]
    columns["WHALE_WATCH_CAUSAL_ROLE"] = cfg.causal_roles["whale_watch"]
    columns["COMPONENT_PROVENANCE_JSON"] = component_provenance
    columns["DATA_COVERAGE_STATE"] = "partial"
    columns["SOURCE_COVERAGE_STATE"] = "partial"
    frame = add_transformations(pd.DataFrame(columns), transformation_columns, "DATE")
    return add_lineage_pandas(frame, lineage)


def _build_daily(
    cfg: WaterObservationConfig,
    *,
    ais_basis: KernelBasis,
    ferry: pd.DataFrame,
    ferry_basis: KernelBasis | None,
    lineage: dict[str, Any],
    component_provenance: str,
    distance_summaries: dict[str, np.ndarray],
) -> tuple[int, int]:
    source_temporary = cfg.source_daily_path.with_name(f".{cfg.source_daily_path.name}.part")
    target_temporary = cfg.target_daily_path.with_name(f".{cfg.target_daily_path.name}.part")
    source_writer = None
    target_writer = None
    source_rows = 0
    target_rows = 0
    source_order_r7 = _r7_source_order(cfg)
    try:
        for year in range(2020, 2025):
            dates = pd.date_range(date(year, 1, 1), date(year, 12, 31), freq="D")
            ais_activity, coverage, complete, status = _read_ais_year(
                cfg, source_cells=ais_basis.source_order, dates=dates
            )
            ais_conditions = _read_conditions(cfg, source_cells=ais_basis.source_order, dates=dates)
            source_activity_r7 = _expand_ais_to_r7_sources(
                ais_activity,
                r6_sources=ais_basis.source_order,
                r7_sources=source_order_r7,
            )
            source_conditions_r7 = _read_conditions(
                cfg,
                source_cells=source_order_r7,
                dates=dates,
            )
            ferry_r7 = _read_ferry_arrays(
                ferry,
                source_cells=source_order_r7,
                dates=dates,
            )
            for month in range(1, 13):
                selected = np.flatnonzero(dates.month == month)
                source_frame = _source_year_frame(
                    cfg,
                    dates[selected],
                    source_order_r7,
                    {name: values[selected] for name, values in source_activity_r7.items()},
                    coverage[selected],
                    complete[selected],
                    [status[index] for index in selected],
                    {name: values[selected] for name, values in source_conditions_r7.items()},
                    {name: values[selected] for name, values in ferry_r7.items()},
                    lineage,
                    component_provenance,
                )
                source_writer = _append_table(source_writer, source_frame, source_temporary)
                source_rows += len(source_frame)

            native_ferry = None
            native_conditions = None
            if ferry_basis is not None:
                native_ferry = _read_ferry_arrays(
                    ferry,
                    source_cells=ferry_basis.source_order,
                    dates=dates,
                )
                native_conditions = _read_conditions(
                    cfg, source_cells=ferry_basis.source_order, dates=dates
                )
            target_frame = _target_year_frame(
                cfg,
                dates=dates,
                ais_basis=ais_basis,
                ais_activity=ais_activity,
                ais_coverage=coverage,
                ais_complete=complete,
                ais_status=status,
                ais_conditions=ais_conditions,
                ferry_basis=ferry_basis,
                ferry_activity=native_ferry,
                ferry_conditions=native_conditions,
                lineage=lineage,
                component_provenance=component_provenance,
                distance_summaries=distance_summaries,
            )
            target_writer = _append_table(target_writer, target_frame, target_temporary)
            target_rows += len(target_frame)
    finally:
        if source_writer is not None:
            source_writer.close()
        if target_writer is not None:
            target_writer.close()
    if source_rows != 1827 * len(source_order_r7):
        raise ValueError("Water source daily row count is incomplete.")
    if target_rows != 1827 * len(ais_basis.target_order):
        raise ValueError("Water target daily row count is incomplete.")
    os.replace(source_temporary, cfg.source_daily_path)
    os.replace(target_temporary, cfg.target_daily_path)
    return source_rows, target_rows


def _build_weekly(cfg: WaterObservationConfig) -> tuple[int, int]:
    source_schema = pl.scan_parquet(cfg.source_daily_path).collect_schema().names()
    source_additive = [
        *AIS_TARGET_COLUMNS.keys(),
        "FERRY_RIDER_HOURS",
        "FERRY_PLATFORM_HOURS",
        "WHALE_WATCH_ACTIVITY_HOURS",
    ]
    source_states = [f"{name}_STATE" for name in source_additive] + [
        "VISIBILITY_STATE",
        "DAYLIGHT_STATE",
        "WIND_STATE",
        "PRECIPITATION_STATE",
        "SEA_STATE_STATE",
        "REPORTING_CAPTURE_STATE",
    ]
    source_weekly = aggregate_weekly(
        pl.scan_parquet(cfg.source_daily_path),
        additive_columns=[name for name in source_additive if name in source_schema],
        mean_columns=[
            name
            for name in [
                *SOURCE_CONDITION_COLUMNS,
                "AIS_SOURCE_TEMPORAL_COVERAGE_FRACTION",
                "SEA_STATE_WEIGHT",
                "REPORTING_CAPTURE_WEIGHT",
            ]
            if name in source_schema
        ],
        state_columns=[name for name in source_states if name in source_schema],
    )
    _atomic_parquet(source_weekly, cfg.source_weekly_path)

    target_schema = pl.scan_parquet(cfg.target_daily_path).collect_schema().names()
    target_additive = [*AIS_TARGET_COLUMNS.values(), *FERRY_TARGET_COLUMNS.values()]
    target_additive.append("WATER_WHALE_WATCH_OBSERVATION_OPPORTUNITY_RAW")
    target_states = [f"{name.removesuffix('_RAW')}_STATE" for name in target_additive] + [
        "LINE_OF_SIGHT_STATE",
        "PHYSICAL_VIEWABILITY_STATE",
        "DISTANCE_DETECTION_STATE",
        "DISTANCE_ADJUSTED_VIEWABILITY_STATE",
        "ATMOSPHERIC_VISIBILITY_STATE",
        "DAYLIGHT_STATE",
        "WIND_STATE",
        "PRECIPITATION_STATE",
        "SEA_STATE_STATE",
        "REPORTING_CAPTURE_STATE",
        "PRIMARY_WATER_COMPOSITE_STATE",
    ]
    target_weekly = aggregate_weekly(
        pl.scan_parquet(cfg.target_daily_path),
        additive_columns=[name for name in target_additive if name in target_schema],
        mean_columns=[
            name
            for name in [
                *TARGET_CONDITION_COLUMNS,
                *[name for name in target_schema if name.endswith("_COVERAGE")],
                "PHYSICAL_VIEWABILITY_RAW",
                "DISTANCE_ADJUSTED_VIEWABILITY_RAW",
                "DISTANCE_KM_MEAN",
                "DISTANCE_DETECTION_WEIGHT",
                "LINE_OF_SIGHT_SUPPORT",
                "AIS_SOURCE_TEMPORAL_COVERAGE_FRACTION",
                "SEA_STATE_WEIGHT",
                "REPORTING_CAPTURE_WEIGHT",
                "PRIMARY_WATER_COMPOSITE",
            ]
            if name in target_schema
        ],
        state_columns=[name for name in target_states if name in target_schema],
    )
    _atomic_parquet(target_weekly, cfg.target_weekly_path)
    return source_weekly.height, target_weekly.height


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    cfg = load_water_observation_config(config_path, resolve_generation=False)
    if cfg.manifest_path.exists() and not overwrite:
        raise FileExistsError(f"Water observation selector exists: {cfg.manifest_path}")
    raw_manifest = load_manifest(cfg.raw_manifest_path)
    if raw_manifest["config_hash"] != cfg.human.config_hash:
        raise ValueError("Water observation input inventory is stale; rerun download.")
    if not any(
        item.get("name", "").startswith("canonical_water_") for item in raw_manifest["inputs"]
    ):
        raise ValueError("Canonical water-area source identity is missing; rerun water download")
    for item in raw_manifest["inputs"]:
        if sha256_file(item["path"]) != item["sha256"]:
            raise ValueError(f"Water observation input checksum is stale: {item['path']}")
    if cfg.source_completeness != "complete" and not allow_partial:
        raise ValueError(
            "Water observation inputs are partial; use allow_partial=True for research."
        )
    ais_manifest = load_manifest(cfg.ais_manifest_path)
    ferry_manifest = load_manifest(cfg.ferry_manifest_path)
    weather_manifest = json.loads(cfg.surface_weather_manifest_path.read_text())
    daylight_manifest = json.loads(cfg.daylight_manifest_path.read_text())
    land_manifest = load_manifest(cfg.land_manifest_path)
    if weather_manifest.get("source_completeness") != "complete":
        raise ValueError("Surface weather must be complete for water dynamics.")
    if daylight_manifest.get("source_completeness") not in {
        "complete",
        "not_applicable_deterministic",
    }:
        raise ValueError("Daylight has unsupported completeness semantics.")
    app = load_app_config(cfg.viewshed_config_path)
    static_metadata = validate_static_artifact_metadata(
        cfg.water_static_weights_path, raw=app.raw_config, source_type="water"
    )
    if static_metadata.get("scientific_config_hash") != static_scientific_config_hash(
        app.raw_config
    ):
        raise ValueError("Water static viewshed scientific configuration is stale.")
    sightings_observations = resolve_sightings_release_artifact(
        cfg.sightings_release_pointer,
        "whale.sightings.observations",
        verify_checksum=True,
    )
    sightings_source_records = resolve_sightings_release_artifact(
        cfg.sightings_release_pointer,
        "whale.sightings.source_records",
        verify_checksum=True,
    )

    canonical_support = _canonical_water_support(cfg)
    ais_basis, kernel_metadata = _r6_basis(cfg, canonical_support)
    distance_config = load_distance_weight_config(app.raw_config)
    viewshed_max_distance_km = (
        float(app.raw_config.get("viewshed", {}).get("max_distance_m", 0.0)) / 1000.0
    )
    distance_summaries = static_distance_summaries(
        ais_basis,
        distance_detection_weights=distance_weight_values(
            ais_basis.distance_km,
            distance_config,
            viewshed_max_distance_km,
        ),
    )
    ferry_daily_matches = [
        Path(item["path"])
        for item in ferry_manifest["artifacts"]
        if item["dataset_id"].endswith(".daily_r7")
    ]
    if len(ferry_daily_matches) != 1:
        raise ValueError("Ferry manifest must contain exactly one daily_r7 artifact.")
    ferry = _load_ferry_daily(ferry_daily_matches[0])
    ferry_sources = set(ferry["source_h3"].astype(str))
    ferry_basis = _ferry_basis(cfg, ferry_source_cells=ferry_sources, support=canonical_support)
    cfg = new_generation(cfg)
    support_path = cfg.source_daily_path.parent / "canonical_target_water_support.parquet"
    _atomic_parquet(pl.from_pandas(canonical_support), support_path)
    lineage = lineage_values(
        generation_id=cfg.source_daily_path.parent.name,
        config_hash=cfg.human.config_hash,
        source_hashes={
            "ais_manifest": sha256_file(cfg.ais_manifest_path),
            "ferry_manifest": sha256_file(cfg.ferry_manifest_path),
            "surface_weather_manifest": sha256_file(cfg.surface_weather_manifest_path),
            "daylight_manifest": sha256_file(cfg.daylight_manifest_path),
            "water_static_viewshed": sha256_file(cfg.water_static_weights_path),
            "water_static_viewshed_metadata": sha256_file(
                cfg.water_static_weights_path.with_name(
                    f"{cfg.water_static_weights_path.stem}_metadata.json"
                )
            ),
            "viewshed_config": sha256_file(cfg.viewshed_config_path),
            "land_generation_manifest": sha256_file(cfg.land_manifest_path),
            "public_shore_access": sha256_file(cfg.public_shore_path),
            "sightings_release_pointer": sha256_file(cfg.sightings_release_pointer),
            "sightings_observations": sightings_observations.checksum,
            "sightings_source_records": sightings_source_records.checksum,
        },
        source_vintages={
            "ais": ais_manifest.get("temporal_coverage", {}),
            "ferry": ferry_manifest.get("temporal_coverage", {}),
            "surface_weather": weather_manifest.get("temporal_coverage", {}),
            "daylight": daylight_manifest.get("temporal_coverage", {}),
            "land": land_manifest.get("temporal_coverage", {}),
            "sightings_release_id": sightings_observations.release_id,
        },
    )
    component_provenance = component_provenance_json(
        {
            "ais_activity": {
                "source_vintage": ais_manifest.get("temporal_coverage", {}),
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": False,
                "spatial_receiver_coverage": cfg.ais_spatial_coverage_state,
            },
            "ferry_activity": {
                "source_vintage": ferry_manifest.get("temporal_coverage", {}),
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": False,
            },
            "surface_weather": {
                "source_vintage": weather_manifest.get("temporal_coverage", {}),
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": False,
            },
            "daylight": {
                "source_vintage": daylight_manifest.get("temporal_coverage", {}),
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": False,
            },
            "water_static_viewability": {
                "source_vintage": static_metadata.get("created_at_utc", "unknown"),
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": True,
                "schema_version": static_metadata.get("schema_version", "legacy_static_pair"),
            },
            "land_opportunity": {
                "source_vintage": land_manifest.get("temporal_coverage", {}),
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": True,
            },
            "public_shore_access": {
                "source_vintage": "manifest-selected current static layer",
                "knowledge_time_utc": lineage["KNOWLEDGE_TIME_UTC"],
                "historical_reconstruction": True,
            },
        }
    )
    source_rows, target_rows = _build_daily(
        cfg,
        ais_basis=ais_basis,
        ferry=ferry,
        ferry_basis=ferry_basis,
        lineage=lineage,
        component_provenance=component_provenance,
        distance_summaries=distance_summaries,
    )
    source_weekly_rows, target_weekly_rows = _build_weekly(cfg)

    contract = {
        "schema_version": SCHEMA_VERSION,
        "physical_artifacts_unchanged": True,
        "physical_columns": list(static_pair_arrow_schema().names),
        "future_geometry_schema": list(static_geometry_arrow_schema().names),
        "active_static_schema": "legacy_static_pair_v2",
        "future_geometry_generation_published": False,
        "canonical_mapping": STATIC_PAIR_CANONICAL_MAPPING,
        "distance_semantics": (
            "weight_terrain is distance-weighted LOS support; weight_distance is a centroid "
            "diagnostic; physical viewability is weight_terrain times vegetation support"
        ),
        "curve": app.raw_config.get("distance_weight", {}),
        "curve_status": "uncalibrated",
        "curve_sensitivity": list(cfg.distance_curve_sensitivity),
        "curve_selection_uses_whale_sightings": False,
        "horizon": {
            "observer_height_m": app.raw_config.get("water_viewing", {})
            .get("observer_height_classes", {})
            .get(
                app.raw_config.get("water_viewing", {}).get("default_observer_height_class"),
                {},
            )
            .get("eye_height_m"),
            "target_height_m": app.raw_config.get("water_viewing", {}).get(
                "target_visibility_height_m"
            ),
            "earth_radius_m": app.raw_config.get("viewshed", {}).get("earth_radius_m"),
            "curvature_coefficient": app.raw_config.get("viewshed", {}).get(
                "curvature_coefficient"
            ),
        },
    }
    atomic_write_json(cfg.static_contract_path, contract)

    from .diagnostics import build_diagnostics

    diagnostics = build_diagnostics(cfg, lineage=lineage)
    metadata = {
        "water_semantic_version": "fine_distance_water_area_independent_conditions_v1",
        "temporal_scope": "fixed_historical_interval_2020_2024",
        "schema_version": SCHEMA_VERSION,
        "product": "human.water_observation_opportunity",
        "status": "research_only_partial",
        "model_eligible": False,
        "source_rows_daily": source_rows,
        "target_rows_daily": target_rows,
        "source_rows_weekly": source_weekly_rows,
        "target_rows_weekly": target_weekly_rows,
        "kernel": kernel_metadata,
        "ferry_native_r7_propagation": ferry_basis is not None,
        "canonical_primary_composite": None,
        "legacy_sensitivity_interface": (
            "observer_effort/water_ais_reporting_opportunity_weekly_r6.parquet; "
            "passenger plus recreational AIS only, deprecated compatibility product"
        ),
        "causal_roles": cfg.causal_roles,
        "distance_curve_sensitivity": list(cfg.distance_curve_sensitivity),
        "coverage_contract": {
            "ais_temporal_scope": cfg.ais_temporal_scope,
            "ais_spatial_coverage_state": cfg.ais_spatial_coverage_state,
            "ais_receiver_coverage_method": cfg.ais_receiver_coverage_method,
            "absent_ais_policy": cfg.absent_ais_policy,
        },
        "component_provenance": json.loads(component_provenance),
        "unavailable_components": ["whale_watch", "sea_state", "reporting_capture"],
        "sightings_used_to_construct_or_scale_proxy": False,
        "diagnostics": diagnostics,
        "lineage": lineage,
    }
    atomic_write_json(cfg.metadata_path, metadata)
    artifacts = [
        artifact_record(
            support_path,
            dataset_id="human.water_observation_opportunity.canonical_target_water_support",
            h3_resolution=7,
        ),
        artifact_record(
            cfg.source_daily_path,
            dataset_id="human.water_observation_opportunity.source_daily_h3_r7",
            h3_resolution=7,
        ),
        artifact_record(
            cfg.source_weekly_path,
            dataset_id="human.water_observation_opportunity.source_weekly_h3_r7",
            h3_resolution=7,
        ),
        artifact_record(
            cfg.target_daily_path,
            dataset_id="human.water_observation_opportunity.target_daily_h3_r6",
            h3_resolution=6,
        ),
        artifact_record(
            cfg.target_weekly_path,
            dataset_id="human.water_observation_opportunity.target_weekly_h3_r6",
            h3_resolution=6,
        ),
        artifact_record(
            cfg.static_contract_path,
            dataset_id="human.water_observation_opportunity.static_pair_contract",
        ),
        artifact_record(
            cfg.metadata_path,
            dataset_id="human.water_observation_opportunity.metadata",
        ),
        artifact_record(
            cfg.sighting_detail_path,
            dataset_id="human.water_observation_opportunity.sighting_detail",
            h3_resolution=6,
        ),
        artifact_record(
            cfg.sighting_summary_path,
            dataset_id="human.water_observation_opportunity.sighting_summary",
        ),
        artifact_record(
            cfg.border_matches_path,
            dataset_id="human.water_observation_opportunity.border_matches",
            h3_resolution=7,
        ),
        artifact_record(
            cfg.border_summary_path,
            dataset_id="human.water_observation_opportunity.border_summary",
        ),
    ]
    payload = manifest_payload(
        config=cfg.human,
        stage="build",
        artifacts=artifacts,
        inputs=raw_manifest["inputs"],
        sources=[*ais_manifest.get("sources", []), *ferry_manifest.get("sources", [])],
        source_completeness="partial",
        measurement_statuses=[
            MeasurementStatus.OBSERVED,
            MeasurementStatus.DERIVED,
            MeasurementStatus.ESTIMATED,
            MeasurementStatus.UNAVAILABLE,
        ],
        attribution=[
            *ais_manifest.get("attribution", []),
            *ferry_manifest.get("attribution", []),
        ],
        licenses=[*ais_manifest.get("licenses", []), *ferry_manifest.get("licenses", [])],
        h3_resolution=6,
        temporal={"minimum": "2020-01-01", "maximum": "2024-12-31"},
        limitations=[
            "All outputs are research-only and model-ineligible.",
            "Fixed historical publication interval is 2020-01-01 through 2024-12-31; outside dates are unsupported.",
            "All-vessel AIS contains AIS subclasses; passenger AIS may overlap ferry activity. No components may be summed without an overlap model.",
            "AIS values are unique-vessel-hour proxies with partial source coverage.",
            "AIS R6 activity uses a uniform-within-modeled-parent assumption.",
            "Canonical water-source tables are H3 R7; AIS activity is conservatively distributed from its H3 R6 parent across modeled R7 source children.",
            "Ferry rider and platform components are propagated at native H3 R7 and remain separate from passenger AIS.",
            "Whale-watch, sea-state, direct observer effort, and reporting capture are unavailable.",
            "No primary water composite is defined; the legacy passenger-plus-recreational AIS product remains sensitivity-only.",
            "Sightings are used for contradiction diagnostics only, never fitting or scaling.",
        ],
    )
    for item in raw_manifest["inputs"]:
        if sha256_file(item["path"]) != item["sha256"]:
            raise ValueError(f"Water input changed during build: {item['path']}")
    payload["water_semantic_version"] = "fine_distance_water_area_independent_conditions_v1"
    payload["temporal_scope"] = "fixed_historical_interval_2020_2024; outside dates unavailable"
    payload["lineage"] = lineage
    payload["diagnostic_sources"] = diagnostics
    payload["model_eligible"] = False
    payload["generation_id"] = cfg.source_daily_path.parent.name
    payload["schema_version"] = SCHEMA_VERSION
    from .inspect import inspect

    reports = inspect(
        config_path,
        output_dir=cfg.source_daily_path.parent,
        _candidate_config=cfg,
        _candidate_manifest=payload,
    )
    if reports != [cfg.report_artifact_path]:
        raise ValueError("Water observation inspector wrote an unexpected report artifact.")
    payload["artifacts"].append(
        artifact_record(
            cfg.report_artifact_path,
            dataset_id="human.water_observation_opportunity.report",
        )
    )
    publish(cfg, payload)
    cfg.report_path.parent.mkdir(parents=True, exist_ok=True)
    report_temporary = cfg.report_path.with_name(f".{cfg.report_path.name}.{os.getpid()}.part")
    shutil.copyfile(cfg.report_artifact_path, report_temporary)
    os.replace(report_temporary, cfg.report_path)
    return cfg.target_daily_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(build(args.config, allow_partial=args.allow_partial, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
