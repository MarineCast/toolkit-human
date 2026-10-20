"""Validated configuration for population-weighted travel time."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from human.utils.config import HumanConfig

DEFAULT_CONFIG_PATH = "config/data/human/accessibility/population_travel_time.yaml"


@dataclass(frozen=True)
class PopulationTravelConfig:
    human: HumanConfig
    source_path: Path
    source_metadata_path: Path
    source_provider: str
    source_license: str
    source_attribution: str
    h3_resolution: int
    source_completeness: str
    decay_minutes: tuple[int, ...]
    primary_decay_minutes: int
    cap_quantile: float
    minimum_routed_population_fraction: float
    allow_partial_evaluated_lower_bound: bool
    snapshot_path: Path
    snapshot_metadata_path: Path
    raw_manifest_path: Path
    output_path: Path
    manifest_path: Path
    report_path: Path


def load_population_travel_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> PopulationTravelConfig:
    human = HumanConfig.load(
        path,
        product="population_travel_time",
        category="accessibility",
    )
    source = human.section("source")
    parameters = human.section("parameters")
    resolution = int(parameters.get("h3_resolution", -1))
    if resolution != 7:
        raise ValueError("Population travel time currently requires H3 resolution 7.")
    completeness = str(parameters.get("source_completeness", "partial"))
    if completeness not in {"complete", "partial"}:
        raise ValueError("population-travel source_completeness must be complete or partial.")
    decay_minutes = tuple(int(value) for value in parameters.get("decay_minutes", ()))
    primary = int(parameters.get("primary_decay_minutes", 120))
    if not decay_minutes or any(value <= 0 for value in decay_minutes):
        raise ValueError("population-travel decay_minutes must contain positive values.")
    if primary not in decay_minutes:
        raise ValueError("primary_decay_minutes must be present in decay_minutes.")
    cap_quantile = float(parameters.get("cap_quantile", 0.99))
    minimum_fraction = float(parameters.get("minimum_routed_population_fraction", 0.99))
    if not 0 < cap_quantile <= 1 or not 0 < minimum_fraction <= 1:
        raise ValueError("Population-travel fraction parameters must be in (0, 1].")
    return PopulationTravelConfig(
        human=human,
        source_path=human.resolve(str(source["path"])),
        source_metadata_path=human.resolve(str(source["metadata_path"])),
        source_provider=str(source["provider"]),
        source_license=str(source["license"]),
        source_attribution=str(source["attribution"]),
        h3_resolution=resolution,
        source_completeness=completeness,
        decay_minutes=decay_minutes,
        primary_decay_minutes=primary,
        cap_quantile=cap_quantile,
        minimum_routed_population_fraction=minimum_fraction,
        allow_partial_evaluated_lower_bound=bool(
            parameters.get("allow_partial_evaluated_lower_bound", False)
        ),
        snapshot_path=human.path_value("raw", "snapshot_path"),
        snapshot_metadata_path=human.path_value("raw", "snapshot_metadata_path"),
        raw_manifest_path=human.path_value("raw", "manifest_path"),
        output_path=human.path_value("output", "h3_path"),
        manifest_path=human.path_value("output", "manifest_path"),
        report_path=human.path_value("inspection", "report_path"),
    )
