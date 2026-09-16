"""Materialize configured ferry ridership, route, and vessel-history snapshots."""

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

from .config import DEFAULT_CONFIG_PATH, load_ferry_config


def download(config_path: str | Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    cfg = load_ferry_config(config_path, resolve_generation=False)
    artifacts = []
    sources = []
    for name, source in cfg.sources.items():
        suffix = "route_mapping.yaml" if name == "route_mapping" else source.supplied_path.name
        snapshot = materialize_source(
            source.supplied_path, cfg.snapshot_dir / suffix, overwrite=overwrite
        )
        status = MeasurementStatus(source.measurement_status)
        artifacts.append(artifact_record(snapshot, dataset_id=f"human.ferry.raw.{name}"))
        sources.append(
            source_record(
                snapshot,
                name=name,
                provider=source.provider,
                license_name=source.license_name,
                attribution=source.attribution,
                measurement_status=status,
            )
        )
    payload = manifest_payload(
        config=cfg.human,
        stage="download",
        artifacts=artifacts,
        sources=sources,
        source_completeness=cfg.source_completeness,
        measurement_statuses=[source.measurement_status for source in cfg.sources.values()],
        attribution=[source.attribution for source in cfg.sources.values()],
        licenses=[source.license_name for source in cfg.sources.values()],
        h3_resolution=cfg.native_h3_resolution,
        limitations=[
            "BC ridership is temporally allocated from monthly published totals.",
            "The configured WSF vessel-history snapshot has limited date coverage.",
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
