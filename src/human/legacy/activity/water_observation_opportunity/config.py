"""Strict configuration for water observation-opportunity schema-v3 products."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from pydantic import Field

from human.core.config.models import StrictConfig
from human.utils.config import HumanConfig

DEFAULT_CONFIG_PATH = "config/data/human/activity_and_effort/water_observation_opportunity.yaml"
OUTPUT_FIELDS = {
    "source_daily_h3_r7": "source_daily_path",
    "source_weekly_h3_r7": "source_weekly_path",
    "target_daily_h3_r6": "target_daily_path",
    "target_weekly_h3_r6": "target_weekly_path",
    "static_pair_contract": "static_contract_path",
    "metadata": "metadata_path",
    "sighting_detail": "sighting_detail_path",
    "sighting_summary": "sighting_summary_path",
    "border_matches": "border_matches_path",
    "border_summary": "border_summary_path",
    "report": "report_artifact_path",
}
LEGACY_SOURCE_OUTPUT_KEYS = {
    "source_daily_h3_r7": "source_daily_h3_r6",
    "source_weekly_h3_r7": "source_weekly_h3_r6",
}


class _SourceConfig(StrictConfig):
    source_completeness: Literal["complete", "partial"] = "partial"


class _BorderCalipers(StrictConfig):
    population_travel: float = Field(default=0.35, gt=0)
    road_access: float = Field(default=0.35, gt=0)
    transport_access: float = Field(default=0.35, gt=0)
    shoreline_length: float = Field(default=0.35, gt=0)
    physical_viewability: float = Field(default=0.35, gt=0)


class _CausalRoles(StrictConfig):
    ferry: Literal["exogenous"] = "exogenous"
    passenger_ais: Literal["mixed"] = "mixed"
    all_vessel_ais: Literal["mixed"] = "mixed"
    recreational_ais: Literal["mixed", "potentially_endogenous"] = "mixed"
    commercial_ais: Literal["exogenous"] = "exogenous"
    fishing_ais: Literal["exogenous"] = "exogenous"
    whale_watch: Literal["potentially_endogenous"] = "potentially_endogenous"


class _DistanceCurveAlternative(StrictConfig):
    label: str = Field(min_length=1)
    selected_model: Literal["logistic"] = "logistic"
    logistic_d50_km: float = Field(gt=0)
    logistic_slope_km: float = Field(gt=0)
    normalize_at_zero: bool = True
    hard_cutoff_km: float = Field(gt=0)


class _Parameters(StrictConfig):
    source_h3_resolution: Literal[7] = 7
    viewshed_h3_resolution: Literal[7] = 7
    output_h3_resolution: Literal[6] = 6
    distance_bin_km: float = Field(default=0.5, gt=0)
    visibility_transition_fraction: float = Field(default=0.20, gt=0)
    visibility_minimum_transition_km: float = Field(default=1.0, gt=0)
    wind_support_midpoint_ms: float = 5.5
    wind_support_slope_ms: float = Field(default=1.5, gt=0)
    precipitation_support_half_mm_day: float = Field(default=10.0, gt=0)
    conditions_wind_exponent: float = Field(default=0.70, ge=0)
    conditions_precipitation_exponent: float = Field(default=0.30, ge=0)
    border_distance_bands_km: tuple[float, ...] = (15.0, 30.0, 50.0)
    border_minimum_matches: int = Field(default=20, gt=0)
    border_calipers: _BorderCalipers = _BorderCalipers()
    causal_roles: _CausalRoles = _CausalRoles()
    distance_curve_sensitivity: tuple[_DistanceCurveAlternative, ...]


class _CoverageConfig(StrictConfig):
    ais_temporal_scope: Literal["source_daily_global"] = "source_daily_global"
    ais_spatial_coverage_state: Literal["unknown", "source_unavailable"] = "unknown"
    ais_receiver_coverage_method: Literal["unavailable"] = "unavailable"
    absent_ais_policy: Literal["unknown_unless_cell_date_complete"] = (
        "unknown_unless_cell_date_complete"
    )


class _Pipeline(StrictConfig):
    ais_daily_path: Path
    ais_manifest_path: Path
    ferry_daily_path: Path
    ferry_manifest_path: Path
    surface_weather_path: Path
    surface_weather_manifest_path: Path
    daylight_path: Path
    daylight_manifest_path: Path
    water_static_weights_path: Path
    viewshed_config_path: Path
    sightings_release_pointer: Path
    land_manifest_path: Path
    public_shore_path: Path


class _Raw(StrictConfig):
    input_inventory_path: Path
    manifest_path: Path


class _Output(StrictConfig):
    source_daily_path: Path
    source_weekly_path: Path
    target_daily_path: Path
    target_weekly_path: Path
    static_contract_path: Path
    metadata_path: Path
    sighting_detail_path: Path
    sighting_summary_path: Path
    border_matches_path: Path
    border_summary_path: Path
    report_artifact_path: Path
    manifest_path: Path


class _Inspection(StrictConfig):
    report_path: Path


class _WaterObservationDocument(StrictConfig):
    schema_version: Literal[1]
    product: Literal["water_observation_opportunity"]
    category: Literal["activity_and_effort"]
    source: _SourceConfig
    parameters: _Parameters
    coverage: _CoverageConfig
    pipeline: _Pipeline
    raw: _Raw
    output: _Output
    inspection: _Inspection


@dataclass(frozen=True)
class WaterObservationConfig:
    human: HumanConfig
    source_completeness: str
    source_h3_resolution: int
    viewshed_h3_resolution: int
    output_h3_resolution: int
    distance_bin_km: float
    visibility_transition_fraction: float
    visibility_minimum_transition_km: float
    wind_support_midpoint_ms: float
    wind_support_slope_ms: float
    precipitation_support_half_mm_day: float
    conditions_wind_exponent: float
    conditions_precipitation_exponent: float
    border_distance_bands_km: tuple[float, ...]
    border_minimum_matches: int
    border_calipers: dict[str, float]
    causal_roles: dict[str, str]
    distance_curve_sensitivity: tuple[dict[str, object], ...]
    ais_temporal_scope: str
    ais_spatial_coverage_state: str
    ais_receiver_coverage_method: str
    absent_ais_policy: str
    ais_daily_path: Path
    ais_manifest_path: Path
    ferry_daily_path: Path
    ferry_manifest_path: Path
    surface_weather_path: Path
    surface_weather_manifest_path: Path
    daylight_path: Path
    daylight_manifest_path: Path
    water_static_weights_path: Path
    viewshed_config_path: Path
    sightings_release_pointer: Path
    land_manifest_path: Path
    public_shore_path: Path
    raw_inventory_path: Path
    raw_manifest_path: Path
    source_daily_path: Path
    source_weekly_path: Path
    target_daily_path: Path
    target_weekly_path: Path
    static_contract_path: Path
    metadata_path: Path
    sighting_detail_path: Path
    sighting_summary_path: Path
    border_matches_path: Path
    border_summary_path: Path
    report_artifact_path: Path
    manifest_path: Path
    report_path: Path


def load_water_observation_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    resolve_generation: bool = True,
) -> WaterObservationConfig:
    human = HumanConfig.load(
        path,
        product="water_observation_opportunity",
        category="activity_and_effort",
        extra_keys={"coverage"},
    )
    model = human.document.validate_as(_WaterObservationDocument)
    parameters = model.parameters
    pipeline = model.pipeline
    output = model.output
    coverage = model.coverage
    cfg = WaterObservationConfig(
        human=human,
        source_completeness=model.source.source_completeness,
        source_h3_resolution=parameters.source_h3_resolution,
        viewshed_h3_resolution=parameters.viewshed_h3_resolution,
        output_h3_resolution=parameters.output_h3_resolution,
        distance_bin_km=parameters.distance_bin_km,
        visibility_transition_fraction=parameters.visibility_transition_fraction,
        visibility_minimum_transition_km=parameters.visibility_minimum_transition_km,
        wind_support_midpoint_ms=parameters.wind_support_midpoint_ms,
        wind_support_slope_ms=parameters.wind_support_slope_ms,
        precipitation_support_half_mm_day=parameters.precipitation_support_half_mm_day,
        conditions_wind_exponent=parameters.conditions_wind_exponent,
        conditions_precipitation_exponent=parameters.conditions_precipitation_exponent,
        border_distance_bands_km=parameters.border_distance_bands_km,
        border_minimum_matches=parameters.border_minimum_matches,
        border_calipers=parameters.border_calipers.model_dump(),
        causal_roles=parameters.causal_roles.model_dump(),
        distance_curve_sensitivity=tuple(
            alternative.model_dump() for alternative in parameters.distance_curve_sensitivity
        ),
        ais_temporal_scope=coverage.ais_temporal_scope,
        ais_spatial_coverage_state=coverage.ais_spatial_coverage_state,
        ais_receiver_coverage_method=coverage.ais_receiver_coverage_method,
        absent_ais_policy=coverage.absent_ais_policy,
        ais_daily_path=human.resolve(pipeline.ais_daily_path),
        ais_manifest_path=human.resolve(pipeline.ais_manifest_path),
        ferry_daily_path=human.resolve(pipeline.ferry_daily_path),
        ferry_manifest_path=human.resolve(pipeline.ferry_manifest_path),
        surface_weather_path=human.resolve(pipeline.surface_weather_path),
        surface_weather_manifest_path=human.resolve(pipeline.surface_weather_manifest_path),
        daylight_path=human.resolve(pipeline.daylight_path),
        daylight_manifest_path=human.resolve(pipeline.daylight_manifest_path),
        water_static_weights_path=human.resolve(pipeline.water_static_weights_path),
        viewshed_config_path=human.resolve(pipeline.viewshed_config_path),
        sightings_release_pointer=human.resolve(pipeline.sightings_release_pointer),
        land_manifest_path=human.resolve(pipeline.land_manifest_path),
        public_shore_path=human.resolve(pipeline.public_shore_path),
        raw_inventory_path=human.resolve(model.raw.input_inventory_path),
        raw_manifest_path=human.resolve(model.raw.manifest_path),
        source_daily_path=human.resolve(output.source_daily_path),
        source_weekly_path=human.resolve(output.source_weekly_path),
        target_daily_path=human.resolve(output.target_daily_path),
        target_weekly_path=human.resolve(output.target_weekly_path),
        static_contract_path=human.resolve(output.static_contract_path),
        metadata_path=human.resolve(output.metadata_path),
        sighting_detail_path=human.resolve(output.sighting_detail_path),
        sighting_summary_path=human.resolve(output.sighting_summary_path),
        border_matches_path=human.resolve(output.border_matches_path),
        border_summary_path=human.resolve(output.border_summary_path),
        report_artifact_path=human.resolve(output.report_artifact_path),
        manifest_path=human.resolve(output.manifest_path),
        report_path=human.resolve(model.inspection.report_path),
    )
    if resolve_generation:
        return resolve_outputs(cfg)
    return cfg


def resolve_outputs(cfg: WaterObservationConfig) -> WaterObservationConfig:
    """Resolve one complete immutable generation without a publication import cycle."""

    if not cfg.manifest_path.exists():
        return cfg
    payload = json.loads(cfg.manifest_path.read_text(encoding="utf-8"))
    if not payload.get("generation_id"):
        return cfg
    outputs = {
        item["dataset_id"].split(".")[-1]: Path(item["path"])
        for item in payload.get("artifacts", [])
    }
    for canonical, legacy in LEGACY_SOURCE_OUTPUT_KEYS.items():
        if canonical not in outputs and legacy in outputs:
            outputs[canonical] = outputs[legacy]
    required = set(OUTPUT_FIELDS).difference({"report"})
    if not required.issubset(outputs):
        raise ValueError("Incomplete water observation generation.")
    if "report" not in outputs:
        outputs["report"] = cfg.report_path
    if len({outputs[key].parent for key in required}) != 1:
        raise ValueError("Mixed water observation generation.")
    return replace(cfg, **{field: outputs[key] for key, field in OUTPUT_FIELDS.items()})
