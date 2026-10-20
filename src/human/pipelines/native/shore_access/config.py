"""Validated configuration for public shoreline-access products."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from human.utils.config import HumanConfig, require_mapping

DEFAULT_CONFIG_PATH = "config/data/human/accessibility/public_shore_access.yaml"


@dataclass(frozen=True)
class ShoreSource:
    name: str
    source_type: str
    url: str
    provider: str
    license_name: str
    attribution: str
    jurisdiction: str
    fallback_urls: tuple[str, ...]
    required: bool
    feature_type: str | None
    page_size: int
    sort_by: str | None


@dataclass(frozen=True)
class PublicShoreConfig:
    human: HumanConfig
    sources: dict[str, ShoreSource]
    source_completeness: str
    bbox: tuple[float, float, float, float]
    h3_resolution: int
    length_crs: str
    sample_spacing_m: float
    bc_shore_connection_distance_m: float
    osm_shore_connection_distance_m: float
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


def load_public_shore_config(path: str | Path = DEFAULT_CONFIG_PATH) -> PublicShoreConfig:
    human = HumanConfig.load(path, product="public_shore_access", category="accessibility")
    source_section = human.section("sources")
    sources: dict[str, ShoreSource] = {}
    required_names = (
        "wa_public_access_lines",
        "wa_public_access_points",
        "wa_marine_shoreline",
        "osm_shore_access",
    )
    missing_sources = sorted(set(required_names).difference(source_section))
    if missing_sources:
        raise ValueError(f"Missing required public-shore sources: {missing_sources}")
    for name, raw_values in source_section.items():
        if not isinstance(name, str):
            raise ValueError("Public-shore source names must be strings.")
        values = require_mapping(raw_values, f"sources.{name}")
        source_type = str(values.get("type", "arcgis"))
        if source_type not in {"arcgis", "overpass", "wfs"}:
            raise ValueError(f"Unsupported public-shore source type for {name}: {source_type}")
        fallback_values = values.get("fallback_urls", ())
        if isinstance(fallback_values, str):
            fallback_values = (fallback_values,)
        sources[name] = ShoreSource(
            name=name,
            source_type=source_type,
            url=str(values["url"]),
            provider=str(values["provider"]),
            license_name=str(values["license"]),
            attribution=str(values["attribution"]),
            jurisdiction=str(values.get("jurisdiction", "cross_border")),
            fallback_urls=tuple(str(value) for value in fallback_values),
            required=bool(values.get("required", True)),
            feature_type=(str(values["feature_type"]) if values.get("feature_type") else None),
            page_size=int(values.get("page_size", 10_000)),
            sort_by=str(values["sort_by"]) if values.get("sort_by") else None,
        )
        if source_type == "wfs" and not sources[name].feature_type:
            raise ValueError(f"WFS public-shore source {name} requires feature_type.")
        if sources[name].page_size <= 0:
            raise ValueError(f"Public-shore source {name} page_size must be positive.")
    parameters = human.section("parameters")
    resolution = int(parameters.get("h3_resolution", -1))
    if resolution != 7:
        raise ValueError("Public shore access currently requires native H3 resolution 7.")
    completeness = str(parameters.get("source_completeness", "partial"))
    if completeness not in {"complete", "partial"}:
        raise ValueError("public-shore source_completeness must be complete or partial.")
    tile_span = float(parameters.get("overpass_tile_span_degrees", 2.5))
    if tile_span <= 0:
        raise ValueError("overpass_tile_span_degrees must be positive.")
    query_timeout = int(parameters.get("overpass_query_timeout_seconds", 60))
    request_timeout = int(parameters.get("overpass_request_timeout_seconds", 75))
    if query_timeout <= 0 or request_timeout <= 0:
        raise ValueError("Overpass timeout parameters must be positive.")
    bbox_values = human.section("bbox")
    bbox = tuple(float(bbox_values[key]) for key in ("west", "south", "east", "north"))
    bc_shore_connection_distance_m = float(parameters.get("bc_shore_connection_distance_m", 500.0))
    if bc_shore_connection_distance_m <= 0:
        raise ValueError("bc_shore_connection_distance_m must be positive.")
    osm_shore_connection_distance_m = float(
        parameters.get("osm_shore_connection_distance_m", 500.0)
    )
    if osm_shore_connection_distance_m <= 0:
        raise ValueError("osm_shore_connection_distance_m must be positive.")
    return PublicShoreConfig(
        human=human,
        sources=sources,
        source_completeness=completeness,
        bbox=bbox,
        h3_resolution=resolution,
        length_crs=str(parameters.get("length_crs", "EPSG:32610")),
        sample_spacing_m=float(parameters.get("sample_spacing_m", 200.0)),
        bc_shore_connection_distance_m=bc_shore_connection_distance_m,
        osm_shore_connection_distance_m=osm_shore_connection_distance_m,
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
