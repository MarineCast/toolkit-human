"""Acquire immutable WA, BC, and supplemental OSM boat-launch snapshots."""

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

from .config import DEFAULT_CONFIG_PATH, AccessSource, load_boat_launch_config

OSM_SELECTORS = ('["leisure"="slipway"]', '["man_made"="boat_ramp"]')


def _acquire(
    source: AccessSource,
    bbox: tuple[float, float, float, float],
    *,
    overpass_tile_span_degrees: float = 2.5,
    overpass_query_timeout_seconds: int = 60,
    overpass_request_timeout_seconds: int = 75,
) -> gpd.GeoDataFrame:
    if source.source_type == "arcgis":
        return query_arcgis_geojson(source.url, bbox=bbox)
    return query_overpass_points_batched(
        (source.url, *source.fallback_urls),
        bbox,
        OSM_SELECTORS,
        maximum_span_degrees=overpass_tile_span_degrees,
        query_timeout_seconds=overpass_query_timeout_seconds,
        request_timeout_seconds=overpass_request_timeout_seconds,
    )


def download(config_path: str | Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    cfg = load_boat_launch_config(config_path)
    artifacts = []
    sources = []
    statuses: set[MeasurementStatus] = set()
    availability: dict[str, dict[str, object]] = {}
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
        availability[source.name] = {
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
                dataset_id=f"human.accessibility.boat_launch_access.raw.{source.name}",
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
    atomic_write_json(
        cfg.source_availability_path,
        {"sources": availability},
        overwrite=cfg.source_availability_path.exists(),
    )
    artifacts.append(
        artifact_record(
            cfg.source_availability_path,
            dataset_id="human.accessibility.boat_launch_access.raw.source_availability",
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
            "The BC inventory is a legacy circa-2004 inventory and is not maintained.",
            "OSM is supplemental community-mapped evidence, not an authoritative "
            "completeness claim.",
            "Ramp capability, seasonal operation, and public status are incomplete across sources.",
            *(
                ["OSM acquisition was unavailable; the empty snapshot is not an observed zero."]
                if MeasurementStatus.UNAVAILABLE in statuses
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
