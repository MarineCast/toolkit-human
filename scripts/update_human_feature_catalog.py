#!/usr/bin/env python3
"""Generate or verify the non-viewshed human feature catalog."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from human.core.config.paths import project_root

CATALOG_PATH = project_root() / "src/human/feature_catalog.yaml"


def _feature(
    name: str,
    *,
    role: str,
    status: str,
    model_eligible: bool,
    unit: str,
) -> dict[str, object]:
    feature: dict[str, object] = {
        "name": name,
        "role": role,
        "measurement_status": status,
        "model_eligible": model_eligible,
        "unit": unit,
    }
    if "LAND_EFFORT_PROXY" in name:
        feature["deprecated_alias"] = True
        feature["replacement"] = name.replace("LAND_EFFORT_PROXY", "LAND_OBSERVATION_OPPORTUNITY")
    elif name == "CALENDAR_EFFORT_WEIGHT":
        feature["deprecated_alias"] = True
        feature["replacement"] = "CALENDAR_CONTEXT_WEIGHT"
    return feature


def _apply_promotion_gates(catalog: dict[str, object]) -> dict[str, object]:
    """Keep partial/research collections out of production model allowlists."""

    collections = catalog.get("collections", {})
    if not isinstance(collections, dict):
        raise ValueError("Human feature catalog collections must be a mapping")
    for collection in collections.values():
        if not isinstance(collection, dict):
            continue
        artifact = str(collection.get("artifact", ""))
        if "land_reporting_opportunity/" in artifact:
            collection["observation_opportunity_schema_version"] = "3.0.0-research"
            collection["artifact_resolution"] = "manifest_dataset_id_single_generation"
            collection["active_manifest"] = (
                "data/processed/domain/human/activity_and_effort/land_reporting_opportunity/manifest.json"
            )
            collection["flat_path_policy"] = "legacy_snapshot_only_do_not_load_as_current"
        if "water_observation_opportunity/" in artifact:
            collection["observation_opportunity_schema_version"] = "3.0.0-research"
            collection["artifact_resolution"] = "manifest_dataset_id_single_generation"
            collection["active_manifest"] = (
                "data/processed/domain/human/activity_and_effort/water_observation_opportunity/manifest.json"
            )
            collection["flat_path_policy"] = "selector_only_do_not_load_flat_path_as_current"
        policy = str(collection.get("model_policy", ""))
        if policy == "partial_source_research_only":
            collection["model_channel"] = "research_only"
            for feature in collection.get("features", []):
                if isinstance(feature, dict):
                    feature["model_eligible"] = False
        elif policy in {"evidence_only", "app_only"}:
            collection["model_channel"] = policy
        else:
            collection["model_channel"] = "candidate"
    return catalog


def build_catalog() -> dict[str, object]:
    population_predictors = [
        _feature(
            "POPULATION", role="predictor", status="derived", model_eligible=True, unit="people"
        ),
        _feature(
            "POPULATION_DENSITY_PER_KM2",
            role="predictor",
            status="derived",
            model_eligible=True,
            unit="people_per_km2",
        ),
        *[
            _feature(
                f"water_weighted_population_{distance}mi",
                role="predictor",
                status="derived",
                model_eligible=True,
                unit="weighted_people",
            )
            for distance in (25, 50, 75, 100)
        ],
    ]
    land_dynamic_streams = [
        "PHYSICAL_VIEWABILITY",
        "POPULATION_TRAVEL_OPPORTUNITY",
        "ROAD_ACCESS_OPPORTUNITY",
        "CITY_ACCESS_OPPORTUNITY",
        "TRANSPORT_ACCESS_OPPORTUNITY",
        "LAND_REACHABILITY_OPPORTUNITY",
        "LAND_EFFORT_PROXY",
        "VERIFIED_LAND_EFFORT_PROXY",
        "LAND_OBSERVATION_OPPORTUNITY",
        "VERIFIED_LAND_OBSERVATION_OPPORTUNITY",
    ]

    def land_dynamic_features(
        period_column: str, *, weekly: bool = False
    ) -> list[dict[str, object]]:
        features = [
            _feature(
                period_column,
                role="support",
                status="derived",
                model_eligible=False,
                unit="date",
            ),
            _feature(
                "H3_INDEX",
                role="support",
                status="derived",
                model_eligible=False,
                unit="h3_cell",
            ),
            _feature(
                "CALENDAR_EFFORT_WEIGHT",
                role="predictor",
                status="derived",
                model_eligible=False,
                unit="relative_weight",
            ),
            _feature(
                "CALENDAR_CONTEXT_WEIGHT",
                role="predictor",
                status="derived",
                model_eligible=False,
                unit="relative_weight",
            ),
            *[
                _feature(
                    name,
                    role="evidence",
                    status="derived",
                    model_eligible=False,
                    unit="kilometres" if name == "DISTANCE_KM_MEAN" else "relative_weight",
                )
                for name in (
                    "DISTANCE_KM_MEAN",
                    "DISTANCE_DETECTION_WEIGHT",
                    "ATMOSPHERIC_VISIBILITY_WEIGHT",
                    "DAYLIGHT_WEIGHT",
                    "WIND_WEIGHT",
                    "PRECIPITATION_WEIGHT",
                    "PUBLIC_SHORE_ACCESS_MAPPED_STATIC_CONTEXT_FRACTION",
                    "PUBLIC_SHORE_ACCESS_VERIFIED_STATIC_CONTEXT_FRACTION",
                )
            ],
            *[
                _feature(
                    name,
                    role="state",
                    status="derived",
                    model_eligible=False,
                    unit="controlled_state",
                )
                for name in (
                    "DISTANCE_DETECTION_STATE",
                    "ATMOSPHERIC_VISIBILITY_STATE",
                    "DAYLIGHT_STATE",
                    "WIND_STATE",
                    "PRECIPITATION_STATE",
                    "CALENDAR_CONTEXT_STATE",
                    "PUBLIC_SHORE_ACCESS_MAPPED_STATE",
                    "PUBLIC_SHORE_ACCESS_VERIFIED_STATE",
                )
            ],
            *[
                _feature(
                    f"{stream}_RAW",
                    role="predictor",
                    status="derived",
                    model_eligible=False,
                    unit="weighted_relative_opportunity",
                )
                for stream in land_dynamic_streams
            ],
            *[
                _feature(
                    f"{stream}_INDEX",
                    role="predictor",
                    status="derived",
                    model_eligible=False,
                    unit="relative_index",
                )
                for stream in land_dynamic_streams
            ],
            *[
                _feature(
                    f"{stream}_DYNAMIC_CONTEXT_COVERAGE",
                    role="coverage",
                    status="derived",
                    model_eligible=False,
                    unit="fraction",
                )
                for stream in land_dynamic_streams
            ],
            *[
                _feature(
                    f"{stream}_STATE",
                    role="state",
                    status="derived",
                    model_eligible=False,
                    unit="controlled_state",
                )
                for stream in land_dynamic_streams
            ],
            *[
                _feature(
                    f"{stream}_{suffix}",
                    role="evidence",
                    status="derived",
                    model_eligible=False,
                    unit="log_relative_opportunity" if suffix == "LOG1P" else "spatial_percentile",
                )
                for stream in (
                    "LAND_OBSERVATION_OPPORTUNITY",
                    "VERIFIED_LAND_OBSERVATION_OPPORTUNITY",
                )
                for suffix in ("LOG1P", "SPATIAL_RANK")
            ],
            _feature(
                "REPORTING_CAPTURE_WEIGHT",
                role="evidence",
                status="unavailable",
                model_eligible=False,
                unit="relative_weight",
            ),
            _feature(
                "REPORTING_CAPTURE_STATE",
                role="state",
                status="unavailable",
                model_eligible=False,
                unit="controlled_state",
            ),
            *[
                _feature(
                    name,
                    role="provenance",
                    status="derived",
                    model_eligible=False,
                    unit="identifier_or_json",
                )
                for name in (
                    "GENERATION_ID",
                    "CONFIG_HASH",
                    "SOURCE_HASHES_JSON",
                    "KNOWLEDGE_TIME_UTC",
                    "SOURCE_VINTAGES_JSON",
                    "HISTORICAL_RECONSTRUCTION",
                    "COMPONENT_PROVENANCE_JSON",
                    "DATA_COVERAGE_STATE",
                    "SOURCE_COVERAGE_STATE",
                )
            ],
            _feature(
                "LAND_EFFORT_PROXY_STATUS",
                role="provenance",
                status="derived",
                model_eligible=False,
                unit="category",
            ),
            _feature(
                "MEASUREMENT_STATUS",
                role="provenance",
                status="derived",
                model_eligible=False,
                unit="category",
            ),
        ]
        if weekly:
            features.extend(
                [
                    _feature(
                        "PERIOD_DAY_COUNT",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="days",
                    ),
                    _feature(
                        "INCOMPLETE_WEEK",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                ]
            )
        return features

    lineage_features = [
        _feature(
            name,
            role="provenance",
            status="derived",
            model_eligible=False,
            unit="identifier_or_json",
        )
        for name in (
            "GENERATION_ID",
            "CONFIG_HASH",
            "SOURCE_HASHES_JSON",
            "KNOWLEDGE_TIME_UTC",
            "SOURCE_VINTAGES_JSON",
            "HISTORICAL_RECONSTRUCTION",
            "COMPONENT_PROVENANCE_JSON",
            "DATA_COVERAGE_STATE",
            "SOURCE_COVERAGE_STATE",
        )
    ]
    water_ais_classes = ("ALL_VESSEL", "PASSENGER", "RECREATIONAL", "COMMERCIAL", "FISHING")
    water_source_components = [
        *[f"{name}_AIS_ACTIVITY_HOURS_PROXY" for name in water_ais_classes],
        "FERRY_RIDER_HOURS",
        "FERRY_PLATFORM_HOURS",
        "WHALE_WATCH_ACTIVITY_HOURS",
    ]
    water_target_components = [
        *[f"WATER_{name}_AIS_OBSERVATION_OPPORTUNITY_RAW" for name in water_ais_classes],
        "WATER_FERRY_RIDER_OBSERVATION_OPPORTUNITY_RAW",
        "WATER_FERRY_PLATFORM_OBSERVATION_OPPORTUNITY_RAW",
        "WATER_WHALE_WATCH_OBSERVATION_OPPORTUNITY_RAW",
    ]

    water_causal_columns = (
        "ALL_VESSEL_AIS_CAUSAL_ROLE",
        "PASSENGER_AIS_CAUSAL_ROLE",
        "RECREATIONAL_AIS_CAUSAL_ROLE",
        "COMMERCIAL_AIS_CAUSAL_ROLE",
        "FISHING_AIS_CAUSAL_ROLE",
        "FERRY_CAUSAL_ROLE",
        "WHALE_WATCH_CAUSAL_ROLE",
    )
    water_coverage_columns = (
        "AIS_SOURCE_TEMPORAL_COVERAGE_FRACTION",
        "AIS_SOURCE_COVERAGE_COMPLETE",
        "AIS_SOURCE_COVERAGE_STATUS",
        "AIS_SOURCE_COVERAGE_STATE",
        "AIS_TEMPORAL_COVERAGE_COMPLETE",
        "AIS_TEMPORAL_COVERAGE_SCOPE",
        "AIS_SPATIAL_COVERAGE_STATE",
        "AIS_RECEIVER_COVERAGE_METHOD",
        "AIS_ABSENT_POLICY",
        "FERRY_SOURCE_COVERAGE_COMPLETE",
        "FERRY_SOURCE_COVERAGE_STATE",
    )

    def water_source_features(
        period_column: str, *, weekly: bool = False
    ) -> list[dict[str, object]]:
        features = [
            _feature(
                period_column, role="support", status="derived", model_eligible=False, unit="date"
            ),
            _feature(
                "H3_INDEX", role="support", status="derived", model_eligible=False, unit="h3_cell"
            ),
            _feature(
                "H3_RESOLUTION",
                role="provenance",
                status="derived",
                model_eligible=False,
                unit="resolution",
            ),
            _feature(
                "AIS_PARENT_H3_R6",
                role="provenance",
                status="derived",
                model_eligible=False,
                unit="h3_cell",
            ),
            _feature(
                "AIS_SPATIAL_ALLOCATION_METHOD",
                role="provenance",
                status="derived",
                model_eligible=False,
                unit="category",
            ),
            *[
                _feature(
                    name,
                    role="evidence",
                    status="unavailable" if name.startswith("WHALE_WATCH") else "derived",
                    model_eligible=False,
                    unit="activity_hours_proxy",
                )
                for name in water_source_components
            ],
            *[
                _feature(
                    f"{name}_STATE",
                    role="state",
                    status="derived",
                    model_eligible=False,
                    unit="controlled_state",
                )
                for name in water_source_components
            ],
            *[
                _feature(
                    name,
                    role="evidence" if name != "DYNAMIC_CONDITION_COVERAGE" else "coverage",
                    status="derived",
                    model_eligible=False,
                    unit="fraction" if name != "VISIBILITY_KM" else "kilometres",
                )
                for name in (
                    "VISIBILITY_KM",
                    "DAYLIGHT_WEIGHT",
                    "WIND_WEIGHT",
                    "PRECIPITATION_WEIGHT",
                    "DYNAMIC_CONDITION_COVERAGE",
                )
            ],
            _feature(
                "SEA_STATE_WEIGHT",
                role="evidence",
                status="unavailable",
                model_eligible=False,
                unit="relative_weight",
            ),
            _feature(
                "REPORTING_CAPTURE_WEIGHT",
                role="evidence",
                status="unavailable",
                model_eligible=False,
                unit="relative_weight",
            ),
            *[
                _feature(
                    name,
                    role="state",
                    status=(
                        "unavailable"
                        if name.startswith(("SEA_STATE", "REPORTING_CAPTURE"))
                        else "derived"
                    ),
                    model_eligible=False,
                    unit="controlled_state",
                )
                for name in (
                    "VISIBILITY_STATE",
                    "DAYLIGHT_STATE",
                    "WIND_STATE",
                    "PRECIPITATION_STATE",
                    "SEA_STATE_STATE",
                    "REPORTING_CAPTURE_STATE",
                )
            ],
            *[
                _feature(
                    name,
                    role=(
                        "coverage" if "STATUS" not in name and "STATE" not in name else "provenance"
                    ),
                    status="derived",
                    model_eligible=False,
                    unit=(
                        "fraction"
                        if "FRACTION" in name
                        else ("boolean" if "COMPLETE" in name else "category")
                    ),
                )
                for name in water_coverage_columns
            ],
            *[
                _feature(
                    name,
                    role="provenance",
                    status="derived",
                    model_eligible=False,
                    unit="causal_role",
                )
                for name in water_causal_columns
            ],
            *lineage_features,
        ]
        if weekly:
            features.extend(
                _feature(
                    f"{name}_AVAILABLE_DAY_COUNT",
                    role="coverage",
                    status="derived",
                    model_eligible=False,
                    unit="days",
                )
                for name in water_source_components
            )
            features.append(
                _feature(
                    "PERIOD_DAY_COUNT",
                    role="coverage",
                    status="derived",
                    model_eligible=False,
                    unit="days",
                )
            )
            features.append(
                _feature(
                    "INCOMPLETE_WEEK",
                    role="coverage",
                    status="derived",
                    model_eligible=False,
                    unit="boolean",
                )
            )
        return features

    def water_target_features(
        period_column: str, *, weekly: bool = False
    ) -> list[dict[str, object]]:
        features = [
            _feature(
                period_column, role="support", status="derived", model_eligible=False, unit="date"
            ),
            _feature(
                "H3_INDEX", role="support", status="derived", model_eligible=False, unit="h3_cell"
            ),
            _feature(
                "H3_RESOLUTION",
                role="provenance",
                status="derived",
                model_eligible=False,
                unit="resolution",
            ),
            _feature(
                "LINE_OF_SIGHT_SUPPORT",
                role="evidence",
                status="unavailable",
                model_eligible=False,
                unit="support_fraction",
            ),
            _feature(
                "PHYSICAL_VIEWABILITY_RAW",
                role="evidence",
                status="unavailable",
                model_eligible=False,
                unit="static_viewability_support",
            ),
            _feature(
                "DISTANCE_KM_MEAN",
                role="evidence",
                status="derived",
                model_eligible=False,
                unit="kilometres",
            ),
            _feature(
                "DISTANCE_DETECTION_WEIGHT",
                role="evidence",
                status="derived",
                model_eligible=False,
                unit="relative_weight",
            ),
            _feature(
                "DISTANCE_ADJUSTED_VIEWABILITY_RAW",
                role="evidence",
                status="derived",
                model_eligible=False,
                unit="legacy_static_viewability_support",
            ),
            *[
                _feature(
                    name,
                    role="state",
                    status=(
                        "unavailable"
                        if name in {"LINE_OF_SIGHT_STATE", "PHYSICAL_VIEWABILITY_STATE"}
                        else "derived"
                    ),
                    model_eligible=False,
                    unit="controlled_state",
                )
                for name in (
                    "LINE_OF_SIGHT_STATE",
                    "PHYSICAL_VIEWABILITY_STATE",
                    "DISTANCE_DETECTION_STATE",
                    "DISTANCE_ADJUSTED_VIEWABILITY_STATE",
                )
            ],
            *[
                _feature(
                    name,
                    role="evidence",
                    status="unavailable" if "WHALE_WATCH" in name else "derived",
                    model_eligible=False,
                    unit="viewshed_condition_weighted_activity",
                )
                for name in water_target_components
            ],
            *[
                _feature(
                    f"{name.removesuffix('_RAW')}_{suffix}",
                    role="evidence",
                    status="unavailable" if "WHALE_WATCH" in name else "derived",
                    model_eligible=False,
                    unit=(
                        "log1p_viewshed_condition_weighted_activity"
                        if suffix == "LOG1P"
                        else "spatial_percentile"
                    ),
                )
                for name in water_target_components
                for suffix in ("LOG1P", "SPATIAL_RANK")
            ],
            *[
                _feature(
                    f"{name.removesuffix('_RAW')}_STATE",
                    role="state",
                    status="derived",
                    model_eligible=False,
                    unit="controlled_state",
                )
                for name in water_target_components
            ],
            *[
                _feature(
                    name,
                    role="evidence" if name != "DYNAMIC_CONDITION_COVERAGE" else "coverage",
                    status="derived",
                    model_eligible=False,
                    unit="fraction",
                )
                for name in (
                    "ATMOSPHERIC_VISIBILITY_WEIGHT",
                    "DAYLIGHT_WEIGHT",
                    "WIND_WEIGHT",
                    "PRECIPITATION_WEIGHT",
                    "DYNAMIC_CONDITION_COVERAGE",
                )
            ],
            *[
                _feature(
                    name,
                    role="state",
                    status="derived",
                    model_eligible=False,
                    unit="controlled_state",
                )
                for name in (
                    "ATMOSPHERIC_VISIBILITY_STATE",
                    "DAYLIGHT_STATE",
                    "WIND_STATE",
                    "PRECIPITATION_STATE",
                )
            ],
            _feature(
                "PRIMARY_WATER_COMPOSITE",
                role="evidence",
                status="unavailable",
                model_eligible=False,
                unit="not_applicable",
            ),
            _feature(
                "PRIMARY_WATER_COMPOSITE_STATE",
                role="state",
                status="unavailable",
                model_eligible=False,
                unit="controlled_state",
            ),
            *[
                _feature(
                    name,
                    role="evidence" if name.endswith("_WEIGHT") else "state",
                    status="unavailable",
                    model_eligible=False,
                    unit="relative_weight" if name.endswith("_WEIGHT") else "controlled_state",
                )
                for name in (
                    "SEA_STATE_WEIGHT",
                    "SEA_STATE_STATE",
                    "REPORTING_CAPTURE_WEIGHT",
                    "REPORTING_CAPTURE_STATE",
                )
            ],
            *[
                _feature(
                    name,
                    role=(
                        "coverage" if "STATUS" not in name and "STATE" not in name else "provenance"
                    ),
                    status="derived",
                    model_eligible=False,
                    unit=(
                        "fraction"
                        if "FRACTION" in name
                        else ("boolean" if "COMPLETE" in name else "category")
                    ),
                )
                for name in water_coverage_columns
            ],
            _feature(
                "PRODUCT_ROLE",
                role="provenance",
                status="derived",
                model_eligible=False,
                unit="product_role",
            ),
            *[
                _feature(
                    name,
                    role="provenance",
                    status="derived",
                    model_eligible=False,
                    unit="causal_role",
                )
                for name in water_causal_columns
            ],
            *lineage_features,
        ]
        if weekly:
            features.extend(
                _feature(
                    f"{name.removesuffix('_RAW')}_AVAILABLE_DAY_COUNT",
                    role="coverage",
                    status="derived",
                    model_eligible=False,
                    unit="days",
                )
                for name in water_target_components
            )
            features.append(
                _feature(
                    "PERIOD_DAY_COUNT",
                    role="coverage",
                    status="derived",
                    model_eligible=False,
                    unit="days",
                )
            )
            features.append(
                _feature(
                    "INCOMPLETE_WEEK",
                    role="coverage",
                    status="derived",
                    model_eligible=False,
                    unit="boolean",
                )
            )
        return features

    catalog = {
        "schema_version": 1,
        "generated_by": "scripts/update_human_feature_catalog.py",
        "measurement_statuses": ["observed", "derived", "estimated", "fallback", "unavailable"],
        "roles": ["predictor", "state", "evidence", "coverage", "provenance", "support", "qc"],
        "default_model_policy": {
            "population_cross_border_r7": [feature["name"] for feature in population_predictors]
        },
        "collections": {
            "calendar_daily": {
                "category": "temporal_context",
                "artifact": "data/processed/domain/human/temporal_context/calendar/calendar_daily.parquet",
                "model_policy": "evidence_only",
                "features": [
                    _feature(
                        "date", role="support", status="derived", model_eligible=False, unit="date"
                    ),
                    _feature(
                        "is_weekend",
                        role="state",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "is_us_holiday",
                        role="state",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "is_ca_holiday",
                        role="state",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "calendar_effort_multiplier",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="multiplier",
                    ),
                    _feature(
                        "calendar_effort_weight",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                ],
            },
            "places_catalog": {
                "category": "demography_and_presence",
                "artifact": "data/processed/domain/human/demography_and_presence/places/places_of_interest.json",
                "model_policy": "app_only",
                "features": [
                    _feature(
                        "latitude",
                        role="support",
                        status="observed",
                        model_eligible=False,
                        unit="degrees_north",
                    ),
                    _feature(
                        "longitude",
                        role="support",
                        status="observed",
                        model_eligible=False,
                        unit="degrees_east",
                    ),
                    _feature(
                        "source",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="identifier",
                    ),
                    _feature(
                        "destination_score",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="heuristic_score",
                    ),
                ],
            },
            "population_cross_border_r7": {
                "category": "demography_and_presence",
                "artifact": "data/processed/domain/human/demography_and_presence/population/cross_border_population_h3_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "eligible_after_temporal_review",
                "features": [
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "H3_RESOLUTION",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="resolution",
                    ),
                    _feature(
                        "COUNTRY_CODE",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="code",
                    ),
                    _feature(
                        "SUBDIVISION_CODE",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="code",
                    ),
                    _feature(
                        "CENSUS_YEAR",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="year",
                    ),
                    *population_predictors,
                ],
            },
            "population_context_r7": {
                "category": "demography_and_presence",
                "artifact": "data/processed/domain/human/demography_and_presence/population/population_context_h3_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "evidence_only",
                "features": [
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "H3_RESOLUTION",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="resolution",
                    ),
                    _feature(
                        "POPULATION",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="people",
                    ),
                    _feature(
                        "POPULATION_LOG1P",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="log1p_people",
                    ),
                    _feature(
                        "POPULATION_US_2020",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="people",
                    ),
                    _feature(
                        "POPULATION_CA_2021",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="people",
                    ),
                    _feature(
                        "COUNTRY_COUNT",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="countries",
                    ),
                    _feature(
                        "COUNTRY_CODES",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="codes",
                    ),
                    _feature(
                        "SUBDIVISION_CODES",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="codes",
                    ),
                    _feature(
                        "CENSUS_YEAR_MIN",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="year",
                    ),
                    _feature(
                        "CENSUS_YEAR_MAX",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="year",
                    ),
                    _feature(
                        "CENSUS_VINTAGE_MIXED_QC",
                        role="qc",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "POPULATION_CONTEXT_AVAILABLE",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "MARINE_TRANSFER_APPLIED",
                        role="state",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "CONTEXT_SCOPE",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "MEASUREMENT_STATUS",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="status",
                    ),
                ],
            },
            "ais_daily_r6": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/ais/ais_activity_daily_r6.parquet",
                "h3_resolution": 6,
                "model_policy": "partial_source_research_only",
                "features": [
                    _feature(
                        "DATE", role="support", status="derived", model_eligible=False, unit="date"
                    ),
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "H3_RESOLUTION",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="resolution",
                    ),
                    _feature(
                        "UNIQUE_VESSELS",
                        role="predictor",
                        status="observed",
                        model_eligible=True,
                        unit="vessels",
                    ),
                    _feature(
                        "ACTIVE_VESSEL_HOURS_PROXY",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="unique_vessel_hours",
                    ),
                    _feature(
                        "OBSERVER_CAPABLE_VESSEL_HOURS_PROXY",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="unique_vessel_hours",
                    ),
                    _feature(
                        "MEAN_SOG",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="knots",
                    ),
                    *[
                        feature
                        for vessel_class in (
                            "FISHING",
                            "TOWING",
                            "RECREATIONAL",
                            "PASSENGER",
                            "CARGO",
                            "TANKER",
                            "UNKNOWN",
                        )
                        for feature in (
                            _feature(
                                f"UNIQUE_{vessel_class}_VESSELS",
                                role="predictor" if vessel_class != "UNKNOWN" else "qc",
                                status="derived",
                                model_eligible=vessel_class != "UNKNOWN",
                                unit="vessels",
                            ),
                            _feature(
                                f"{vessel_class}_VESSEL_HOURS_PROXY",
                                role="predictor" if vessel_class != "UNKNOWN" else "qc",
                                status="derived",
                                model_eligible=vessel_class != "UNKNOWN",
                                unit="unique_vessel_hours",
                            ),
                        )
                    ],
                    _feature(
                        "VALID_SOG_PING_COUNT",
                        role="coverage",
                        status="observed",
                        model_eligible=False,
                        unit="pings",
                    ),
                    _feature(
                        "INVALID_SOG_PING_COUNT",
                        role="qc",
                        status="derived",
                        model_eligible=False,
                        unit="pings",
                    ),
                    _feature(
                        "INVALID_SOG_GROUP_COUNT",
                        role="qc",
                        status="derived",
                        model_eligible=False,
                        unit="groups",
                    ),
                    _feature(
                        "PING_COUNT",
                        role="coverage",
                        status="observed",
                        model_eligible=False,
                        unit="pings",
                    ),
                    _feature(
                        "OBSERVED_HOURS",
                        role="coverage",
                        status="observed",
                        model_eligible=False,
                        unit="hours",
                    ),
                    _feature(
                        "SOURCE_OBSERVED_HOURS",
                        role="coverage",
                        status="observed",
                        model_eligible=False,
                        unit="hours",
                    ),
                    _feature(
                        "SOURCE_EXPECTED_HOURS",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="hours",
                    ),
                    _feature(
                        "SOURCE_TEMPORAL_COVERAGE_FRACTION",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "SOURCE_COVERAGE_COMPLETE",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "SOURCE_COVERAGE_STATUS",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "UNKNOWN_VESSEL_TYPE_FRACTION",
                        role="qc",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "MEASUREMENT_STATUS",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="status",
                    ),
                ],
            },
            "ais_weekly_r6": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/ais/ais_activity_weekly_r6.parquet",
                "h3_resolution": 6,
                "model_policy": "partial_source_research_only",
                "features": [
                    _feature(
                        "WEEK_START",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="date",
                    ),
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "H3_RESOLUTION",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="resolution",
                    ),
                    _feature(
                        "UNIQUE_VESSELS",
                        role="predictor",
                        status="observed",
                        model_eligible=True,
                        unit="vessels",
                    ),
                    _feature(
                        "ACTIVE_VESSEL_HOURS_PROXY",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="unique_vessel_hours",
                    ),
                    _feature(
                        "OBSERVER_CAPABLE_VESSEL_HOURS_PROXY",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="unique_vessel_hours",
                    ),
                    _feature(
                        "MEAN_SOG",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="knots",
                    ),
                    *[
                        feature
                        for vessel_class in (
                            "FISHING",
                            "TOWING",
                            "RECREATIONAL",
                            "PASSENGER",
                            "CARGO",
                            "TANKER",
                            "UNKNOWN",
                        )
                        for feature in (
                            _feature(
                                f"UNIQUE_{vessel_class}_VESSELS",
                                role="predictor" if vessel_class != "UNKNOWN" else "qc",
                                status="derived",
                                model_eligible=vessel_class != "UNKNOWN",
                                unit="vessels",
                            ),
                            _feature(
                                f"{vessel_class}_VESSEL_HOURS_PROXY",
                                role="predictor" if vessel_class != "UNKNOWN" else "qc",
                                status="derived",
                                model_eligible=vessel_class != "UNKNOWN",
                                unit="unique_vessel_hours",
                            ),
                        )
                    ],
                    _feature(
                        "VALID_SOG_PING_COUNT",
                        role="coverage",
                        status="observed",
                        model_eligible=False,
                        unit="pings",
                    ),
                    _feature(
                        "INVALID_SOG_PING_COUNT",
                        role="qc",
                        status="derived",
                        model_eligible=False,
                        unit="pings",
                    ),
                    _feature(
                        "INVALID_SOG_GROUP_COUNT",
                        role="qc",
                        status="derived",
                        model_eligible=False,
                        unit="groups",
                    ),
                    _feature(
                        "PING_COUNT",
                        role="coverage",
                        status="observed",
                        model_eligible=False,
                        unit="pings",
                    ),
                    _feature(
                        "OBSERVED_HOURS",
                        role="coverage",
                        status="observed",
                        model_eligible=False,
                        unit="hours",
                    ),
                    _feature(
                        "SOURCE_OBSERVED_HOURS",
                        role="coverage",
                        status="observed",
                        model_eligible=False,
                        unit="hours",
                    ),
                    _feature(
                        "SOURCE_EXPECTED_HOURS",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="hours",
                    ),
                    _feature(
                        "SOURCE_TEMPORAL_COVERAGE_FRACTION",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "SOURCE_COVERAGE_COMPLETE",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "SOURCE_COVERAGE_STATUS",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "UNKNOWN_VESSEL_TYPE_FRACTION",
                        role="qc",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "MEASUREMENT_STATUS",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="status",
                    ),
                ],
            },
            "water_ais_reporting_opportunity_weekly_r6": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/observer_effort/water_ais_reporting_opportunity_weekly_r6.parquet",
                "h3_resolution": 6,
                "model_policy": "partial_source_research_only",
                "product_role": "deprecated_compatibility_sensitivity",
                "replacement": "water_observation_components_weekly_r6",
                "features": [
                    _feature(
                        "WEEK_START",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="date",
                    ),
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "H3_RESOLUTION",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="resolution",
                    ),
                    _feature(
                        "WATER_AIS_REPORTING_OPPORTUNITY_RAW",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="viewshed_weighted_unique_vessel_hours",
                    ),
                    _feature(
                        "WATER_AIS_REPORTING_OPPORTUNITY_INDEX",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="index_0_1",
                    ),
                    _feature(
                        "WATER_RECREATIONAL_VIEWABILITY_RAW",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="viewshed_weighted_unique_vessel_hours",
                    ),
                    _feature(
                        "WATER_PASSENGER_VIEWABILITY_RAW",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="viewshed_weighted_unique_vessel_hours",
                    ),
                    _feature(
                        "WATER_ALL_VESSEL_VIEWABILITY_RAW",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="viewshed_weighted_unique_vessel_hours",
                    ),
                    _feature(
                        "AIS_SOURCE_R6_WITH_OBSERVER_ACTIVITY",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cells",
                    ),
                    _feature(
                        "SOURCE_TEMPORAL_COVERAGE_FRACTION",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "SOURCE_COVERAGE_COMPLETE",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "SOURCE_COVERAGE_STATUS",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "WATER_VIEWABILITY_STATUS",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "MEASUREMENT_STATUS",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="status",
                    ),
                ],
            },
            "water_observation_source_components_daily_r7": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/water_observation_opportunity/water_observation_source_components_daily_r7.parquet",
                "h3_resolution": 7,
                "temporal_grain": "daily",
                "model_policy": "partial_source_research_only",
                "product_role": "canonical_components",
                "features": water_source_features("DATE"),
            },
            "water_observation_source_components_weekly_r7": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/water_observation_opportunity/water_observation_source_components_weekly_r7.parquet",
                "h3_resolution": 7,
                "temporal_grain": "weekly",
                "model_policy": "partial_source_research_only",
                "product_role": "canonical_components",
                "features": water_source_features("WEEK_START", weekly=True),
            },
            "water_observation_components_daily_r6": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/water_observation_opportunity/water_observation_components_daily_r6.parquet",
                "h3_resolution": 6,
                "temporal_grain": "daily",
                "model_policy": "partial_source_research_only",
                "product_role": "canonical_components_no_primary_composite",
                "features": water_target_features("DATE"),
            },
            "water_observation_components_weekly_r6": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/water_observation_opportunity/water_observation_components_weekly_r6.parquet",
                "h3_resolution": 6,
                "temporal_grain": "weekly",
                "model_policy": "partial_source_research_only",
                "product_role": "canonical_components_no_primary_composite",
                "features": water_target_features("WEEK_START", weekly=True),
            },
            "ferry_route_daily_r7": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/ferry/ferry_effort_daily_by_route_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "evidence_only",
                "features": [
                    _feature(
                        "service_date",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="date",
                    ),
                    _feature(
                        "source_h3",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "route_geometry_id",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="identifier",
                    ),
                    _feature(
                        "ferry_rider_minutes",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="rider_minutes",
                    ),
                    _feature(
                        "ferry_vessel_minutes",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="vessel_minutes",
                    ),
                    _feature(
                        "ferry_vessel_km",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="vessel_km",
                    ),
                    _feature(
                        "has_observed_ridership",
                        role="evidence",
                        status="observed",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "has_estimated_ridership",
                        role="evidence",
                        status="estimated",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "has_duration_fallback",
                        role="qc",
                        status="fallback",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "platform_effort_coverage_fraction",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "source_coverage_complete",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                ],
            },
            "ferry_daily_r7": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/ferry/ferry_effort_daily_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "partial_source_research_only",
                "features": [
                    _feature(
                        "service_date",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="date",
                    ),
                    _feature(
                        "source_h3",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "ferry_rider_minutes",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="rider_minutes",
                    ),
                    _feature(
                        "ferry_vessel_minutes",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="vessel_minutes",
                    ),
                    _feature(
                        "ferry_vessel_km",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="vessel_km",
                    ),
                    _feature(
                        "ferry_route_count",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="routes",
                    ),
                    _feature(
                        "has_observed_ridership",
                        role="evidence",
                        status="observed",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "has_estimated_ridership",
                        role="evidence",
                        status="estimated",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "has_duration_fallback",
                        role="qc",
                        status="fallback",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "platform_effort_coverage_fraction",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "source_coverage_complete",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                ],
            },
            "ferry_weekly_r6": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/ferry/ferry_effort_weekly_r6.parquet",
                "h3_resolution": 6,
                "model_policy": "partial_source_research_only",
                "features": [
                    _feature(
                        "week_start",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="date",
                    ),
                    _feature(
                        "source_h3",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "ferry_rider_minutes",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="rider_minutes",
                    ),
                    _feature(
                        "ferry_vessel_minutes",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="vessel_minutes",
                    ),
                    _feature(
                        "ferry_vessel_km",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="vessel_km",
                    ),
                    _feature(
                        "ferry_route_count",
                        role="predictor",
                        status="derived",
                        model_eligible=True,
                        unit="routes",
                    ),
                    _feature(
                        "has_observed_ridership",
                        role="evidence",
                        status="observed",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "has_estimated_ridership",
                        role="evidence",
                        status="estimated",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "has_duration_fallback",
                        role="qc",
                        status="fallback",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "has_platform_effort",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "source_coverage_complete",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                ],
            },
            "boat_launch_facilities_r7": {
                "category": "accessibility",
                "artifact": "data/processed/domain/human/accessibility/boat_launch_access/boat_launch_facilities_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "partial_source_research_only",
                "features": [
                    _feature(
                        "ACCESS_SITE_ID",
                        role="support",
                        status="observed",
                        model_eligible=False,
                        unit="identifier",
                    ),
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "SOURCE_DATASET",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="identifier",
                    ),
                    _feature(
                        "PUBLIC_ACCESS_STATE",
                        role="state",
                        status="observed",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "RAMP_CAPABILITY_STATE",
                        role="evidence",
                        status="observed",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "SEASONAL_OPERATION_STATE",
                        role="evidence",
                        status="observed",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "SOURCE_COVERAGE_STATUS",
                        role="coverage",
                        status="observed",
                        model_eligible=False,
                        unit="category",
                    ),
                ],
            },
            "boat_launch_access_h3_r7": {
                "category": "accessibility",
                "artifact": "data/processed/domain/human/accessibility/boat_launch_access/boat_launch_access_h3_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "partial_source_research_only",
                "features": [
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "H3_RESOLUTION",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="resolution",
                    ),
                    _feature(
                        "BOAT_LAUNCH_COUNT",
                        role="predictor",
                        status="derived",
                        model_eligible=False,
                        unit="launches",
                    ),
                    _feature(
                        "VERIFIED_PUBLIC_BOAT_LAUNCH_COUNT",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="launches",
                    ),
                    _feature(
                        "BOAT_LAUNCHES_WITH_KNOWN_RAMP_CAPABILITY",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="launches",
                    ),
                    _feature(
                        "BOAT_LAUNCHES_WITH_KNOWN_SEASONAL_OPERATION",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="launches",
                    ),
                    _feature(
                        "SOURCE_DATASET_COUNT",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="sources",
                    ),
                    _feature(
                        "SOURCE_COVERAGE_COMPLETE",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                ],
            },
            "public_shore_facilities_r7": {
                "category": "accessibility",
                "artifact": "data/processed/domain/human/accessibility/public_shore_access/public_shore_facilities_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "partial_source_research_only",
                "features": [
                    _feature(
                        "ACCESS_SITE_ID",
                        role="support",
                        status="observed",
                        model_eligible=False,
                        unit="identifier",
                    ),
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "SOURCE_DATASET",
                        role="provenance",
                        status="observed",
                        model_eligible=False,
                        unit="identifier",
                    ),
                    _feature(
                        "PUBLIC_ACCESS_STATE",
                        role="state",
                        status="observed",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "SOURCE_COVERAGE_STATUS",
                        role="coverage",
                        status="observed",
                        model_eligible=False,
                        unit="category",
                    ),
                ],
            },
            "public_shore_access_h3_r7": {
                "category": "accessibility",
                "artifact": "data/processed/domain/human/accessibility/public_shore_access/public_shore_access_h3_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "partial_source_research_only",
                "features": [
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "H3_RESOLUTION",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="resolution",
                    ),
                    _feature(
                        "TOTAL_MARINE_SHORELINE_M",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="metres",
                    ),
                    _feature(
                        "PUBLIC_ACCESSIBLE_SHORELINE_M",
                        role="predictor",
                        status="derived",
                        model_eligible=False,
                        unit="metres",
                    ),
                    _feature(
                        "ACCESSIBLE_WATERFRONT_FRACTION",
                        role="predictor",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "PUBLIC_ACCESS_STATE",
                        role="state",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "PUBLIC_ACCESS_EVIDENCE_STATE",
                        role="state",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "PUBLIC_ACCESS_SITE_COUNT",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="sites",
                    ),
                    _feature(
                        "VERIFIED_PUBLIC_ACCESS_SITE_COUNT",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="sites",
                    ),
                    _feature(
                        "OSM_EXPLICIT_PUBLIC_ACCESS_SITE_COUNT",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="sites",
                    ),
                    _feature(
                        "OSM_SHORE_CANDIDATE_COUNT",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="sites",
                    ),
                    _feature(
                        "MAPPED_ACCESS_UNKNOWN_SITE_COUNT",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="sites",
                    ),
                    _feature(
                        "RESTRICTED_ACCESS_SITE_COUNT",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="sites",
                    ),
                    _feature(
                        "CONDITIONAL_ACCESS_SITE_COUNT",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="sites",
                    ),
                    _feature(
                        "SOURCE_COVERAGE_COMPLETE",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "ACCESSIBLE_WATERFRONT_RAW_RATIO_QC",
                        role="qc",
                        status="derived",
                        model_eligible=False,
                        unit="ratio",
                    ),
                    _feature(
                        "FRACTION_DENOMINATOR_MISMATCH_QC",
                        role="qc",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                ],
            },
            "land_transport_access_h3_r7": {
                "category": "accessibility",
                "artifact": "data/processed/domain/human/accessibility/land_transport_access/land_transport_access_h3_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "prototype_routing_research_only",
                "features": [
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "H3_RESOLUTION",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="resolution",
                    ),
                    _feature(
                        "ROAD_PROXIMITY_COMPONENT",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "CITY_TRAVEL_ACCESS_COMPONENT",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "LAND_TRANSPORT_ACCESS_OPPORTUNITY_INDEX",
                        role="predictor",
                        status="derived",
                        model_eligible=False,
                        unit="relative_index",
                    ),
                    _feature(
                        "LAND_TRANSPORT_ACCESS_AVAILABLE",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "LAND_TRANSPORT_ACCESS_STATUS",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                ],
            },
            "population_travel_time_h3_r7": {
                "category": "accessibility",
                "artifact": "data/processed/domain/human/accessibility/population_travel_time/population_travel_time_h3_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "prototype_routing_research_only",
                "features": [
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "H3_RESOLUTION",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="resolution",
                    ),
                    _feature(
                        "POPULATION_TRAVEL_DEMAND_COMPONENT_60_MIN",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="relative_index",
                    ),
                    _feature(
                        "POPULATION_TRAVEL_DEMAND_COMPONENT_120_MIN",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="relative_index",
                    ),
                    _feature(
                        "POPULATION_TRAVEL_DEMAND_COMPONENT_240_MIN",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="relative_index",
                    ),
                    _feature(
                        "POPULATION_TRAVEL_OPPORTUNITY_INDEX",
                        role="predictor",
                        status="derived",
                        model_eligible=False,
                        unit="relative_index",
                    ),
                    _feature(
                        "POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                    _feature(
                        "POPULATION_TRAVEL_OPPORTUNITY_STATUS",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                ],
            },
            "land_reporting_source_h3_r7": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/land_reporting_opportunity/land_reporting_source_h3_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "partial_source_research_only",
                "features": [
                    _feature(
                        "source_h3",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX",
                        role="predictor",
                        status="derived",
                        model_eligible=False,
                        unit="relative_index",
                    ),
                    _feature(
                        "VERIFIED_PUBLIC_ACCESS_SUPPORTED_INDEX",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="relative_index",
                    ),
                    _feature(
                        "MAPPED_PUBLIC_ACCESS_SUPPORTED_INDEX",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="relative_index",
                    ),
                    _feature(
                        "PUBLIC_ACCESS_EVIDENCE_STATE",
                        role="state",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                    _feature(
                        "LAND_REACHABILITY_OPPORTUNITY_AVAILABLE",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="boolean",
                    ),
                ],
            },
            "land_reporting_target_h3_r7": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/land_reporting_opportunity/land_reporting_target_h3_r7.parquet",
                "h3_resolution": 7,
                "model_policy": "partial_source_research_only",
                "features": [
                    _feature(
                        "H3_INDEX",
                        role="support",
                        status="derived",
                        model_eligible=False,
                        unit="h3_cell",
                    ),
                    _feature(
                        "LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW",
                        role="predictor",
                        status="derived",
                        model_eligible=False,
                        unit="weighted_relative_opportunity",
                    ),
                    _feature(
                        "LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_INDEX",
                        role="predictor",
                        status="derived",
                        model_eligible=False,
                        unit="relative_index",
                    ),
                    _feature(
                        "VERIFIED_PUBLIC_ACCESS_SUPPORTED_RAW",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="weighted_relative_opportunity",
                    ),
                    _feature(
                        "MAPPED_PUBLIC_ACCESS_SUPPORTED_RAW",
                        role="evidence",
                        status="derived",
                        model_eligible=False,
                        unit="weighted_relative_opportunity",
                    ),
                    _feature(
                        "TRANSPORT_TRAVEL_CONTEXT_COVERAGE",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "MAPPED_ACCESS_CONTEXT_FRACTION",
                        role="coverage",
                        status="derived",
                        model_eligible=False,
                        unit="fraction",
                    ),
                    _feature(
                        "LAND_REPORTING_OPPORTUNITY_STATUS",
                        role="provenance",
                        status="derived",
                        model_eligible=False,
                        unit="category",
                    ),
                ],
            },
            "land_reporting_opportunity_daily_h3_r6": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/land_reporting_opportunity/land_reporting_opportunity_daily_h3_r6.parquet",
                "h3_resolution": 6,
                "temporal_grain": "daily",
                "model_policy": "partial_source_research_only",
                "features": land_dynamic_features("DATE"),
            },
            "land_reporting_opportunity_weekly_h3_r6": {
                "category": "activity_and_effort",
                "artifact": "data/processed/domain/human/activity_and_effort/land_reporting_opportunity/land_reporting_opportunity_weekly_h3_r6.parquet",
                "h3_resolution": 6,
                "temporal_grain": "weekly",
                "model_policy": "partial_source_research_only",
                "features": land_dynamic_features("WEEK_START", weekly=True),
            },
        },
    }
    return _apply_promotion_gates(catalog)


def rendered_catalog() -> str:
    return yaml.safe_dump(build_catalog(), sort_keys=False, allow_unicode=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = rendered_catalog()
    if args.check:
        if not CATALOG_PATH.is_file() or CATALOG_PATH.read_text(encoding="utf-8") != rendered:
            raise SystemExit(
                "Human feature catalog is stale; run scripts/update_human_feature_catalog.py"
            )
        print(f"Human feature catalog is current: {CATALOG_PATH}")
        return 0
    CATALOG_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {CATALOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
