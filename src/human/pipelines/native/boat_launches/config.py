"""Validated configuration for boat-launch access products."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from human.utils.config import HumanConfig, require_mapping

DEFAULT_CONFIG_PATH = "config/data/human/accessibility/boat_launch_access.yaml"


@dataclass(frozen=True)
class AccessSource:
    name: str
    source_type: str
    url: str
    provider: str
    license_name: str
    attribution: str
    jurisdiction: str
    fallback_urls: tuple[str, ...]
    required: bool


@dataclass(frozen=True)
class BoatLaunchConfig:
    human: HumanConfig
    sources: dict[str, AccessSource]
    source_completeness: str
    bbox: tuple[float, float, float, float]
    h3_resolution: int
    overpass_tile_span_degrees: float
    overpass_query_timeout_seconds: int
    overpass_request_timeout_seconds: int
    snapshot_dir: Path
    source_availability_path: Path
    raw_manifest_path: Path
    facilities_path: Path
    h3_path: Path
    manifest_path: Path
    report_path: Path


def load_boat_launch_config(path: str | Path = DEFAULT_CONFIG_PATH) -> BoatLaunchConfig:
    human = HumanConfig.load(path, product="boat_launch_access", category="accessibility")
    source_section = human.section("sources")
    sources: dict[str, AccessSource] = {}
    for name in ("wa_public_access_points", "bc_coastal_boat_launches", "osm_slipways"):
        values = require_mapping(source_section.get(name), f"sources.{name}")
        source_type = str(values.get("type", "arcgis"))
        if source_type not in {"arcgis", "overpass"}:
            raise ValueError(f"Unsupported boat-launch source type for {name}: {source_type}")
        fallback_values = values.get("fallback_urls", ())
        if isinstance(fallback_values, str):
            fallback_values = (fallback_values,)
        sources[name] = AccessSource(
            name=name,
            source_type=source_type,
            url=str(values["url"]),
            provider=str(values["provider"]),
            license_name=str(values["license"]),
            attribution=str(values["attribution"]),
            jurisdiction=str(values.get("jurisdiction", "cross_border")),
            fallback_urls=tuple(str(value) for value in fallback_values),
            required=bool(values.get("required", True)),
        )
    parameters = human.section("parameters")
    resolution = int(parameters.get("h3_resolution", -1))
    if resolution != 7:
        raise ValueError("Boat-launch access currently requires native H3 resolution 7.")
    bbox_values = human.section("bbox")
    bbox = tuple(float(bbox_values[key]) for key in ("west", "south", "east", "north"))
    completeness = str(parameters.get("source_completeness", "partial"))
    if completeness not in {"complete", "partial"}:
        raise ValueError("boat-launch source_completeness must be complete or partial.")
    tile_span = float(parameters.get("overpass_tile_span_degrees", 2.5))
    if tile_span <= 0:
        raise ValueError("overpass_tile_span_degrees must be positive.")
    query_timeout = int(parameters.get("overpass_query_timeout_seconds", 60))
    request_timeout = int(parameters.get("overpass_request_timeout_seconds", 75))
    if query_timeout <= 0 or request_timeout <= 0:
        raise ValueError("Overpass timeout parameters must be positive.")
    return BoatLaunchConfig(
        human=human,
        sources=sources,
        source_completeness=completeness,
        bbox=bbox,
        h3_resolution=resolution,
        overpass_tile_span_degrees=tile_span,
        overpass_query_timeout_seconds=query_timeout,
        overpass_request_timeout_seconds=request_timeout,
        snapshot_dir=human.path_value("raw", "snapshot_dir"),
        source_availability_path=human.path_value("raw", "source_availability_path"),
        raw_manifest_path=human.path_value("raw", "manifest_path"),
        facilities_path=human.path_value("output", "facilities_path"),
        h3_path=human.path_value("output", "h3_path"),
        manifest_path=human.path_value("output", "manifest_path"),
        report_path=human.path_value("inspection", "report_path"),
    )
