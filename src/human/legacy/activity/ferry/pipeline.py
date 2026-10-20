"""Build raw daily ferry effort weights on H3 source cells.

This module deliberately stops at the ferry centerline.  It does not construct
target cells, apply viewshed weights, or normalize the resulting effort.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import yaml
from shapely import line_merge, unary_union
from shapely.geometry import LineString, MultiLineString, Polygon

LOGGER = logging.getLogger("ferry_daily_source_weights")
PIPELINE_VERSION = "1.0.0"
DEFAULT_H3_RESOLUTION = 7
TOLERANCE = 1e-7

ROUTE_OUTPUT_COLUMNS = [
    "service_date",
    "jurisdiction",
    "operator",
    "route_key",
    "route_name",
    "route_geometry_id",
    "source_h3",
    "h3_resolution",
    "daily_riders",
    "daily_sailings",
    "route_duration_minutes",
    "distance_in_cell_m",
    "distance_fraction",
    "voyage_time_in_cell_minutes",
    "ferry_rider_minutes",
    "ferry_rider_hours",
    "ferry_rider_km",
    "ferry_vessel_minutes",
    "ferry_vessel_hours",
    "ferry_vessel_km",
    "ferry_effort_weight_raw",
    "ferry_platform_weight_raw",
    "ridership_is_estimated",
    "sailing_count_is_estimated",
    "platform_effort_available",
    "source_temporal_resolution",
    "full_route_traversal_assumption",
    "ridership_source",
]


class FerryEffortError(ValueError):
    """Raised when a ferry effort contract or conservation check fails."""


class UnresolvedRoutesError(FerryEffortError):
    """Raised when input routes lack explicit, complete configuration."""

    def __init__(self, unresolved: list[dict[str, Any]]):
        self.unresolved = unresolved
        labels = ", ".join(
            f"{row['jurisdiction']}:{row['ridership_route_key']} ({row['reason']})"
            for row in unresolved
        )
        super().__init__(f"Unresolved ferry routes: {labels}")


@dataclass(frozen=True)
class RouteMapping:
    jurisdiction: str
    ridership_route_key: str
    route_geometry_id: str
    route_name: str
    route_duration_minutes: float
    segment_ids: tuple[str, ...]
    enabled: bool = True
    ridership_scope: str = "route_level"
    operator: str = ""
    duration_source_url: str = ""
    full_route_traversal_assumption: bool = False
    traversal_assumption_note: str = ""
    history_duration_eligible: bool = True
    partial_domain_geometry: bool = False

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RouteMapping":
        required = {
            "jurisdiction",
            "ridership_route_key",
            "route_geometry_id",
            "route_name",
            "route_duration_minutes",
            "segment_ids",
        }
        missing = sorted(required - set(value))
        if missing:
            raise FerryEffortError(f"Route mapping is missing fields: {missing}")
        duration = float(value["route_duration_minutes"])
        segments = tuple(str(item) for item in value["segment_ids"])
        if duration <= 0 or not segments:
            raise FerryEffortError(
                f"Route {value['ridership_route_key']} needs a positive duration "
                "and at least one explicit segment_id"
            )
        return cls(
            jurisdiction=str(value["jurisdiction"]),
            ridership_route_key=str(value["ridership_route_key"]),
            route_geometry_id=str(value["route_geometry_id"]),
            route_name=str(value["route_name"]),
            route_duration_minutes=duration,
            segment_ids=segments,
            enabled=bool(value.get("enabled", True)),
            ridership_scope=str(value.get("ridership_scope", "route_level")),
            operator=str(value.get("operator", "")),
            duration_source_url=str(value.get("duration_source_url", "")),
            full_route_traversal_assumption=bool(
                value.get("full_route_traversal_assumption", False)
            ),
            traversal_assumption_note=str(value.get("traversal_assumption_note", "")),
            history_duration_eligible=bool(value.get("history_duration_eligible", True)),
            partial_domain_geometry=bool(value.get("partial_domain_geometry", False)),
        )


@dataclass(frozen=True)
class PipelineResult:
    route_level_path: Path
    collapsed_path: Path
    crosswalk_path: Path
    metadata_path: Path
    unresolved_path: Path
    route_level_rows: int
    collapsed_rows: int


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_route_config(path: str | Path) -> tuple[list[RouteMapping], list[dict[str, Any]]]:
    with Path(path).open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    mappings = [RouteMapping.from_dict(row) for row in config.get("routes", [])]
    enabled = [row for row in mappings if row.enabled]
    keys = [(row.jurisdiction, row.ridership_route_key) for row in enabled]
    if len(keys) != len(set(keys)):
        raise FerryEffortError("Enabled route configuration contains duplicate ridership keys")
    geometry_contracts: dict[str, tuple[tuple[str, ...], float]] = {}
    for row in enabled:
        contract = (row.segment_ids, row.route_duration_minutes)
        existing = geometry_contracts.setdefault(row.route_geometry_id, contract)
        if existing != contract:
            raise FerryEffortError(
                f"Mappings that share route_geometry_id {row.route_geometry_id} must "
                "use identical segment_ids and route_duration_minutes"
            )
    exclusions = [dict(row) for row in config.get("excluded_routes", [])]
    return enabled, exclusions


def _sum_or_null(series: pd.Series) -> float:
    return series.sum(min_count=1)


def _require_columns(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise FerryEffortError(f"{label} is missing required columns: {missing}")


def _validate_nonnegative(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    for column in columns:
        if column in frame and (pd.to_numeric(frame[column], errors="coerce") < 0).any():
            raise FerryEffortError(f"{label}.{column} contains negative values")


def standardize_wsf_route_days(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse WSF sailing records to undirected adjacent-leg route-days."""
    required = [
        "service_date",
        "segment_id",
        "route_group",
        "sailing_id",
        "reported_total_riders",
        "source",
    ]
    _require_columns(frame, required, "WSF ridership")
    work = frame.copy()
    work["service_date"] = pd.to_datetime(work["service_date"]).dt.normalize()
    work["reported_total_riders"] = pd.to_numeric(work["reported_total_riders"], errors="coerce")
    _validate_nonnegative(work, ["reported_total_riders"], "WSF ridership")
    group = ["service_date", "segment_id", "route_group"]
    result = (
        work.groupby(group, dropna=False, observed=True)
        .agg(
            daily_riders=("reported_total_riders", _sum_or_null),
            daily_sailings=("sailing_id", "nunique"),
            ridership_source=("source", "first"),
        )
        .reset_index()
    )
    result = result.rename(columns={"segment_id": "route_key", "route_group": "source_route_name"})
    result["jurisdiction"] = "Washington"
    result["operator"] = "Washington State Ferries"
    result["ridership_is_estimated"] = False
    result["sailing_count_is_estimated"] = False
    result["platform_effort_available"] = True
    result["source_temporal_resolution"] = "sailing"
    result["full_route_traversal_assumption"] = False
    return result


TERMINAL_ALIASES = {
    "colman": "seattle",
    "keystone": "coupeville",
    "ptdefiance": "pointdefiance",
}


def _normalized_terminal(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value).lower()).replace("island", "")
    return TERMINAL_ALIASES.get(normalized, normalized)


def build_wsf_duration_adjustments(
    wsf_sailings: pd.DataFrame,
    vessel_history: pd.DataFrame,
    mappings: Sequence[RouteMapping],
) -> pd.DataFrame:
    """Build route-day totals using WSDOT operational durations when available."""
    _require_columns(
        vessel_history,
        [
            "service_date",
            "departing_terminal",
            "arriving_terminal",
            "scheduled_departure_local",
            "operational_duration_minutes",
            "operational_duration_is_valid",
        ],
        "WSF vessel history",
    )
    mapping_lookup = {
        mapping.ridership_route_key: mapping
        for mapping in mappings
        if mapping.jurisdiction == "Washington"
    }
    history = vessel_history.loc[
        vessel_history["operational_duration_is_valid"].fillna(False)
    ].copy()
    history["service_date"] = pd.to_datetime(history["service_date"]).dt.normalize()
    history["scheduled_departure_local"] = pd.to_datetime(history["scheduled_departure_local"])
    history["origin_key"] = history["departing_terminal"].map(_normalized_terminal)
    history["destination_key"] = history["arriving_terminal"].map(_normalized_terminal)
    join_keys = ["service_date", "scheduled_departure_local", "origin_key", "destination_key"]
    if history.duplicated(join_keys).any():
        duplicates = history.loc[history.duplicated(join_keys, keep=False), join_keys]
        raise FerryEffortError(
            "WSF vessel history has ambiguous terminal/time keys: "
            f"{duplicates.head(5).to_dict(orient='records')}"
        )
    history_dates = set(history["service_date"])
    work = wsf_sailings.copy()
    work["service_date"] = pd.to_datetime(work["service_date"]).dt.normalize()
    work = work.loc[
        work["service_date"].isin(history_dates)
        & work["segment_id"].astype(str).isin(mapping_lookup)
    ].copy()
    if work.empty:
        return pd.DataFrame()
    work["scheduled_departure_local"] = pd.to_datetime(work["departure_local"])
    work["origin_key"] = work["origin_terminal"].map(_normalized_terminal)
    work["destination_key"] = work["destination_terminal"].map(_normalized_terminal)
    work = work.merge(
        history[join_keys + ["operational_duration_minutes"]],
        on=join_keys,
        how="left",
        validate="many_to_one",
    )
    work["configured_route_duration_minutes"] = work["segment_id"].map(
        lambda key: mapping_lookup[str(key)].route_duration_minutes
    )
    eligible = (
        work["segment_id"]
        .map(lambda key: mapping_lookup[str(key)].history_duration_eligible)
        .astype(bool)
    )
    work["history_duration_matched"] = work["operational_duration_minutes"].notna() & eligible
    work["selected_duration_minutes"] = work["operational_duration_minutes"].where(
        work["history_duration_matched"],
        work["configured_route_duration_minutes"],
    )
    work["reported_total_riders"] = pd.to_numeric(work["reported_total_riders"], errors="coerce")
    work["rider_minutes_route_total_row"] = (
        work["reported_total_riders"] * work["selected_duration_minutes"]
    )
    work["history_matched_riders_row"] = work["reported_total_riders"].where(
        work["history_duration_matched"], 0.0
    )
    group = ["service_date", "segment_id"]
    result = (
        work.groupby(group, as_index=False, observed=True)
        .agg(
            route_rider_minutes_total=("rider_minutes_route_total_row", _sum_or_null),
            route_vessel_minutes_total=("selected_duration_minutes", _sum_or_null),
            history_matched_riders=("history_matched_riders_row", _sum_or_null),
            history_matched_sailings=("history_duration_matched", "sum"),
            duration_adjustment_sailings=("sailing_id", "nunique"),
            duration_adjustment_riders=("reported_total_riders", _sum_or_null),
        )
        .rename(columns={"segment_id": "route_key"})
    )
    result["route_duration_minutes_operational"] = (
        result["route_rider_minutes_total"] / result["duration_adjustment_riders"]
    )
    result["platform_route_duration_minutes"] = (
        result["route_vessel_minutes_total"] / result["duration_adjustment_sailings"]
    )
    result["voyage_duration_history_coverage_fraction"] = (
        result["history_matched_sailings"] / result["duration_adjustment_sailings"]
    )
    result["rider_duration_history_coverage_fraction"] = np.where(
        result["duration_adjustment_riders"] > 0,
        result["history_matched_riders"] / result["duration_adjustment_riders"],
        np.nan,
    )
    result["voyage_duration_source"] = np.select(
        [
            result["history_matched_sailings"].eq(result["duration_adjustment_sailings"]),
            result["history_matched_sailings"].gt(0),
        ],
        [
            "wsdot_actual_departure_to_estimated_arrival",
            "mixed_wsdot_history_and_configured_fallback",
        ],
        default="configured_route_duration_fallback",
    )
    return result


def apply_wsf_duration_adjustments(
    route_level: pd.DataFrame,
    adjustments: pd.DataFrame,
    *,
    tolerance: float = TOLERANCE,
) -> pd.DataFrame:
    """Replace fixed WSF time exposure with route-day operational duration totals."""
    if adjustments.empty:
        return route_level
    output = route_level
    output["platform_route_duration_minutes"] = output["route_duration_minutes"]
    output["history_matched_sailings"] = 0
    output["duration_adjustment_sailings"] = output["daily_sailings"]
    output["voyage_duration_history_coverage_fraction"] = 0.0
    output["rider_duration_history_coverage_fraction"] = 0.0
    output["voyage_duration_source"] = "configured_route_duration"
    for adjustment in adjustments.itertuples(index=False):
        mask = output["service_date"].eq(adjustment.service_date) & output["route_key"].eq(
            adjustment.route_key
        )
        if not mask.any():
            continue
        distance_fraction = output.loc[mask, "distance_fraction"]
        output.loc[mask, "route_duration_minutes"] = adjustment.route_duration_minutes_operational
        output.loc[mask, "platform_route_duration_minutes"] = (
            adjustment.platform_route_duration_minutes
        )
        output.loc[mask, "voyage_time_in_cell_minutes"] = (
            adjustment.route_duration_minutes_operational * distance_fraction
        )
        output.loc[mask, "ferry_rider_minutes"] = (
            adjustment.route_rider_minutes_total * distance_fraction
        )
        output.loc[mask, "ferry_rider_hours"] = output.loc[mask, "ferry_rider_minutes"] / 60.0
        output.loc[mask, "ferry_vessel_minutes"] = (
            adjustment.route_vessel_minutes_total * distance_fraction
        )
        output.loc[mask, "ferry_vessel_hours"] = output.loc[mask, "ferry_vessel_minutes"] / 60.0
        output.loc[mask, "history_matched_sailings"] = adjustment.history_matched_sailings
        output.loc[mask, "duration_adjustment_sailings"] = adjustment.duration_adjustment_sailings
        output.loc[mask, "voyage_duration_history_coverage_fraction"] = (
            adjustment.voyage_duration_history_coverage_fraction
        )
        output.loc[mask, "rider_duration_history_coverage_fraction"] = (
            adjustment.rider_duration_history_coverage_fraction
        )
        output.loc[mask, "voyage_duration_source"] = adjustment.voyage_duration_source
    output["ferry_effort_weight_raw"] = output["ferry_rider_minutes"]
    output["ferry_platform_weight_raw"] = output["ferry_vessel_minutes"]
    validate_allocations(output, tolerance=tolerance)
    output["voyage_duration_source"] = output["voyage_duration_source"].astype("category")
    return output


def _bc_monthly_conservation(
    route_days: pd.DataFrame,
    tolerance: float = TOLERANCE,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    grouped = route_days.groupby(["route_key", "report_month"], dropna=False)
    for (route_key, month), group in grouped:
        published_values = group["published_monthly_passengers"].dropna().unique()
        if len(published_values) != 1:
            raise FerryEffortError(
                f"BC route-month {route_key}/{month} has {len(published_values)} "
                "published passenger totals; expected one"
            )
        expected = float(published_values[0])
        actual = float(group["daily_riders"].sum(min_count=1))
        allowed = tolerance * max(1.0, abs(expected))
        passed = bool(np.isclose(actual, expected, rtol=tolerance, atol=allowed))
        checks.append(
            {
                "route_key": str(route_key),
                "report_month": str(month),
                "actual_daily_sum": actual,
                "published_total": expected,
                "absolute_error": abs(actual - expected),
                "passed": passed,
            }
        )
        if not passed:
            raise FerryEffortError(
                f"BC monthly passenger conservation failed for route {route_key}, "
                f"month {month}: daily sum={actual}, published={expected}"
            )
    return checks


def standardize_bc_route_days(
    frame: pd.DataFrame,
    *,
    tolerance: float = TOLERANCE,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Collapse modeled BC route-hour records to route-day totals."""
    required = [
        "service_date",
        "route_number",
        "route_name",
        "reported_total_riders",
        "report_month",
        "published_monthly_passengers",
        "is_estimated",
        "source",
        "source_report_url",
        "observation_granularity",
        "estimation_method",
    ]
    _require_columns(frame, required, "BC Ferries ridership")
    work = frame.copy()
    work["service_date"] = pd.to_datetime(work["service_date"]).dt.normalize()
    work["route_number"] = work["route_number"].astype("string").str.zfill(2)
    work["reported_total_riders"] = pd.to_numeric(work["reported_total_riders"], errors="coerce")
    _validate_nonnegative(work, ["reported_total_riders"], "BC Ferries ridership")
    group = ["service_date", "route_number", "route_name", "report_month"]
    result = (
        work.groupby(group, dropna=False, observed=True)
        .agg(
            daily_riders=("reported_total_riders", _sum_or_null),
            ridership_is_estimated=("is_estimated", "max"),
            ridership_source=("source", "first"),
            source_report_url=("source_report_url", "first"),
            published_monthly_passengers=("published_monthly_passengers", "first"),
            input_temporal_resolution=("observation_granularity", "first"),
            estimation_method=("estimation_method", "first"),
        )
        .reset_index()
    )
    result = result.rename(columns={"route_number": "route_key"})
    result["jurisdiction"] = "British Columbia"
    result["operator"] = "BC Ferries"
    result["daily_sailings"] = pd.Series(pd.NA, index=result.index, dtype="Float64")
    result["sailing_count_is_estimated"] = False
    result["platform_effort_available"] = False
    result["source_temporal_resolution"] = "estimated_route_day"
    result["full_route_traversal_assumption"] = True
    checks = _bc_monthly_conservation(result, tolerance=tolerance)
    return result, checks


def resolve_route_days(
    route_days: pd.DataFrame,
    mappings: Sequence[RouteMapping],
    exclusions: Sequence[Mapping[str, Any]] = (),
    *,
    allow_incomplete_routes: bool = False,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply exact accepted mappings and report unconfigured input routes."""
    mapping_lookup = {(m.jurisdiction, m.ridership_route_key): m for m in mappings}
    exclusion_lookup = {
        (str(row["jurisdiction"]), str(row["ridership_route_key"])): str(
            row.get("reason", "explicitly excluded")
        )
        for row in exclusions
    }
    unresolved: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    input_keys = route_days[["jurisdiction", "route_key"]].drop_duplicates()
    for row in input_keys.itertuples(index=False):
        key = (str(row.jurisdiction), str(row.route_key))
        if key in mapping_lookup:
            continue
        record = {
            "jurisdiction": key[0],
            "ridership_route_key": key[1],
            "reason": exclusion_lookup.get(key, "no explicit accepted route mapping"),
            "explicitly_excluded": key in exclusion_lookup,
        }
        unresolved.append(record)
        if key in exclusion_lookup:
            excluded.append(record)
    if unresolved and not allow_incomplete_routes:
        raise UnresolvedRoutesError(unresolved)
    accepted_keys = set(mapping_lookup)
    mask = [
        (str(jurisdiction), str(route_key)) in accepted_keys
        for jurisdiction, route_key in zip(
            route_days["jurisdiction"], route_days["route_key"], strict=True
        )
    ]
    resolved = route_days.loc[mask].copy()
    for column in [
        "route_geometry_id",
        "route_name",
        "route_duration_minutes",
        "full_route_traversal_assumption",
    ]:
        resolved[column] = [
            getattr(mapping_lookup[(str(j), str(k))], column)
            for j, k in zip(resolved["jurisdiction"], resolved["route_key"], strict=True)
        ]
    resolved["operator"] = [
        mapping_lookup[(str(j), str(k))].operator or operator
        for j, k, operator in zip(
            resolved["jurisdiction"],
            resolved["route_key"],
            resolved["operator"],
            strict=True,
        )
    ]
    _validate_nonnegative(
        resolved,
        ["daily_riders", "daily_sailings", "route_duration_minutes"],
        "standardized route-day",
    )
    if resolved.duplicated(["service_date", "jurisdiction", "route_key"]).any():
        raise FerryEffortError("Standardized route-day key is not unique")
    return resolved, unresolved, excluded


def _line_parts(geometry: LineString | MultiLineString) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    raise FerryEffortError(f"Expected line geometry, found {geometry.geom_type}")


def _h3_polygon(cell: str) -> Polygon:
    boundary = h3.cell_to_boundary(cell)
    return Polygon([(longitude, latitude) for latitude, longitude in boundary])


def _candidate_cells(
    geometry_metric: LineString | MultiLineString,
    metric_crs: Any,
    resolution: int,
) -> set[str]:
    edge_m = float(h3.average_hexagon_edge_length(resolution, unit="m"))
    sample_spacing = max(10.0, edge_m / 4.0)
    points = []
    for part in _line_parts(geometry_metric):
        distances = np.arange(0.0, part.length, sample_spacing).tolist() + [part.length]
        points.extend(part.interpolate(distance) for distance in distances)
    point_series = gpd.GeoSeries(points, crs=metric_crs).to_crs(4326)
    candidates: set[str] = set()
    for point in point_series:
        center = h3.latlng_to_cell(point.y, point.x, resolution)
        candidates.update(h3.grid_disk(center, 1))
    return candidates


def validate_crosswalk(
    crosswalk: pd.DataFrame,
    *,
    tolerance: float = TOLERANCE,
) -> None:
    _require_columns(
        crosswalk,
        [
            "route_geometry_id",
            "source_h3",
            "distance_in_cell_m",
            "route_length_m",
            "distance_fraction",
            "voyage_time_in_cell_minutes",
            "route_duration_minutes",
        ],
        "route-to-H3 crosswalk",
    )
    if crosswalk.empty:
        raise FerryEffortError("Route-to-H3 crosswalk is empty")
    if crosswalk["source_h3"].isna().any():
        raise FerryEffortError("Route-to-H3 crosswalk contains null source_h3")
    if (crosswalk["distance_in_cell_m"] <= 0).any():
        raise FerryEffortError("Route-to-H3 crosswalk contains non-positive distance")
    if (~crosswalk["distance_fraction"].between(0, 1, inclusive="both")).any():
        raise FerryEffortError("Route-to-H3 distance fractions must be between 0 and 1")
    if crosswalk.duplicated(["route_geometry_id", "source_h3"]).any():
        raise FerryEffortError("route_geometry_id x source_h3 must be unique")
    for route_id, group in crosswalk.groupby("route_geometry_id"):
        fraction_sum = float(group["distance_fraction"].sum())
        duration_sum = float(group["voyage_time_in_cell_minutes"].sum())
        expected_duration = float(group["route_duration_minutes"].iloc[0])
        if not np.isclose(fraction_sum, 1.0, rtol=tolerance, atol=tolerance):
            raise FerryEffortError(
                f"Crosswalk conservation failed for {route_id}: "
                f"distance_fraction sums to {fraction_sum}, expected 1"
            )
        if not np.isclose(
            duration_sum,
            expected_duration,
            rtol=tolerance,
            atol=tolerance * max(1.0, expected_duration),
        ):
            raise FerryEffortError(
                f"Crosswalk conservation failed for {route_id}: voyage time sums "
                f"to {duration_sum}, expected {expected_duration}"
            )


def build_route_h3_crosswalk(
    route_segments: gpd.GeoDataFrame,
    mappings: Sequence[RouteMapping],
    *,
    h3_resolution: int = DEFAULT_H3_RESOLUTION,
    tolerance: float = TOLERANCE,
) -> pd.DataFrame:
    """Intersect configured ferry centerlines with H3 cells in a metric CRS."""
    _require_columns(route_segments, ["segment_id", "geometry"], "route segments")
    if route_segments.crs is None:
        raise FerryEffortError("Route segments must declare a CRS")
    rows: list[dict[str, Any]] = []
    unique_mappings: dict[str, RouteMapping] = {}
    for mapping in mappings:
        unique_mappings.setdefault(mapping.route_geometry_id, mapping)
    for mapping in unique_mappings.values():
        selected = route_segments[route_segments["segment_id"].isin(mapping.segment_ids)]
        present = set(selected["segment_id"].astype(str))
        missing = sorted(set(mapping.segment_ids) - present)
        if missing:
            raise FerryEffortError(
                f"Configured geometry {mapping.route_geometry_id} is missing segments {missing}"
            )
        geometry_wgs84 = line_merge(unary_union(selected.to_crs(4326).geometry.array))
        if geometry_wgs84.is_empty:
            raise FerryEffortError(f"Geometry {mapping.route_geometry_id} is empty")
        geometry_series = gpd.GeoSeries([geometry_wgs84], crs=4326)
        metric_crs = geometry_series.estimate_utm_crs()
        if metric_crs is None:
            raise FerryEffortError(
                f"Could not determine metric CRS for {mapping.route_geometry_id}"
            )
        geometry_metric = geometry_series.to_crs(metric_crs).iloc[0]
        route_length_m = float(geometry_metric.length)
        candidates = _candidate_cells(geometry_metric, metric_crs, h3_resolution)
        cell_polygons = gpd.GeoDataFrame(
            {"source_h3": sorted(candidates)},
            geometry=[_h3_polygon(cell) for cell in sorted(candidates)],
            crs=4326,
        ).to_crs(metric_crs)
        intersections = cell_polygons.geometry.intersection(geometry_metric)
        distances = intersections.length
        for cell, distance in zip(cell_polygons["source_h3"], distances, strict=True):
            distance_m = float(distance)
            if distance_m <= tolerance:
                continue
            rows.append(
                {
                    "route_geometry_id": mapping.route_geometry_id,
                    "source_h3": cell,
                    "h3_resolution": h3_resolution,
                    "distance_in_cell_m": distance_m,
                    "route_length_m": route_length_m,
                    "distance_fraction": distance_m / route_length_m,
                    "voyage_time_in_cell_minutes": (
                        mapping.route_duration_minutes * distance_m / route_length_m
                    ),
                    "route_duration_minutes": mapping.route_duration_minutes,
                }
            )
    crosswalk = pd.DataFrame(rows)
    if not crosswalk.empty:
        crosswalk = crosswalk.groupby(
            ["route_geometry_id", "source_h3", "h3_resolution"], as_index=False
        ).agg(
            distance_in_cell_m=("distance_in_cell_m", "sum"),
            route_length_m=("route_length_m", "first"),
            route_duration_minutes=("route_duration_minutes", "first"),
        )
        crosswalk["distance_fraction"] = (
            crosswalk["distance_in_cell_m"] / crosswalk["route_length_m"]
        )
        crosswalk["voyage_time_in_cell_minutes"] = (
            crosswalk["route_duration_minutes"] * crosswalk["distance_fraction"]
        )
    validate_crosswalk(crosswalk, tolerance=tolerance)
    return crosswalk


def allocate_route_days(
    route_days: pd.DataFrame,
    crosswalk: pd.DataFrame,
    *,
    tolerance: float = TOLERANCE,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Allocate additive route-day exposure across centerline-intersected cells."""
    positive = crosswalk.loc[crosswalk["distance_in_cell_m"] > 0].copy()
    validate_crosswalk(positive, tolerance=tolerance)
    allocated = route_days.merge(
        positive,
        on=["route_geometry_id", "route_duration_minutes"],
        how="inner",
        validate="many_to_many",
    )
    allocated["ferry_rider_minutes"] = (
        allocated["daily_riders"] * allocated["voyage_time_in_cell_minutes"]
    )
    allocated["ferry_rider_hours"] = allocated["ferry_rider_minutes"] / 60.0
    allocated["ferry_rider_km"] = (
        allocated["daily_riders"] * allocated["distance_in_cell_m"] / 1000.0
    )
    allocated["ferry_vessel_minutes"] = (
        allocated["daily_sailings"] * allocated["voyage_time_in_cell_minutes"]
    )
    allocated["ferry_vessel_hours"] = allocated["ferry_vessel_minutes"] / 60.0
    allocated["ferry_vessel_km"] = (
        allocated["daily_sailings"] * allocated["distance_in_cell_m"] / 1000.0
    )
    allocated["ferry_effort_weight_raw"] = allocated["ferry_rider_minutes"]
    allocated["ferry_platform_weight_raw"] = allocated["ferry_vessel_minutes"]
    allocated["allocated_rider_equivalent_diagnostic"] = (
        allocated["daily_riders"] * allocated["distance_fraction"]
    )
    allocated["route_length_km"] = allocated["route_length_m"] / 1000.0
    conservation = validate_allocations(allocated, tolerance=tolerance)
    forbidden = {"target_h3", "normalized_weight", "ferry_effort_weight_normalized"}
    present = sorted(forbidden & set(allocated.columns))
    if present:
        raise FerryEffortError(f"Forbidden post-viewshed/normalized fields present: {present}")
    key = ["service_date", "jurisdiction", "route_key", "source_h3"]
    if allocated.duplicated(key).any():
        raise FerryEffortError(f"Canonical route-level output key is not unique: {key}")
    _validate_nonnegative(
        allocated,
        [
            "daily_riders",
            "daily_sailings",
            "route_duration_minutes",
            "distance_in_cell_m",
            "ferry_rider_minutes",
            "ferry_rider_km",
            "ferry_vessel_minutes",
            "ferry_vessel_km",
        ],
        "route-level allocation",
    )
    return allocated, conservation


def validate_allocations(
    allocated: pd.DataFrame,
    *,
    tolerance: float = TOLERANCE,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    group_columns = ["service_date", "jurisdiction", "route_key"]
    for key, group in allocated.groupby(group_columns, dropna=False):
        riders = group["daily_riders"].iloc[0]
        sailings = group["daily_sailings"].iloc[0]
        duration = float(group["route_duration_minutes"].iloc[0])
        platform_duration = (
            float(group["platform_route_duration_minutes"].iloc[0])
            if "platform_route_duration_minutes" in group
            and pd.notna(group["platform_route_duration_minutes"].iloc[0])
            else duration
        )
        route_length_km = float(group["route_length_km"].iloc[0])
        record: dict[str, Any] = {
            "service_date": pd.Timestamp(key[0]).date().isoformat(),
            "jurisdiction": str(key[1]),
            "route_key": str(key[2]),
        }
        if pd.notna(riders):
            rider_expected = float(riders) * duration
            rider_actual = float(group["ferry_rider_minutes"].sum(min_count=1))
            distance_expected = float(riders) * route_length_km
            distance_actual = float(group["ferry_rider_km"].sum(min_count=1))
            rider_passed = np.isclose(
                rider_actual,
                rider_expected,
                rtol=tolerance,
                atol=tolerance * max(1.0, abs(rider_expected)),
            )
            distance_passed = np.isclose(
                distance_actual,
                distance_expected,
                rtol=tolerance,
                atol=tolerance * max(1.0, abs(distance_expected)),
            )
            if not rider_passed or not distance_passed:
                raise FerryEffortError(
                    f"Daily rider conservation failed for {key}: rider-minutes "
                    f"{rider_actual} != {rider_expected} or rider-km "
                    f"{distance_actual} != {distance_expected}"
                )
            record.update({"rider_minutes_passed": True, "rider_km_passed": True})
        if pd.notna(sailings):
            vessel_expected = float(sailings) * platform_duration
            vessel_actual = float(group["ferry_vessel_minutes"].sum(min_count=1))
            if not np.isclose(
                vessel_actual,
                vessel_expected,
                rtol=tolerance,
                atol=tolerance * max(1.0, abs(vessel_expected)),
            ):
                raise FerryEffortError(
                    f"Daily platform conservation failed for {key}: "
                    f"{vessel_actual} != {vessel_expected}"
                )
            record["vessel_minutes_passed"] = True
        elif group["ferry_vessel_minutes"].notna().any():
            raise FerryEffortError(
                f"Missing sailing count produced non-null vessel exposure for {key}"
            )
        checks.append(record)
    return checks


def collapse_source_cells(route_level: pd.DataFrame) -> pd.DataFrame:
    """Sum additive exposure at service_date x source_h3 grain."""
    collapse_columns = [
        "service_date",
        "source_h3",
        "h3_resolution",
        "route_geometry_id",
        "operator",
        "jurisdiction",
        "ridership_is_estimated",
        "platform_effort_available",
        "ferry_rider_minutes",
        "ferry_rider_hours",
        "ferry_rider_km",
        "ferry_vessel_minutes",
        "ferry_vessel_hours",
        "ferry_vessel_km",
    ]
    work = route_level[collapse_columns].copy()
    estimated = work["ridership_is_estimated"].fillna(False).astype(bool)
    is_wsf = work["jurisdiction"].eq("Washington")
    is_bc = work["jurisdiction"].eq("British Columbia")
    platform_available = work["platform_effort_available"].fillna(False).astype(bool)
    rider_minutes = work["ferry_rider_minutes"]
    work["estimated_rider_minutes"] = rider_minutes.where(estimated, 0.0)
    work["observed_rider_minutes"] = rider_minutes.where(~estimated, 0.0)
    work["wsf_rider_minutes"] = rider_minutes.where(is_wsf, 0.0)
    work["bc_ferries_rider_minutes"] = rider_minutes.where(is_bc, 0.0)
    work["platform_covered_rider_minutes"] = rider_minutes.where(platform_available, 0.0)
    work["has_wsf_effort"] = is_wsf
    work["has_bc_ferry_effort"] = is_bc
    grouped = work.groupby(["service_date", "source_h3"], sort=True, dropna=False, observed=True)
    collapsed = grouped.agg(
        h3_resolution=("h3_resolution", "first"),
        ferry_route_count=("route_geometry_id", "nunique"),
        ferry_operator_count=("operator", "nunique"),
        has_wsf_effort=("has_wsf_effort", "max"),
        has_bc_ferry_effort=("has_bc_ferry_effort", "max"),
        ferry_rider_minutes=("ferry_rider_minutes", _sum_or_null),
        ferry_rider_hours=("ferry_rider_hours", _sum_or_null),
        ferry_rider_km=("ferry_rider_km", _sum_or_null),
        ferry_vessel_minutes=("ferry_vessel_minutes", _sum_or_null),
        ferry_vessel_hours=("ferry_vessel_hours", _sum_or_null),
        ferry_vessel_km=("ferry_vessel_km", _sum_or_null),
        estimated_rider_minutes=("estimated_rider_minutes", _sum_or_null),
        observed_rider_minutes=("observed_rider_minutes", _sum_or_null),
        wsf_rider_minutes=("wsf_rider_minutes", _sum_or_null),
        bc_ferries_rider_minutes=("bc_ferries_rider_minutes", _sum_or_null),
        platform_covered_rider_minutes=("platform_covered_rider_minutes", _sum_or_null),
    ).reset_index()
    collapsed["platform_effort_coverage_fraction"] = np.where(
        collapsed["ferry_rider_minutes"] > 0,
        collapsed["platform_covered_rider_minutes"] / collapsed["ferry_rider_minutes"],
        np.nan,
    )
    collapsed = collapsed.drop(columns="platform_covered_rider_minutes")
    collapsed["ferry_effort_weight_raw"] = collapsed["ferry_rider_minutes"]
    collapsed["ferry_platform_weight_raw"] = collapsed["ferry_vessel_minutes"]
    if collapsed.duplicated(["service_date", "source_h3"]).any():
        raise FerryEffortError("Collapsed service_date x source_h3 key is not unique")
    return collapsed


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    frame.to_parquet(temporary, compression="zstd", index=False)
    os.replace(temporary, path)


def _atomic_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build_ferry_daily_source_weights(
    *,
    wsf_ridership_path: str | Path,
    bc_ridership_path: str | Path,
    route_segments_path: str | Path,
    route_config_path: str | Path,
    output_dir: str | Path,
    wsf_vessel_history_path: str | Path | None = None,
    h3_resolution: int = DEFAULT_H3_RESOLUTION,
    allow_incomplete_routes: bool = False,
) -> PipelineResult:
    """Run the complete raw daily source-cell ferry effort pipeline."""
    paths = {
        "wsf_ridership": Path(wsf_ridership_path),
        "bc_ridership": Path(bc_ridership_path),
        "route_segments": Path(route_segments_path),
        "route_config": Path(route_config_path),
    }
    if wsf_vessel_history_path is not None:
        paths["wsf_vessel_history"] = Path(wsf_vessel_history_path)
    for label, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"{label} input does not exist: {path}")
    if not 0 <= int(h3_resolution) <= 15:
        raise FerryEffortError("H3 resolution must be between 0 and 15")
    output = Path(output_dir)
    unresolved_path = output / "unresolved_routes.json"
    mappings, exclusions = load_route_config(paths["route_config"])
    wsf_sailings = pd.read_parquet(
        paths["wsf_ridership"],
        columns=[
            "service_date",
            "departure_local",
            "sailing_id",
            "route_group",
            "segment_id",
            "origin_terminal",
            "destination_terminal",
            "reported_total_riders",
            "source",
        ],
    )
    wsf_days = standardize_wsf_route_days(wsf_sailings)
    bc_days, bc_checks = standardize_bc_route_days(pd.read_parquet(paths["bc_ridership"]))
    LOGGER.info("standardized WSF route-days: %s", f"{len(wsf_days):,}")
    LOGGER.info("standardized BC route-days: %s", f"{len(bc_days):,}")
    route_days = pd.concat([wsf_days, bc_days], ignore_index=True, sort=False)
    try:
        resolved, unresolved, excluded = resolve_route_days(
            route_days,
            mappings,
            exclusions,
            allow_incomplete_routes=allow_incomplete_routes,
        )
    except UnresolvedRoutesError as error:
        _atomic_json({"unresolved_routes": error.unresolved}, unresolved_path)
        raise
    _atomic_json(
        {"unresolved_routes": unresolved, "explicitly_excluded_routes": excluded},
        unresolved_path,
    )
    LOGGER.info("routes mapped: %s", resolved["route_geometry_id"].nunique())
    LOGGER.info("unresolved routes: %s", len(unresolved))
    segments = gpd.read_parquet(paths["route_segments"])
    used_geometry_ids = set(resolved["route_geometry_id"])
    used_mappings = [m for m in mappings if m.route_geometry_id in used_geometry_ids]
    crosswalk = build_route_h3_crosswalk(segments, used_mappings, h3_resolution=h3_resolution)
    LOGGER.info("route-cell crosswalk rows: %s", f"{len(crosswalk):,}")
    route_level, allocation_checks = allocate_route_days(resolved, crosswalk)
    if "wsf_vessel_history" in paths:
        duration_adjustments = build_wsf_duration_adjustments(
            wsf_sailings,
            pd.read_parquet(paths["wsf_vessel_history"]),
            used_mappings,
        )
        route_level = apply_wsf_duration_adjustments(route_level, duration_adjustments)
        allocation_checks = validate_allocations(route_level)
        LOGGER.info(
            "WSF route-days with vessel-history coverage: %s",
            f"{len(duration_adjustments):,}",
        )
    del wsf_sailings
    LOGGER.info("collapsing route-level rows to service_date x source_h3")
    collapsed = collapse_source_cells(route_level)
    LOGGER.info("collapsed source-cell rows prepared: %s", f"{len(collapsed):,}")
    route_level_path = output / "ferry_source_weights_daily_by_route.parquet"
    collapsed_path = output / "ferry_source_weights_daily.parquet"
    crosswalk_path = output / "ferry_route_source_h3_crosswalk.parquet"
    metadata_path = output / "ferry_source_weights_daily.metadata.json"
    _atomic_parquet(crosswalk, crosswalk_path)
    LOGGER.info("crosswalk parquet written")
    _atomic_parquet(route_level, route_level_path)
    LOGGER.info("route-level parquet written")
    _atomic_parquet(collapsed, collapsed_path)
    LOGGER.info("collapsed parquet written")
    conservation_summary = {
        "crosswalk_routes_checked": int(crosswalk["route_geometry_id"].nunique()),
        "route_days_checked": len(allocation_checks),
        "bc_route_months_checked": len(bc_checks),
        "all_passed": True,
        "maximum_bc_monthly_absolute_error": max(
            (row["absolute_error"] for row in bc_checks), default=0.0
        ),
    }
    metadata = {
        "pipeline_version": PIPELINE_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            label: {"path": str(path), "sha256": file_sha256(path)} for label, path in paths.items()
        },
        "h3_resolution": int(h3_resolution),
        "route_mappings_used": [asdict(mapping) for mapping in used_mappings],
        "unresolved_routes": unresolved,
        "explicitly_excluded_routes": excluded,
        "output_rows": {
            "route_level": len(route_level),
            "collapsed": len(collapsed),
            "crosswalk": len(crosswalk),
        },
        "date_coverage": {
            "minimum": route_level["service_date"].min().date().isoformat(),
            "maximum": route_level["service_date"].max().date().isoformat(),
        },
        "jurisdiction_coverage": sorted(route_level["jurisdiction"].unique()),
        "conservation_tests": conservation_summary,
        "assumptions": [
            "Effort is allocated only to H3 cells with positive ferry-centerline distance.",
            "The primary raw weight is rider-minutes; no normalization is applied.",
            "BC route-hour estimates are collapsed to route-day totals before allocation.",
            "BC platform effort remains null because daily sailing counts are unavailable.",
            "Configured BC route-level riders traverse the complete configured geometry where flagged.",
            "Platform coverage fraction is rider-minute-weighted coverage by records with known sailings.",
            "Where supplied, WSDOT vessel history uses EstArrival minus ActualDepart; it is an operational estimate, not an actual-arrival duration.",
            "Sailings without an eligible exact terminal/time history match retain the configured route duration.",
        ],
    }
    _atomic_json(metadata, metadata_path)
    LOGGER.info("route-level output rows: %s", f"{len(route_level):,}")
    LOGGER.info("collapsed source-cell rows: %s", f"{len(collapsed):,}")
    LOGGER.info(
        "date coverage: %s through %s",
        metadata["date_coverage"]["minimum"],
        metadata["date_coverage"]["maximum"],
    )
    LOGGER.info("conservation results: all passed")
    return PipelineResult(
        route_level_path=route_level_path,
        collapsed_path=collapsed_path,
        crosswalk_path=crosswalk_path,
        metadata_path=metadata_path,
        unresolved_path=unresolved_path,
        route_level_rows=len(route_level),
        collapsed_rows=len(collapsed),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wsf-ridership", required=True, type=Path)
    parser.add_argument("--bc-ridership", required=True, type=Path)
    parser.add_argument("--route-segments", required=True, type=Path)
    parser.add_argument("--route-config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--wsf-vessel-history", type=Path)
    parser.add_argument("--h3-resolution", type=int, default=DEFAULT_H3_RESOLUTION)
    parser.add_argument("--allow-incomplete-routes", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parser().parse_args(argv)
    try:
        result = build_ferry_daily_source_weights(
            wsf_ridership_path=args.wsf_ridership,
            bc_ridership_path=args.bc_ridership,
            route_segments_path=args.route_segments,
            route_config_path=args.route_config,
            output_dir=args.output_dir,
            wsf_vessel_history_path=args.wsf_vessel_history,
            h3_resolution=args.h3_resolution,
            allow_incomplete_routes=args.allow_incomplete_routes,
        )
    except FerryEffortError as error:
        LOGGER.error("%s", error)
        return 1
    LOGGER.info("wrote %s", result.route_level_path)
    LOGGER.info("wrote %s", result.collapsed_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
