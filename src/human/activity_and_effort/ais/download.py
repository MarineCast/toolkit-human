"""Materialize provenance-declared AIS Parquet source snapshots."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from human.utils.acquisition import materialize_source
from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    manifest_payload,
    source_record,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, load_ais_config


def download(config_path: str | Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    cfg = load_ais_config(config_path)
    artifacts = []
    sources = []
    for supplied in cfg.supplied_paths:
        if cfg.materialization_mode == "reference_existing":
            snapshot = supplied.resolve()
            if not snapshot.is_file():
                raise FileNotFoundError(f"Configured AIS archive does not exist: {snapshot}")
        else:
            snapshot = materialize_source(
                supplied, cfg.snapshot_dir / supplied.name, overwrite=overwrite
            )
        artifacts.append(
            artifact_record(
                snapshot,
                dataset_id=f"human.ais.raw.{snapshot.stem.lower()}",
                h3_resolution=cfg.h3_resolution,
            )
        )
        sources.append(
            source_record(
                snapshot,
                name=snapshot.stem,
                provider=cfg.provider,
                license_name=cfg.license_name,
                attribution=cfg.attribution,
                measurement_status=MeasurementStatus.OBSERVED,
            )
        )
    payload = manifest_payload(
        config=cfg.human,
        stage="download",
        artifacts=artifacts,
        sources=sources,
        source_completeness=cfg.source_completeness,
        measurement_statuses=[MeasurementStatus.OBSERVED],
        attribution=[cfg.attribution],
        licenses=[cfg.license_name],
        h3_resolution=cfg.h3_resolution,
        limitations=[
            "The legacy archive has unresolved original acquisition provenance.",
            (
                "reference_existing inputs are immutable legacy artifacts, not newly downloaded snapshots."
                if cfg.materialization_mode == "reference_existing"
                else "Configured inputs were copied into the immutable raw snapshot directory."
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
