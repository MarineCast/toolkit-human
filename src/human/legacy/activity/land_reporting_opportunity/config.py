"""Validated configuration for land reporting-opportunity components and composites."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import Literal

from pydantic import Field

from human.core.config.models import StrictConfig
from human.utils.config import HumanConfig

DEFAULT_CONFIG_PATH = "config/data/human/activity_and_effort/land_reporting_opportunity.yaml"


class _SourceConfig(StrictConfig):
    source_completeness: Literal["complete", "partial"] = "partial"


class _Parameters(StrictConfig):
    start_date: date | None = None
    end_date: date | None = None
    h3_resolution: Literal[7] = 7
    samples_per_site: int = Field(default=20, ge=1)
    max_site_design_points: int = Field(default=80, ge=1)
    site_allocation: Literal["equal_parent_sites"] = "equal_parent_sites"
    scaling_quantile: float = Field(default=0.99, gt=0, le=1)
    dynamic_output_h3_resolution: Literal[6] = 6
    dynamic_distance_bin_km: float = Field(default=0.25, gt=0)
    dynamic_reference_weeks: int = Field(default=104, gt=0)
    dynamic_chunk_days: int = Field(default=92, gt=0)
    visibility_transition_fraction: float = Field(default=0.20, gt=0)
    visibility_minimum_transition_km: float = Field(default=1.0, gt=0)
    wind_support_midpoint_ms: float = 5.5
    wind_support_slope_ms: float = Field(default=1.5, gt=0)
    precipitation_support_half_mm_day: float = Field(default=10.0, gt=0)
    conditions_wind_exponent: float = Field(default=0.70, ge=0)
    conditions_precipitation_exponent: float = Field(default=0.30, ge=0)


class _CompositeConfig(StrictConfig):
    formula_version: Literal["land_reporting_opportunity_v2_access_conditioned"]
    primary_stream: Literal["LAND_EFFORT_PROXY"]
    calendar_applied_streams: tuple[
        Literal[
            "POPULATION_TRAVEL_OPPORTUNITY",
            "ROAD_ACCESS_OPPORTUNITY",
            "CITY_ACCESS_OPPORTUNITY",
            "TRANSPORT_ACCESS_OPPORTUNITY",
            "LAND_REACHABILITY_OPPORTUNITY",
            "LAND_EFFORT_PROXY",
            "VERIFIED_LAND_EFFORT_PROXY",
        ],
        ...,
    ]
    include_potentially_endogenous: Literal[False] = False


class _Pipeline(StrictConfig):
    static_weights_path: Path
    viewshed_config_path: Path
    transport_path: Path
    transport_manifest_path: Path
    population_travel_path: Path
    population_travel_manifest_path: Path
    public_shore_path: Path
    public_shore_manifest_path: Path
    surface_weather_path: Path
    surface_weather_manifest_path: Path
    daylight_path: Path
    daylight_manifest_path: Path
    calendar_path: Path
    calendar_manifest_path: Path


class _Raw(StrictConfig):
    input_inventory_path: Path
    manifest_path: Path


class _Output(StrictConfig):
    source_h3_path: Path
    target_h3_path: Path
    daily_h3_path: Path
    weekly_h3_path: Path
    dynamic_metadata_path: Path
    manifest_path: Path


class _Inspection(StrictConfig):
    report_path: Path


class _LandReportingDocument(StrictConfig):
    schema_version: Literal[1]
    product: Literal["land_reporting_opportunity"]
    category: Literal["activity_and_effort"]
    source: _SourceConfig
    parameters: _Parameters
    composite: _CompositeConfig
    pipeline: _Pipeline
    raw: _Raw
    output: _Output
    inspection: _Inspection


@dataclass(frozen=True)
class LandReportingConfig:
    human: HumanConfig
    source_completeness: str
    h3_resolution: int
    samples_per_site: int
    max_site_design_points: int
    site_allocation: str
    scaling_quantile: float
    dynamic_output_h3_resolution: int
    dynamic_distance_bin_km: float
    dynamic_reference_weeks: int
    dynamic_chunk_days: int
    visibility_transition_fraction: float
    visibility_minimum_transition_km: float
    wind_support_midpoint_ms: float
    wind_support_slope_ms: float
    precipitation_support_half_mm_day: float
    conditions_wind_exponent: float
    conditions_precipitation_exponent: float
    composite_formula_version: str
    primary_stream: str
    calendar_applied_streams: tuple[str, ...]
    include_potentially_endogenous: bool
    static_weights_path: Path
    viewshed_config_path: Path
    transport_path: Path
    transport_manifest_path: Path
    population_travel_path: Path
    population_travel_manifest_path: Path
    public_shore_path: Path
    public_shore_manifest_path: Path
    surface_weather_path: Path
    surface_weather_manifest_path: Path
    daylight_path: Path
    daylight_manifest_path: Path
    calendar_path: Path
    calendar_manifest_path: Path
    input_inventory_path: Path
    raw_manifest_path: Path
    source_output_path: Path
    target_output_path: Path
    daily_output_path: Path
    weekly_output_path: Path
    dynamic_metadata_path: Path
    manifest_path: Path
    report_path: Path
    start_date: date | None = None
    end_date: date | None = None


def load_land_reporting_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    resolve_generation: bool = True,
) -> LandReportingConfig:
    human = HumanConfig.load(
        path,
        product="land_reporting_opportunity",
        category="activity_and_effort",
        extra_keys={"composite"},
    )
    model = human.document.validate_as(_LandReportingDocument)
    parameters = model.parameters
    if (parameters.start_date is None) != (parameters.end_date is None):
        raise ValueError("Land date bounds must both be supplied or both omitted")
    if parameters.start_date and parameters.start_date > parameters.end_date:
        raise ValueError("Land start_date must not follow end_date")
    pipeline = model.pipeline
    output = model.output
    composite = model.composite
    cfg = LandReportingConfig(
        start_date=parameters.start_date,
        end_date=parameters.end_date,
        human=human,
        source_completeness=model.source.source_completeness,
        h3_resolution=parameters.h3_resolution,
        samples_per_site=parameters.samples_per_site,
        max_site_design_points=parameters.max_site_design_points,
        site_allocation=parameters.site_allocation,
        scaling_quantile=parameters.scaling_quantile,
        dynamic_output_h3_resolution=parameters.dynamic_output_h3_resolution,
        dynamic_distance_bin_km=parameters.dynamic_distance_bin_km,
        dynamic_reference_weeks=parameters.dynamic_reference_weeks,
        dynamic_chunk_days=parameters.dynamic_chunk_days,
        visibility_transition_fraction=parameters.visibility_transition_fraction,
        visibility_minimum_transition_km=parameters.visibility_minimum_transition_km,
        wind_support_midpoint_ms=parameters.wind_support_midpoint_ms,
        wind_support_slope_ms=parameters.wind_support_slope_ms,
        precipitation_support_half_mm_day=parameters.precipitation_support_half_mm_day,
        conditions_wind_exponent=parameters.conditions_wind_exponent,
        conditions_precipitation_exponent=parameters.conditions_precipitation_exponent,
        composite_formula_version=composite.formula_version,
        primary_stream=composite.primary_stream,
        calendar_applied_streams=tuple(composite.calendar_applied_streams),
        include_potentially_endogenous=composite.include_potentially_endogenous,
        static_weights_path=human.resolve(pipeline.static_weights_path),
        viewshed_config_path=human.resolve(pipeline.viewshed_config_path),
        transport_path=human.resolve(pipeline.transport_path),
        transport_manifest_path=human.resolve(pipeline.transport_manifest_path),
        population_travel_path=human.resolve(pipeline.population_travel_path),
        population_travel_manifest_path=human.resolve(pipeline.population_travel_manifest_path),
        public_shore_path=human.resolve(pipeline.public_shore_path),
        public_shore_manifest_path=human.resolve(pipeline.public_shore_manifest_path),
        surface_weather_path=human.resolve(pipeline.surface_weather_path),
        surface_weather_manifest_path=human.resolve(pipeline.surface_weather_manifest_path),
        daylight_path=human.resolve(pipeline.daylight_path),
        daylight_manifest_path=human.resolve(pipeline.daylight_manifest_path),
        calendar_path=human.resolve(pipeline.calendar_path),
        calendar_manifest_path=human.resolve(pipeline.calendar_manifest_path),
        input_inventory_path=human.resolve(model.raw.input_inventory_path),
        raw_manifest_path=human.resolve(model.raw.manifest_path),
        source_output_path=human.resolve(output.source_h3_path),
        target_output_path=human.resolve(output.target_h3_path),
        daily_output_path=human.resolve(output.daily_h3_path),
        weekly_output_path=human.resolve(output.weekly_h3_path),
        dynamic_metadata_path=human.resolve(output.dynamic_metadata_path),
        manifest_path=human.resolve(output.manifest_path),
        report_path=human.resolve(model.inspection.report_path),
    )
    if resolve_generation:
        generations = import_module(f"{__package__}.generations")
        cfg = generations.resolve_outputs(cfg)
    return cfg
