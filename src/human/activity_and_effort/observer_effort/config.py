"""Validated configuration for water AIS reporting-opportunity products."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from human.utils.config import HumanConfig

DEFAULT_CONFIG_PATH = "config/data/human/activity_and_effort/observer_effort.yaml"


@dataclass(frozen=True)
class ObserverEffortConfig:
    human: HumanConfig
    source_completeness: str
    source_h3_resolution: int
    viewshed_h3_resolution: int
    output_h3_resolution: int
    primary_activity_column: str
    component_activity_columns: tuple[str, ...]
    scaling_quantile: float
    scaling_reference_weeks: int
    ais_weekly_path: Path
    ais_manifest_path: Path
    water_static_weights_path: Path
    viewshed_config_path: Path
    weekly_path: Path
    metadata_path: Path
    manifest_path: Path
    report_path: Path


def load_observer_effort_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> ObserverEffortConfig:
    human = HumanConfig.load(path, product="observer_effort", category="activity_and_effort")
    source = human.section("source")
    completeness = str(source.get("source_completeness", "partial"))
    if completeness not in {"complete", "partial"}:
        raise ValueError("Observer-effort source_completeness must be complete or partial.")
    parameters = human.section("parameters")
    source_resolution = int(parameters.get("source_h3_resolution", -1))
    viewshed_resolution = int(parameters.get("viewshed_h3_resolution", -1))
    output_resolution = int(parameters.get("output_h3_resolution", -1))
    if (source_resolution, viewshed_resolution, output_resolution) != (6, 7, 6):
        raise ValueError("The water AIS observer-effort contract requires H3 R6 -> R7 -> R6.")
    primary = str(parameters.get("primary_activity_column", "")).strip()
    components = tuple(
        str(value).strip() for value in parameters.get("component_activity_columns", [])
    )
    if not primary or not components or primary in components:
        raise ValueError(
            "Observer-effort activity columns must define one primary and distinct components."
        )
    quantile = float(parameters.get("scaling_quantile", 0.99))
    reference_weeks = int(parameters.get("scaling_reference_weeks", 104))
    if not 0 < quantile <= 1 or reference_weeks < 1:
        raise ValueError("Observer-effort scaling settings are invalid.")
    pipeline = human.section("pipeline")
    return ObserverEffortConfig(
        human=human,
        source_completeness=completeness,
        source_h3_resolution=source_resolution,
        viewshed_h3_resolution=viewshed_resolution,
        output_h3_resolution=output_resolution,
        primary_activity_column=primary,
        component_activity_columns=components,
        scaling_quantile=quantile,
        scaling_reference_weeks=reference_weeks,
        ais_weekly_path=human.resolve(pipeline["ais_weekly_path"]),
        ais_manifest_path=human.resolve(pipeline["ais_manifest_path"]),
        water_static_weights_path=human.resolve(pipeline["water_static_weights_path"]),
        viewshed_config_path=human.resolve(pipeline["viewshed_config_path"]),
        weekly_path=human.path_value("output", "weekly_path"),
        metadata_path=human.path_value("output", "metadata_path"),
        manifest_path=human.path_value("output", "manifest_path"),
        report_path=human.path_value("inspection", "report_path"),
    )
