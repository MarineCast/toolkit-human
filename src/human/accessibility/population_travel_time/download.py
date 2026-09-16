"""Adopt the checksum-pinned population travel-demand prototype."""

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

from .config import DEFAULT_CONFIG_PATH, load_population_travel_config


def _validate_source(
    frame: pd.DataFrame,
    metadata: dict[str, object],
    *,
    decay_minutes: tuple[int, ...],
) -> None:
    required = {
        "source_h3",
        "POPULATION_TRAVEL_CONTEXT_AVAILABLE",
        "POPULATION_TRAVEL_ROUTED_SELECTED_POPULATION_FRACTION",
        *(f"POPULATION_TRAVEL_DEMAND_{minutes}_MIN" for minutes in decay_minutes),
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Population-travel prototype is missing columns: {missing}")
    if frame["source_h3"].isna().any() or frame["source_h3"].duplicated().any():
        raise ValueError("Population-travel source_h3 values must be non-null and unique.")
    if int(metadata.get("h3_resolution", -1)) != 7:
        raise ValueError("Population-travel prototype metadata must declare H3 resolution 7.")
    if int(metadata.get("rows", -1)) != len(frame):
        raise ValueError("Population-travel prototype metadata row count does not match.")


def download(config_path: str | Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    cfg = load_population_travel_config(config_path)
    if not cfg.source_path.is_file() or not cfg.source_metadata_path.is_file():
        raise FileNotFoundError("Configured population-travel prototype and metadata are required.")
    reuse = cfg.snapshot_path.is_file() and cfg.snapshot_metadata_path.is_file() and not overwrite
    if (
        not reuse
        and not overwrite
        and (cfg.snapshot_path.exists() or cfg.snapshot_metadata_path.exists())
    ):
        raise FileExistsError("Population-travel raw snapshot is incomplete; rerun with overwrite.")
    selected_frame_path = cfg.snapshot_path if reuse else cfg.source_path
    selected_metadata_path = cfg.snapshot_metadata_path if reuse else cfg.source_metadata_path
    frame = pd.read_parquet(selected_frame_path)
    metadata = json.loads(selected_metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("Population-travel prototype metadata must be a JSON object.")
    _validate_source(frame, metadata, decay_minutes=cfg.decay_minutes)
    declared_source = metadata.get("source_artifact")
    if not reuse and isinstance(declared_source, dict) and declared_source.get("sha256"):
        if str(declared_source["sha256"]) != sha256_file(cfg.source_path):
            raise ValueError("Population-travel prototype checksum does not match its metadata.")
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
            dataset_id="human.accessibility.population_travel_time.raw.demand_h3_r7",
            frame=frame,
            h3_resolution=7,
        ),
        artifact_record(
            cfg.snapshot_metadata_path,
            dataset_id="human.accessibility.population_travel_time.raw.routing_metadata",
        ),
    ]
    sources = [
        source_record(
            cfg.snapshot_path,
            name="population_travel_routing_prototype",
            provider=cfg.source_provider,
            license_name=cfg.source_license,
            attribution=cfg.source_attribution,
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
            "This stage adopts a checksum-pinned notebook routing snapshot and does not rerun routes.",
            "The public OSRM graph version is unknown and ferry semantics are unvalidated.",
            "Population origins are regional H3 R4 clusters rather than household trips.",
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
