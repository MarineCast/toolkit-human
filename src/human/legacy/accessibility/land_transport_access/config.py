"""Validated configuration for land transport-access evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from human.utils.config import HumanConfig

DEFAULT_CONFIG_PATH = "config/data/human/accessibility/land_transport_access.yaml"


@dataclass(frozen=True)
class LandTransportConfig:
    human: HumanConfig
    source_path: Path
    source_metadata_path: Path
    source_provider: str
    source_license: str
    source_attribution: str
    h3_resolution: int
    source_completeness: str
    road_distance_decay_km: float
    city_travel_time_decay_minutes: float
    snapshot_path: Path
    snapshot_metadata_path: Path
    raw_manifest_path: Path
    output_path: Path
    manifest_path: Path
    report_path: Path


def load_land_transport_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> LandTransportConfig:
    human = HumanConfig.load(path, product="land_transport_access", category="accessibility")
    source = human.section("source")
    parameters = human.section("parameters")
    resolution = int(parameters.get("h3_resolution", -1))
    if resolution != 7:
        raise ValueError("Land transport access currently requires H3 resolution 7.")
    completeness = str(parameters.get("source_completeness", "partial"))
    if completeness not in {"complete", "partial"}:
        raise ValueError("land-transport source_completeness must be complete or partial.")
    road_decay = float(parameters.get("road_distance_decay_km", 5.0))
    city_decay = float(parameters.get("city_travel_time_decay_minutes", 120.0))
    if road_decay <= 0 or city_decay <= 0:
        raise ValueError("Land-transport decay parameters must be positive.")
    return LandTransportConfig(
        human=human,
        source_path=human.resolve(str(source["path"])),
        source_metadata_path=human.resolve(str(source["metadata_path"])),
        source_provider=str(source["provider"]),
        source_license=str(source["license"]),
        source_attribution=str(source["attribution"]),
        h3_resolution=resolution,
        source_completeness=completeness,
        road_distance_decay_km=road_decay,
        city_travel_time_decay_minutes=city_decay,
        snapshot_path=human.path_value("raw", "snapshot_path"),
        snapshot_metadata_path=human.path_value("raw", "snapshot_metadata_path"),
        raw_manifest_path=human.path_value("raw", "manifest_path"),
        output_path=human.path_value("output", "h3_path"),
        manifest_path=human.path_value("output", "manifest_path"),
        report_path=human.path_value("inspection", "report_path"),
    )
