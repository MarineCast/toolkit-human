"""Checksum-bind locally available water observation-opportunity inputs."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_json,
    manifest_payload,
    sha256_file,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, load_water_observation_config


def download(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    overwrite: bool = False,
) -> Path:
    cfg = load_water_observation_config(config_path, resolve_generation=False)
    files = {
        "ais_manifest": cfg.ais_manifest_path,
        "ferry_manifest": cfg.ferry_manifest_path,
        "surface_weather_manifest": cfg.surface_weather_manifest_path,
        "daylight_manifest": cfg.daylight_manifest_path,
        "water_static_weights": cfg.water_static_weights_path,
        "viewshed_config": cfg.viewshed_config_path,
        "sightings_release_pointer": cfg.sightings_release_pointer,
        "land_generation_manifest": cfg.land_manifest_path,
        "public_shore_access": cfg.public_shore_path,
    }
    from human.viewshed.config import load_app_config

    app = load_app_config(cfg.viewshed_config_path)
    for name in ("regional_dem_path", "water_polygon_path", "land_polygon_path"):
        path = getattr(app.paths, name)
        members = sorted(path.parent.glob(f"{path.stem}.*")) if path.suffix == ".shp" else [path]
        for index, member in enumerate(members):
            files[f"canonical_water_{name}_{index}"] = member
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing local water observation inputs: {missing}")
    inputs = [
        {
            "name": name,
            "path": str(path.resolve()),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for name, path in files.items()
    ]
    inventory = {
        "schema_version": "2.0.0-research",
        "config_hash": cfg.human.config_hash,
        "local_sources_only": True,
        "inputs": inputs,
    }
    atomic_write_json(cfg.raw_inventory_path, inventory, overwrite=overwrite)
    payload = manifest_payload(
        config=cfg.human,
        stage="download",
        artifacts=[
            artifact_record(
                cfg.raw_inventory_path,
                dataset_id="human.water_observation_opportunity.input_inventory",
            )
        ],
        inputs=inputs,
        source_completeness="partial",
        measurement_statuses=[MeasurementStatus.OBSERVED, MeasurementStatus.DERIVED],
        attribution=["See checksum-bound upstream manifests"],
        licenses=["See checksum-bound upstream manifests"],
        limitations=[
            "This stage snapshots identities of local sources; it performs no network acquisition.",
            "AIS, ferry, public-access, whale-watch, and sea-state coverage remain incomplete.",
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
