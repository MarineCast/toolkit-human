"""Adopt the checksum-pinned land-transport routing prototype as a raw snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import pandas as pd

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_json,
    atomic_write_parquet,
    manifest_payload,
    sha256_file,
    source_record,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, load_land_transport_config


def _validate_source(frame: pd.DataFrame, metadata: dict[str, object]) -> None:
    required = {
        "source_h3",
        "ROAD_SNAP_DISTANCE_FROM_SOURCE_CENTROID_M",
        "MIN_CITY_TRAVEL_TIME_MIN",
        "ROAD_ROUTING_AVAILABLE",
        "CITY_TRAVEL_ROUTING_AVAILABLE",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Land-transport prototype is missing columns: {missing}")
    if frame["source_h3"].isna().any() or frame["source_h3"].duplicated().any():
        raise ValueError("Land-transport source_h3 values must be non-null and unique.")
    if int(metadata.get("h3_resolution", -1)) != 7:
        raise ValueError("Land-transport prototype metadata must declare H3 resolution 7.")
    if int(metadata.get("rows", -1)) != len(frame):
        raise ValueError("Land-transport prototype metadata row count does not match.")


def download(config_path: str | Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    cfg = load_land_transport_config(config_path)
    if not cfg.source_path.is_file() or not cfg.source_metadata_path.is_file():
        raise FileNotFoundError("Configured land-transport prototype and metadata are required.")
    reuse = cfg.snapshot_path.is_file() and cfg.snapshot_metadata_path.is_file() and not overwrite
    if (
        not reuse
        and not overwrite
        and (cfg.snapshot_path.exists() or cfg.snapshot_metadata_path.exists())
    ):
        raise FileExistsError("Land-transport raw snapshot is incomplete; rerun with overwrite.")
    selected_frame_path = cfg.snapshot_path if reuse else cfg.source_path
    selected_metadata_path = cfg.snapshot_metadata_path if reuse else cfg.source_metadata_path
    frame = pd.read_parquet(selected_frame_path)
    metadata = json.loads(selected_metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("Land-transport prototype metadata must be a JSON object.")
    _validate_source(frame, metadata)
    if not reuse:
        atomic_write_parquet(frame, cfg.snapshot_path, overwrite=overwrite)
        adopted_metadata = {
            **metadata,
            "adopted_source_path": str(cfg.source_path),
            "adopted_source_sha256": sha256_file(cfg.source_path),
            "adoption_status": "research_snapshot_not_reconstructed",
        }
        atomic_write_json(
            cfg.snapshot_metadata_path,
            adopted_metadata,
            overwrite=overwrite,
        )
    artifacts = [
        artifact_record(
            cfg.snapshot_path,
            dataset_id="human.accessibility.land_transport_access.raw.routing_h3_r7",
            frame=frame,
            h3_resolution=7,
        ),
        artifact_record(
            cfg.snapshot_metadata_path,
            dataset_id="human.accessibility.land_transport_access.raw.routing_metadata",
        ),
    ]
    sources = [
        source_record(
            cfg.snapshot_path,
            name="land_transport_routing_prototype",
            provider=cfg.source_provider,
            license_name=cfg.source_license,
            attribution=cfg.source_attribution,
            url=None,
            measurement_status=MeasurementStatus.DERIVED,
        )
    ]
    payload = manifest_payload(
        config=cfg.human,
        stage="download",
        artifacts=artifacts,
        sources=sources,
        source_completeness=cfg.source_completeness,
        measurement_statuses=[MeasurementStatus.DERIVED],
        attribution=[cfg.source_attribution],
        licenses=[cfg.source_license],
        h3_resolution=7,
        limitations=[
            "This stage adopts a checksum-pinned notebook routing snapshot; it does not "
            "reconstruct the OSRM graph or rerun routes.",
            "The upstream public OSRM service did not expose a reconstructable graph version.",
            "Routes terminate at source-cell centroids snapped to the driving graph.",
            "Ferry inclusion and schedules were not independently validated.",
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
