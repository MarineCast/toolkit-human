"""Sightings and WA/BC diagnostics for observation-opportunity components.

These routines are validation-only. They create no absence labels and never
fit, scale, or correct a component from whale observations.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import h3
import numpy as np
import pandas as pd
import polars as pl
from scipy.spatial import cKDTree

from human.activity_and_effort.land_reporting_opportunity.config import (
    load_land_reporting_config,
)
from human.activity_and_effort.observation_opportunity_contract import (
    JoinCoverageReason,
    add_lineage_pandas,
    validate_product_contract,
)
from human.utils.artifacts import sha256_file
from human.utils.release_inputs import resolve_sightings_release_artifact

from .config import WaterObservationConfig
from .pipeline import AIS_TARGET_COLUMNS, FERRY_TARGET_COLUMNS, haversine_km

OPPORTUNITY_COLUMNS = [
    *AIS_TARGET_COLUMNS.values(),
    *FERRY_TARGET_COLUMNS.values(),
    "WATER_WHALE_WATCH_OBSERVATION_OPPORTUNITY_RAW",
]

WATER_DIAGNOSTIC_COMPONENTS = {
    "WATER_LINE_OF_SIGHT_SUPPORT": "LINE_OF_SIGHT_SUPPORT",
    "WATER_PHYSICAL_VIEWABILITY_RAW": "PHYSICAL_VIEWABILITY_RAW",
    "WATER_DISTANCE_DETECTION_WEIGHT": "DISTANCE_DETECTION_WEIGHT",
    "WATER_DISTANCE_ADJUSTED_VIEWABILITY_RAW": "DISTANCE_ADJUSTED_VIEWABILITY_RAW",
    "WATER_ATMOSPHERIC_VISIBILITY_WEIGHT": "ATMOSPHERIC_VISIBILITY_WEIGHT",
    "WATER_DAYLIGHT_WEIGHT": "DAYLIGHT_WEIGHT",
    "WATER_WIND_WEIGHT": "WIND_WEIGHT",
    "WATER_PRECIPITATION_WEIGHT": "PRECIPITATION_WEIGHT",
    "WATER_SEA_STATE_WEIGHT": "SEA_STATE_WEIGHT",
    **{column: column for column in OPPORTUNITY_COLUMNS},
    "WATER_REPORTING_CAPTURE_WEIGHT": "REPORTING_CAPTURE_WEIGHT",
}

WATER_COVERAGE_COLUMNS = (
    "AIS_SOURCE_TEMPORAL_COVERAGE_FRACTION",
    "AIS_TEMPORAL_COVERAGE_COMPLETE",
    "AIS_SOURCE_COVERAGE_COMPLETE",
    "AIS_SOURCE_COVERAGE_STATUS",
    "AIS_SOURCE_COVERAGE_STATE",
    "AIS_TEMPORAL_COVERAGE_SCOPE",
    "AIS_SPATIAL_COVERAGE_STATE",
    "AIS_RECEIVER_COVERAGE_METHOD",
    "AIS_ABSENT_POLICY",
    "FERRY_SOURCE_COVERAGE_COMPLETE",
    "FERRY_SOURCE_COVERAGE_STATE",
    "DYNAMIC_CONDITION_COVERAGE",
    "DATA_COVERAGE_STATE",
    "SOURCE_COVERAGE_STATE",
)


def _state_column_for_component(column: str) -> str:
    if column == "LINE_OF_SIGHT_SUPPORT":
        return "LINE_OF_SIGHT_STATE"
    return f"{column.removesuffix('_RAW').removesuffix('_WEIGHT')}_STATE"


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def _platform_from_payload(payload: Any) -> str:
    """Classify only explicit structured platform fields; free text is ignored."""

    try:
        parsed = json.loads(str(payload)) if not isinstance(payload, Mapping) else payload
    except (json.JSONDecodeError, TypeError, ValueError):
        return "unknown"
    if not isinstance(parsed, Mapping):
        return "unknown"
    platform_keys = {
        "platform",
        "platform_type",
        "observation_platform",
        "observer_platform",
        "survey_platform",
        "vessel_type",
    }
    values = [
        str(value).strip().lower()
        for key, value in parsed.items()
        if str(key).strip().lower() in platform_keys and value not in (None, "")
    ]
    if not values:
        return "unknown"
    value = " ".join(values)
    if "ferry" in value:
        return "ferry"
    if any(token in value for token in ("vessel", "boat", "ship", "watercraft")):
        return "vessel"
    if any(token in value for token in ("shore", "land", "beach", "coast")):
        return "shore"
    return "unknown"


def classify_observation_platforms(
    observations: pd.DataFrame,
    source_records: pd.DataFrame,
) -> pd.Series:
    evidence = source_records[["SOURCE_RECORD_ID", "SOURCE_PAYLOAD"]].copy()
    evidence["PLATFORM"] = evidence["SOURCE_PAYLOAD"].map(_platform_from_payload)
    lookup = evidence.set_index("SOURCE_RECORD_ID")["PLATFORM"].to_dict()

    def classify(ids: Any) -> str:
        if not isinstance(ids, Iterable) or isinstance(ids, (str, bytes)):
            return "unknown"
        values = {lookup.get(str(identifier), "unknown") for identifier in ids}
        values.discard("unknown")
        return values.pop() if len(values) == 1 else "unknown"

    return observations["SOURCE_RECORD_IDS"].map(classify).astype("string")


def join_sighting_components(
    observations: pd.DataFrame,
    water: pd.DataFrame,
    land: pd.DataFrame,
) -> pd.DataFrame:
    """Join by exact local date and H3 without dropping positive observations."""

    keys = ["DATE", "H3_INDEX"]
    for label, frame in (("water", water), ("land", land)):
        if frame.duplicated(keys).any():
            raise ValueError(f"{label} daily components are not unique by DATE and H3_INDEX.")
    result = observations.copy()
    for label, frame in (("WATER", water), ("LAND", land)):
        reason_column = f"{label}_JOIN_COVERAGE_REASON"
        if frame.empty:
            result[reason_column] = JoinCoverageReason.UPSTREAM_UNAVAILABLE.value
            continue
        frame_dates = pd.to_datetime(frame["DATE"], errors="coerce").dt.normalize()
        minimum = frame_dates.min()
        maximum = frame_dates.max()
        supported_cells = set(frame["H3_INDEX"].dropna().astype(str))
        supported_keys = set(zip(frame_dates, frame["H3_INDEX"].astype(str)))

        def reason(row: pd.Series) -> str:
            sighting_date = pd.to_datetime(row["DATE"], errors="coerce")
            cell = row.get("H3_INDEX")
            if pd.isna(sighting_date) or pd.isna(cell):
                return JoinCoverageReason.PROCESSING_FAILURE.value
            sighting_date = sighting_date.normalize()
            cell = str(cell)
            if sighting_date < minimum or sighting_date > maximum:
                return JoinCoverageReason.OUTSIDE_TEMPORAL_COVERAGE.value
            if cell not in supported_cells:
                return JoinCoverageReason.OUTSIDE_SPATIAL_SUPPORT.value
            if (sighting_date, cell) not in supported_keys:
                return JoinCoverageReason.UPSTREAM_UNAVAILABLE.value
            return JoinCoverageReason.MATCHED.value

        result[reason_column] = result.apply(reason, axis=1)
    result = result.merge(water, on=keys, how="left", validate="many_to_one")
    result = result.merge(land, on=keys, how="left", validate="many_to_one")
    if len(result) != len(observations):
        raise ValueError("Sighting component joins changed the positive-observation row count.")
    return result


def apply_platform_pathways(
    detail: pd.DataFrame,
    *,
    component_states: Mapping[str, str],
) -> pd.DataFrame:
    """Mask nonmatching pathways and assign controlled join states."""

    result = detail.copy()
    for component, state in component_states.items():
        rank = f"{component.removesuffix('_RAW')}_SPATIAL_RANK"
        evaluated = (
            result["WATER_PATHWAY_EVALUATED"]
            if component.startswith("WATER_")
            else result["LAND_PATHWAY_EVALUATED"]
        )
        result.loc[~evaluated, [component, rank]] = np.nan
        result.loc[~evaluated, state] = "not_applicable"
        result.loc[evaluated, state] = result.loc[evaluated, state].fillna("unmapped")
    return result


def within_calipers(
    left: pd.Series,
    right: pd.Series,
    *,
    covariates: list[str],
    calipers: Mapping[str, float],
) -> tuple[bool, dict[str, float]]:
    """Evaluate standardized covariate calipers for one cross-border pair."""

    differences = {
        column: abs(float(left[f"_Z_{column}"]) - float(right[f"_Z_{column}"]))
        for column in covariates
    }
    return all(differences[column] <= calipers[column] for column in covariates), differences


def _nearest_coastal_jurisdiction(
    cells: pd.Series,
    public_shore: pd.DataFrame,
) -> tuple[list[str], list[float]]:
    shore = public_shore[public_shore["JURISDICTION"].isin(["WA", "BC"])].copy()
    coordinates = np.asarray([h3.cell_to_latlng(cell) for cell in shore["H3_INDEX"]])
    latitude_scale = 111.32
    longitude_scale = 111.32 * math.cos(math.radians(float(coordinates[:, 0].mean())))
    xy = np.column_stack((coordinates[:, 1] * longitude_scale, coordinates[:, 0] * latitude_scale))
    tree = cKDTree(xy)
    sighting_coordinates = np.asarray([h3.cell_to_latlng(cell) for cell in cells])
    sighting_xy = np.column_stack(
        (
            sighting_coordinates[:, 1] * longitude_scale,
            sighting_coordinates[:, 0] * latitude_scale,
        )
    )
    distances, indices = tree.query(sighting_xy, k=1)
    return shore.iloc[indices]["JURISDICTION"].astype(str).tolist(), distances.tolist()


def _land_daily_for_join() -> tuple[Path, dict[str, str], dict[str, str]]:
    cfg = load_land_reporting_config()
    schema = pl.scan_parquet(cfg.daily_output_path).collect_schema().names()
    candidates = {
        "LAND_PHYSICAL_VIEWABILITY_RAW": "PHYSICAL_VIEWABILITY_RAW",
        "LAND_DISTANCE_DETECTION_WEIGHT": "DISTANCE_DETECTION_WEIGHT",
        "LAND_ATMOSPHERIC_VISIBILITY_WEIGHT": "ATMOSPHERIC_VISIBILITY_WEIGHT",
        "LAND_DAYLIGHT_WEIGHT": "DAYLIGHT_WEIGHT",
        "LAND_WIND_WEIGHT": "WIND_WEIGHT",
        "LAND_PRECIPITATION_WEIGHT": "PRECIPITATION_WEIGHT",
        "LAND_CALENDAR_CONTEXT_WEIGHT": "CALENDAR_CONTEXT_WEIGHT",
        "LAND_POPULATION_TRAVEL_OPPORTUNITY_RAW": "POPULATION_TRAVEL_OPPORTUNITY_RAW",
        "LAND_ROAD_ACCESS_OPPORTUNITY_RAW": "ROAD_ACCESS_OPPORTUNITY_RAW",
        "LAND_TRANSPORT_ACCESS_OPPORTUNITY_RAW": "TRANSPORT_ACCESS_OPPORTUNITY_RAW",
        "LAND_REACHABILITY_OPPORTUNITY_RAW": "LAND_REACHABILITY_OPPORTUNITY_RAW",
        "LAND_PUBLIC_SHORE_ACCESS_MAPPED": ("PUBLIC_SHORE_ACCESS_MAPPED_STATIC_CONTEXT_FRACTION"),
        "LAND_PUBLIC_SHORE_ACCESS_VERIFIED": (
            "PUBLIC_SHORE_ACCESS_VERIFIED_STATIC_CONTEXT_FRACTION"
        ),
        "LAND_OBSERVATION_OPPORTUNITY_RAW": (
            "LAND_OBSERVATION_OPPORTUNITY_RAW"
            if "LAND_OBSERVATION_OPPORTUNITY_RAW" in schema
            else "LAND_EFFORT_PROXY_RAW"
        ),
        "VERIFIED_LAND_OBSERVATION_OPPORTUNITY_RAW": (
            "VERIFIED_LAND_OBSERVATION_OPPORTUNITY_RAW"
            if "VERIFIED_LAND_OBSERVATION_OPPORTUNITY_RAW" in schema
            else "VERIFIED_LAND_EFFORT_PROXY_RAW"
        ),
    }
    mapping = {canonical: source for canonical, source in candidates.items() if source in schema}
    state_fallbacks = {
        "LAND_PUBLIC_SHORE_ACCESS_MAPPED": "PUBLIC_SHORE_ACCESS_MAPPED_STATE",
        "LAND_PUBLIC_SHORE_ACCESS_VERIFIED": "PUBLIC_SHORE_ACCESS_VERIFIED_STATE",
    }
    states: dict[str, str] = {}
    for canonical, source in mapping.items():
        canonical_state = _state_column_for_component(canonical)
        source_state = _state_column_for_component(source)
        if canonical_state in schema:
            states[canonical_state] = canonical_state
        elif source_state in schema:
            states[canonical_state] = source_state
        elif state_fallbacks.get(canonical) in schema:
            states[canonical_state] = state_fallbacks[canonical]
        else:
            raise ValueError(f"Land diagnostic component {source} has no state column.")
    return cfg.daily_output_path, mapping, states


def build_sighting_diagnostics(
    cfg: WaterObservationConfig,
    *,
    lineage: dict[str, Any],
) -> dict[str, Any]:
    observation_artifact = resolve_sightings_release_artifact(
        cfg.sightings_release_pointer,
        "whale.sightings.observations",
        verify_checksum=True,
    )
    source_artifact = resolve_sightings_release_artifact(
        cfg.sightings_release_pointer,
        "whale.sightings.source_records",
        verify_checksum=True,
    )
    observations = pd.read_parquet(
        observation_artifact.path,
        columns=[
            "OBSERVATION_ID",
            "SIGHTING_DATE",
            "SIGHTING_DATE_UTC",
            "SOURCE_EVENT_AT_UTC",
            "SOURCE_TIME_PRECISION",
            "CANONICAL_TIME_SYNTHETIC",
            "LATITUDE",
            "LONGITUDE",
            "SOURCE",
            "SOURCE_RECORD_IDS",
            "PUBLIC_RELEASE_ELIGIBLE",
        ],
    )
    source_records = pd.read_parquet(
        source_artifact.path, columns=["SOURCE_RECORD_ID", "SOURCE_PAYLOAD"]
    )
    observations["PLATFORM"] = classify_observation_platforms(observations, source_records)
    observations["DATE"] = pd.to_datetime(observations["SIGHTING_DATE"]).dt.normalize()
    canonical_timestamp = pd.to_datetime(observations["SIGHTING_DATE_UTC"], utc=True)
    exact_timestamp = pd.to_datetime(observations["SOURCE_EVENT_AT_UTC"], utc=True)
    observations["SIGHTING_TIMESTAMP_UTC"] = canonical_timestamp.where(
        ~observations["SOURCE_TIME_PRECISION"].eq("TIMESTAMP") | exact_timestamp.isna(),
        exact_timestamp,
    )
    observations["H3_INDEX"] = [
        h3.latlng_to_cell(lat, lon, 6)
        for lat, lon in zip(observations["LATITUDE"], observations["LONGITUDE"])
    ]
    public_shore = pd.read_parquet(cfg.public_shore_path, columns=["H3_INDEX", "JURISDICTION"])
    jurisdictions, coast_distance = _nearest_coastal_jurisdiction(
        observations["H3_INDEX"], public_shore
    )
    observations["JURISDICTION"] = jurisdictions
    observations["NEAREST_COASTAL_SOURCE_DISTANCE_KM"] = coast_distance
    observations["JURISDICTION_METHOD"] = "nearest_WA_or_BC_coastal_source"
    observations["YEAR"] = observations["DATE"].dt.year
    observations["H3_CLUSTER_R5"] = observations["H3_INDEX"].map(
        lambda cell: h3.cell_to_parent(cell, 5)
    )
    observations["LAND_PATHWAY_EVALUATED"] = observations["PLATFORM"].isin(["shore", "unknown"])
    observations["WATER_PATHWAY_EVALUATED"] = observations["PLATFORM"].isin(
        ["ferry", "vessel", "unknown"]
    )

    water_schema = pl.scan_parquet(cfg.target_daily_path).collect_schema().names()
    water_mapping = {
        canonical: source
        for canonical, source in WATER_DIAGNOSTIC_COMPONENTS.items()
        if source in water_schema
    }
    water_states: dict[str, str] = {}
    for canonical, source in water_mapping.items():
        source_state = _state_column_for_component(source)
        if source_state not in water_schema:
            raise ValueError(f"Water diagnostic component {source} has no state column.")
        water_states[canonical] = f"{canonical.removesuffix('_RAW').removesuffix('_WEIGHT')}_STATE"
    water_coverage = [column for column in WATER_COVERAGE_COLUMNS if column in water_schema]
    water = (
        pl.scan_parquet(cfg.target_daily_path)
        .select(
            "DATE",
            "H3_INDEX",
            *[pl.col(source).alias(canonical) for canonical, source in water_mapping.items()],
            *[
                pl.col(_state_column_for_component(source)).alias(water_states[canonical])
                for canonical, source in water_mapping.items()
            ],
            *[pl.col(column) for column in water_coverage],
        )
        .with_columns(
            *[
                (
                    pl.col(canonical).rank(method="average").over("DATE")
                    / pl.col(canonical).count().over("DATE")
                ).alias(f"{canonical.removesuffix('_RAW')}_SPATIAL_RANK")
                for canonical in water_mapping
            ]
        )
        .collect(engine="streaming")
        .to_pandas()
    )
    land_path, land_mapping, land_states = _land_daily_for_join()
    land_schema = pl.scan_parquet(land_path).collect_schema().names()
    land_coverage_sources = [
        column
        for column in (
            "DYNAMIC_CONDITION_COVERAGE",
            "DATA_COVERAGE_STATE",
            "SOURCE_COVERAGE_STATE",
            "POPULATION_ORIGIN_EVALUATED_COVERAGE",
        )
        if column in land_schema
    ]
    land = pl.scan_parquet(land_path).select(
        "DATE",
        "H3_INDEX",
        *[pl.col(source).alias(canonical) for canonical, source in land_mapping.items()],
        *[pl.col(source).alias(canonical) for canonical, source in land_states.items()],
        *[pl.col(column).alias(f"LAND_{column}") for column in land_coverage_sources],
    )
    land = (
        land.with_columns(
            *[
                (
                    pl.col(canonical).rank(method="average").over("DATE")
                    / pl.col(canonical).count().over("DATE")
                ).alias(f"{canonical.removesuffix('_RAW')}_SPATIAL_RANK")
                for canonical in land_mapping
            ]
        )
        .collect(engine="streaming")
        .to_pandas()
    )
    detail = join_sighting_components(observations, water, land)
    diagnostic_components = [*water_mapping, *land_mapping]
    component_states = {
        **water_states,
        **{
            canonical: f"{canonical.removesuffix('_RAW').removesuffix('_WEIGHT')}_STATE"
            for canonical in land_mapping
        },
    }
    detail = apply_platform_pathways(detail, component_states=component_states)
    audit_flags: dict[str, pd.Series] = {}
    for column in diagnostic_components:
        rank = f"{column.removesuffix('_RAW')}_SPATIAL_RANK"
        evaluated = (
            detail["WATER_PATHWAY_EVALUATED"]
            if column.startswith("WATER_")
            else detail["LAND_PATHWAY_EVALUATED"]
        )
        base = column.removesuffix("_RAW")
        audit_flags[f"{base}_MISSING_FLAG"] = evaluated & detail[column].isna()
        audit_flags[f"{base}_ZERO_FLAG"] = evaluated & detail[column].eq(0.0)
        for threshold in (0.01, 0.05, 0.10):
            audit_flags[f"{base}_LOWEST_{int(threshold * 100):02d}PCT_FLAG"] = evaluated & detail[
                rank
            ].le(threshold).fillna(False)
    detail = pd.concat([detail, pd.DataFrame(audit_flags, index=detail.index)], axis=1)
    detail = detail.assign(
        SIGHTINGS_RELEASE_ID=observation_artifact.release_id,
        SIGHTINGS_OBSERVATIONS_SHA256=observation_artifact.checksum,
        SIGHTINGS_SOURCE_RECORDS_SHA256=source_artifact.checksum,
    )
    detail = add_lineage_pandas(detail, lineage)
    validate_product_contract(pl.from_pandas(detail), "sighting_diagnostics")
    detail.to_parquet(cfg.sighting_detail_path, index=False, compression="zstd")

    summaries = []
    dimensions = {
        "platform": "PLATFORM",
        "source": "SOURCE",
        "jurisdiction": "JURISDICTION",
        "year": "YEAR",
        "h3_cluster_r5": "H3_CLUSTER_R5",
    }
    for dimension, column in dimensions.items():
        for value, group in detail.groupby(column, dropna=False, observed=True):
            row: dict[str, Any] = {
                "SUMMARY_DIMENSION": dimension,
                "SUMMARY_VALUE": str(value),
                "SIGHTING_COUNT": len(group),
                "PUBLIC_RELEASE_ELIGIBLE_COUNT": int(
                    group["PUBLIC_RELEASE_ELIGIBLE"].fillna(False).sum()
                ),
            }
            for component in diagnostic_components:
                rank = f"{component.removesuffix('_RAW')}_SPATIAL_RANK"
                evaluated = (
                    group["WATER_PATHWAY_EVALUATED"]
                    if component.startswith("WATER_")
                    else group["LAND_PATHWAY_EVALUATED"]
                )
                eligible_count = int(evaluated.sum())
                available_count = int(group.loc[evaluated, component].notna().sum())
                state_column = component_states[component]
                row[f"{component}_EVALUATED_COUNT"] = eligible_count
                row[f"{component}_AVAILABLE_COUNT"] = available_count
                row[f"{component}_AVAILABLE_RATE"] = (
                    available_count / eligible_count if eligible_count else np.nan
                )
                row[f"{rank}_MEDIAN"] = group[rank].median()
                base = component.removesuffix("_RAW")
                for flag in ("MISSING", "ZERO", "LOWEST_01PCT", "LOWEST_05PCT", "LOWEST_10PCT"):
                    row[f"{base}_{flag}_COUNT"] = int(group[f"{base}_{flag}_FLAG"].sum())
                row[f"{base}_STATE_COUNTS_JSON"] = json.dumps(
                    group[state_column].value_counts(dropna=False).sort_index().to_dict(),
                    sort_keys=True,
                    default=str,
                )
                row[f"{base}_OBSERVED_ZERO_COUNT"] = int(
                    group[state_column].eq("observed_zero").sum()
                )
                row[f"{base}_DERIVED_ZERO_COUNT"] = int(
                    group[state_column].eq("derived_zero").sum()
                )
            for pathway in ("LAND", "WATER"):
                reason_column = f"{pathway}_JOIN_COVERAGE_REASON"
                row[f"{pathway}_JOIN_REASON_COUNTS_JSON"] = json.dumps(
                    group[reason_column].value_counts(dropna=False).sort_index().to_dict(),
                    sort_keys=True,
                    default=str,
                )
            summaries.append(row)
    summary = add_lineage_pandas(pd.DataFrame(summaries), lineage)
    _atomic_csv(summary, cfg.sighting_summary_path)
    return {
        "release_id": observation_artifact.release_id,
        "observations_sha256": observation_artifact.checksum,
        "source_records_sha256": source_artifact.checksum,
        "observations": len(detail),
        "public_release_eligible": int(detail["PUBLIC_RELEASE_ELIGIBLE"].sum()),
        "platform_counts": detail["PLATFORM"].value_counts(dropna=False).to_dict(),
    }


def _standardize(frame: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, dict[str, float]]:
    result = frame.copy()
    scales: dict[str, float] = {}
    for column in columns:
        scale = float(result[column].std(ddof=0))
        scales[column] = scale if math.isfinite(scale) and scale > 0 else 1.0
        result[f"_Z_{column}"] = (result[column] - result[column].mean()) / scales[column]
    return result, scales


def build_border_diagnostics(
    cfg: WaterObservationConfig,
    *,
    lineage: dict[str, Any],
) -> dict[str, Any]:
    land_cfg = load_land_reporting_config()
    source = pd.read_parquet(land_cfg.source_output_path)
    shore = pd.read_parquet(
        cfg.public_shore_path,
        columns=["H3_INDEX", "JURISDICTION", "TOTAL_MARINE_SHORELINE_M"],
    )
    static = (
        pl.scan_parquet("data/processed/domain/human/viewshed/RES7/LAND_STATIC_WEIGHTS_R7.parquet")
        .group_by("source_h3")
        .agg(pl.col("weight_static_viewability").sum().alias("PHYSICAL_VIEWABILITY"))
        .collect(engine="streaming")
        .to_pandas()
    )
    frame = source.merge(
        shore,
        left_on="source_h3",
        right_on="H3_INDEX",
        how="inner",
        validate="one_to_one",
    ).merge(static, on="source_h3", how="left", validate="one_to_one")
    frame = frame[frame["JURISDICTION"].isin(["WA", "BC"])].copy()
    evidence_quality = {
        "verified_public": "verified",
        "mapped_public": "mapped_explicit",
        "mapped_access_unknown": "mapped_uncertain",
        "restricted": "restricted",
        "unknown": "unknown",
    }
    frame["ACCESS_EVIDENCE_QUALITY"] = (
        frame["PUBLIC_ACCESS_EVIDENCE_STATE"].map(evidence_quality).fillna("unknown")
    )
    covariates = [
        "POPULATION_TRAVEL_OPPORTUNITY_INDEX",
        "ROAD_PROXIMITY_COMPONENT",
        "LAND_TRANSPORT_ACCESS_OPPORTUNITY_INDEX",
        "TOTAL_MARINE_SHORELINE_M",
        "PHYSICAL_VIEWABILITY",
    ]
    frame["MATCH_ELIGIBLE"] = frame[covariates].notna().all(axis=1) & frame[
        "PUBLIC_SHORE_CONTEXT_MAPPED"
    ].fillna(False)
    ineligible_counts = (
        frame.loc[~frame["MATCH_ELIGIBLE"]].groupby("JURISDICTION", observed=True).size().to_dict()
    )
    frame = frame[frame["MATCH_ELIGIBLE"]].copy()
    standardized, scales = _standardize(frame, covariates)
    wa = standardized[standardized["JURISDICTION"].eq("WA")].copy()
    bc = standardized[standardized["JURISDICTION"].eq("BC")].copy()
    wa_coords = np.asarray([h3.cell_to_latlng(cell) for cell in wa["source_h3"]])
    bc_coords = np.asarray([h3.cell_to_latlng(cell) for cell in bc["source_h3"]])
    latitude_scale = 111.32
    longitude_scale = 111.32 * math.cos(
        math.radians(float(np.concatenate([wa_coords[:, 0], bc_coords[:, 0]]).mean()))
    )
    wa_xy = np.column_stack((wa_coords[:, 1] * longitude_scale, wa_coords[:, 0] * latitude_scale))
    bc_xy = np.column_stack((bc_coords[:, 1] * longitude_scale, bc_coords[:, 0] * latitude_scale))
    tree = cKDTree(bc_xy)
    bands = [float(value) for value in cfg.border_distance_bands_km]
    configured_calipers = cfg.border_calipers
    caliper_lookup = {
        covariates[0]: float(configured_calipers.get("population_travel", 0.35)),
        covariates[1]: float(configured_calipers.get("road_access", 0.35)),
        covariates[2]: float(configured_calipers.get("transport_access", 0.35)),
        covariates[3]: float(configured_calipers.get("shoreline_length", 0.35)),
        covariates[4]: float(configured_calipers.get("physical_viewability", 0.35)),
    }
    matches = []
    summary_rows = []
    for band in bands:
        candidate_lists = tree.query_ball_point(wa_xy, r=band)
        selected_pairs = []
        for wa_position, candidates in enumerate(candidate_lists):
            viable = []
            wa_row = wa.iloc[wa_position]
            for bc_position in candidates:
                bc_row = bc.iloc[bc_position]
                accepted, differences = within_calipers(
                    wa_row,
                    bc_row,
                    covariates=covariates,
                    calipers=caliper_lookup,
                )
                if accepted:
                    distance = float(np.linalg.norm(wa_xy[wa_position] - bc_xy[bc_position]))
                    viable.append((sum(differences.values()), distance, bc_position, differences))
            if not viable:
                wa_row = wa.iloc[wa_position]
                matches.append(
                    {
                        "DISTANCE_BAND_KM": band,
                        "WA_SOURCE_H3": wa_row["source_h3"],
                        "BC_SOURCE_H3": None,
                        "PAIR_DISTANCE_KM": np.nan,
                        "MATCH_STATUS": "unmatched",
                        "UNMATCHED_REASON": (
                            "no_cross_border_cell_within_radius"
                            if not candidates
                            else "covariate_caliper_failure"
                        ),
                        "WA_ACCESS_EVIDENCE_QUALITY": wa_row["ACCESS_EVIDENCE_QUALITY"],
                        "BC_ACCESS_EVIDENCE_QUALITY": None,
                        "WA_SOURCE_COVERAGE_COMPLETE": bool(
                            wa_row.get("SOURCE_COVERAGE_COMPLETE", False)
                        ),
                        "BC_SOURCE_COVERAGE_COMPLETE": None,
                    }
                )
                continue
            _, distance, bc_position, differences = min(viable)
            selected_pairs.append((wa_position, bc_position))
            row = {
                "DISTANCE_BAND_KM": band,
                "WA_SOURCE_H3": wa_row["source_h3"],
                "BC_SOURCE_H3": bc.iloc[bc_position]["source_h3"],
                "PAIR_DISTANCE_KM": distance,
                "MATCH_STATUS": "matched",
                "UNMATCHED_REASON": None,
                "WA_ACCESS_EVIDENCE_QUALITY": wa_row["ACCESS_EVIDENCE_QUALITY"],
                "BC_ACCESS_EVIDENCE_QUALITY": bc.iloc[bc_position]["ACCESS_EVIDENCE_QUALITY"],
                "WA_SOURCE_COVERAGE_COMPLETE": bool(wa_row.get("SOURCE_COVERAGE_COMPLETE", False)),
                "BC_SOURCE_COVERAGE_COMPLETE": bool(
                    bc.iloc[bc_position].get("SOURCE_COVERAGE_COMPLETE", False)
                ),
            }
            for column in covariates:
                row[f"WA_{column}"] = wa_row[column]
                row[f"BC_{column}"] = bc.iloc[bc_position][column]
                row[f"STANDARDIZED_ABS_DIFF_{column}"] = differences[column]
            row["WA_MAPPED_ACCESS"] = wa_row.get("MAPPED_PUBLIC_ACCESS_SUPPORTED_INDEX")
            row["BC_MAPPED_ACCESS"] = bc.iloc[bc_position].get(
                "MAPPED_PUBLIC_ACCESS_SUPPORTED_INDEX"
            )
            matches.append(row)
        for column in covariates:
            pre = (wa[column].mean() - bc[column].mean()) / scales[column]
            if selected_pairs:
                wa_positions, bc_positions = zip(*selected_pairs)
                post = (
                    wa.iloc[list(wa_positions)][column].mean()
                    - bc.iloc[list(bc_positions)][column].mean()
                ) / scales[column]
            else:
                post = np.nan
            summary_rows.append(
                {
                    "DISTANCE_BAND_KM": band,
                    "METRIC": column,
                    "PRE_MATCH_STANDARDIZED_DIFFERENCE": pre,
                    "POST_MATCH_STANDARDIZED_DIFFERENCE": post,
                    "MATCH_COUNT": len(selected_pairs),
                    "WA_ELIGIBLE_DENOMINATOR": len(wa),
                    "BC_ELIGIBLE_DENOMINATOR": len(bc),
                    "WA_INELIGIBLE_COUNT": int(ineligible_counts.get("WA", 0)),
                    "BC_INELIGIBLE_COUNT": int(ineligible_counts.get("BC", 0)),
                    "RESULT_STATUS": (
                        "sufficient_sample"
                        if len(selected_pairs) >= cfg.border_minimum_matches
                        else "insufficient_sample"
                    ),
                    "MINIMUM_MATCH_COUNT": cfg.border_minimum_matches,
                }
            )
        if selected_pairs:
            wa_positions, bc_positions = zip(*selected_pairs)
            for metric in (
                "MAPPED_PUBLIC_ACCESS_SUPPORTED_INDEX",
                "VERIFIED_PUBLIC_ACCESS_SUPPORTED_INDEX",
            ):
                summary_rows.append(
                    {
                        "DISTANCE_BAND_KM": band,
                        "METRIC": metric,
                        "PRE_MATCH_STANDARDIZED_DIFFERENCE": np.nan,
                        "POST_MATCH_STANDARDIZED_DIFFERENCE": (
                            wa.iloc[list(wa_positions)][metric].mean()
                            - bc.iloc[list(bc_positions)][metric].mean()
                        ),
                        "MATCH_COUNT": len(selected_pairs),
                        "WA_ELIGIBLE_DENOMINATOR": len(wa),
                        "BC_ELIGIBLE_DENOMINATOR": len(bc),
                        "WA_INELIGIBLE_COUNT": int(ineligible_counts.get("WA", 0)),
                        "BC_INELIGIBLE_COUNT": int(ineligible_counts.get("BC", 0)),
                        "RESULT_STATUS": (
                            "sufficient_sample"
                            if len(selected_pairs) >= cfg.border_minimum_matches
                            else "insufficient_sample"
                        ),
                        "MINIMUM_MATCH_COUNT": cfg.border_minimum_matches,
                    }
                )
    match_frame = add_lineage_pandas(pd.DataFrame(matches), lineage)
    if match_frame.empty:
        match_frame = add_lineage_pandas(
            pd.DataFrame(
                columns=[
                    "DISTANCE_BAND_KM",
                    "WA_SOURCE_H3",
                    "BC_SOURCE_H3",
                    "PAIR_DISTANCE_KM",
                    "MATCH_STATUS",
                    "UNMATCHED_REASON",
                ]
            ),
            lineage,
        )
    match_frame.to_parquet(cfg.border_matches_path, index=False, compression="zstd")
    summary = add_lineage_pandas(pd.DataFrame(summary_rows), lineage)
    _atomic_csv(summary, cfg.border_summary_path)
    return {
        "bands_km": bands,
        "candidate_wa_cells": len(wa),
        "candidate_bc_cells": len(bc),
        "matched_pairs": int(
            match_frame.get("MATCH_STATUS", pd.Series(dtype=str)).eq("matched").sum()
        ),
        "audit_rows": len(match_frame),
        "minimum_match_count": cfg.border_minimum_matches,
        "result_by_band": {
            str(band): (
                "sufficient_sample"
                if int(
                    match_frame.loc[match_frame["DISTANCE_BAND_KM"].eq(band), "MATCH_STATUS"]
                    .eq("matched")
                    .sum()
                )
                >= cfg.border_minimum_matches
                else "insufficient_sample"
            )
            for band in bands
        },
        "correction_applied": False,
    }


def build_diagnostics(
    cfg: WaterObservationConfig,
    *,
    lineage: dict[str, Any],
) -> dict[str, Any]:
    sightings = build_sighting_diagnostics(cfg, lineage=lineage)
    border = build_border_diagnostics(cfg, lineage=lineage)
    return {"sightings": sightings, "wa_bc": border}
