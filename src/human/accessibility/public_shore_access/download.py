"""Acquire WA shoreline-access evidence and supplemental OSM access features."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import geopandas as gpd

from human.utils.arcgis import query_arcgis_geojson
from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_json,
    atomic_write_parquet,
    manifest_payload,
    source_record,
    write_manifest,
)
from human.utils.osm import query_overpass_points_batched
from human.utils.wfs import query_wfs_geojson

from .config import DEFAULT_CONFIG_PATH, ShoreSource, load_public_shore_config

OSM_SELECTORS = (
    '["waterway"="access_point"]',
    '["natural"="beach"]',
    '["leisure"="beach_resort"]',
    '["leisure"="slipway"]',
    '["highway"="trailhead"]',
    '["tourism"="viewpoint"]',
)


def _acquire(
    source: ShoreSource,
    bbox: tuple[float, float, float, float],
    *,
    overpass_tile_span_degrees: float = 2.5,
    overpass_query_timeout_seconds: int = 60,
    overpass_request_timeout_seconds: int = 75,
) -> gpd.GeoDataFrame:
    if source.source_type == "arcgis":
        return query_arcgis_geojson(source.url, bbox=bbox)
    if source.source_type == "wfs":
        return query_wfs_geojson(
            source.url,
            source.feature_type or "",
            bbox,
            page_size=source.page_size,
            timeout_seconds=overpass_request_timeout_seconds,
            sort_by=source.sort_by,
        )
    return query_overpass_points_batched(
        (source.url, *source.fallback_urls),
        bbox,
        OSM_SELECTORS,
        maximum_span_degrees=overpass_tile_span_degrees,
        query_timeout_seconds=overpass_query_timeout_seconds,
        request_timeout_seconds=overpass_request_timeout_seconds,
    )


def download(config_path: str | Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    cfg = load_public_shore_config(config_path)
    artifacts = []
    sources = []
    statuses: set[MeasurementStatus] = {MeasurementStatus.UNAVAILABLE}
    source_availability: dict[str, dict[str, object]] = {}
    prior_availability: dict[str, object] = {}
    if cfg.source_availability_path.is_file():
        loaded = json.loads(cfg.source_availability_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            prior_availability = loaded
    prior_sources = prior_availability.get("sources", {})
    if not isinstance(prior_sources, dict):
        prior_sources = {}
    for source in cfg.sources.values():
        snapshot = cfg.snapshot_dir / f"{source.name}.parquet"
        prior = prior_sources.get(source.name, {})
        prior_status = prior.get("status") if isinstance(prior, dict) else None
        reuse = snapshot.exists() and not overwrite and prior_status != "unavailable"
        if reuse:
            frame = gpd.read_parquet(snapshot)
            if not source.required and frame.empty and prior_status is None:
                reuse = False
        status = MeasurementStatus.OBSERVED
        error: str | None = None
        if not reuse:
            try:
                frame = _acquire(
                    source,
                    cfg.bbox,
                    overpass_tile_span_degrees=cfg.overpass_tile_span_degrees,
                    overpass_query_timeout_seconds=cfg.overpass_query_timeout_seconds,
                    overpass_request_timeout_seconds=cfg.overpass_request_timeout_seconds,
                )
            except Exception as exc:
                if source.required:
                    raise
                status = MeasurementStatus.UNAVAILABLE
                error = f"{type(exc).__name__}: {exc}"
                frame = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
            atomic_write_parquet(frame, snapshot, overwrite=snapshot.exists())
        else:
            status = (
                MeasurementStatus.UNAVAILABLE
                if prior_status == "unavailable"
                else MeasurementStatus.OBSERVED
            )
        statuses.add(status)
        source_availability[source.name] = {
            "status": status.value,
            "required": source.required,
            "records": int(len(frame)),
            "url": source.url,
            "fallback_urls": list(source.fallback_urls),
            "error": error,
        }
        artifacts.append(
            artifact_record(
                snapshot,
                dataset_id=f"human.accessibility.public_shore_access.raw.{source.name}",
                frame=frame,
            )
        )
        sources.append(
            source_record(
                snapshot,
                name=source.name,
                provider=source.provider,
                license_name=source.license_name,
                attribution=source.attribution,
                url=source.url,
                measurement_status=status,
                spatial={"jurisdiction": source.jurisdiction, "bbox_wgs84": cfg.bbox},
            )
        )

    bc_site_status = source_availability.get("bc_recreation_sites", {}).get("status")
    bc_shore_status = source_availability.get("bc_shorezone_lines", {}).get("status")
    bc_authoritative_available = (
        bc_site_status == MeasurementStatus.OBSERVED.value
        and bc_shore_status == MeasurementStatus.OBSERVED.value
    )
    availability = {
        "WA": {
            "status": "partial",
            "authoritative_facilities": "wa_public_access_points",
            "authoritative_access_boundaries": "wa_public_access_lines",
            "shoreline_denominator": "wa_marine_shoreline",
        },
        "BC": {
            "status": (
                "partial" if bc_authoritative_available else MeasurementStatus.UNAVAILABLE.value
            ),
            "authoritative_facilities": (
                "bc_recreation_sites"
                if bc_site_status == MeasurementStatus.OBSERVED.value
                else None
            ),
            "authoritative_access_boundaries": None,
            "shoreline_denominator": (
                "bc_shorezone_lines"
                if bc_shore_status == MeasurementStatus.OBSERVED.value
                else None
            ),
            "reason": (
                "BC recreation sites and ShoreZone provide authoritative facility and "
                "shoreline evidence, but not a complete public-access boundary inventory."
                if bc_authoritative_available
                else "Required BC recreation-site or ShoreZone evidence was unavailable."
            ),
        },
        "supplemental": {
            "source": "osm_shore_access",
            "coverage": "community_mapped_partial",
            "authoritative": False,
        },
        "sources": source_availability,
    }
    atomic_write_json(
        cfg.source_availability_path,
        availability,
        overwrite=cfg.source_availability_path.exists(),
    )
    artifacts.append(
        artifact_record(
            cfg.source_availability_path,
            dataset_id="human.accessibility.public_shore_access.raw.source_availability",
        )
    )
    payload = manifest_payload(
        config=cfg.human,
        stage="download",
        artifacts=artifacts,
        sources=sources,
        source_completeness=cfg.source_completeness,
        measurement_statuses=sorted(status.value for status in statuses),
        attribution=[source.attribution for source in cfg.sources.values()],
        licenses=[source.license_name for source in cfg.sources.values()],
        spatial_bounds={
            "west": cfg.bbox[0],
            "south": cfg.bbox[1],
            "east": cfg.bbox[2],
            "north": cfg.bbox[3],
        },
        limitations=[
            "BC recreation sites and ShoreZone do not constitute a complete public "
            "shoreline-access boundary inventory.",
            "OSM is supplemental community-mapped facility evidence and is not used "
            "as waterfront linework.",
            "Unmapped access remains unknown rather than zero.",
            *(
                ["OSM acquisition was unavailable; the empty snapshot is not an observed zero."]
                if source_availability["osm_shore_access"]["status"] == "unavailable"
                else []
            ),
        ],
    )
    write_manifest(cfg.raw_manifest_path, payload)
    return cfg.raw_manifest_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(download(args.config, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
