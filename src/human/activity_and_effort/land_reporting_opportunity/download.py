"""Resolve and checksum the processed inputs for land reporting opportunity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_json,
    load_manifest,
    manifest_payload,
    sha256_file,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, load_land_reporting_config


def download(config_path: str | Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    cfg = load_land_reporting_config(config_path)
    manifests = {
        "land_transport_access": load_manifest(cfg.transport_manifest_path),
        "population_travel_time": load_manifest(cfg.population_travel_manifest_path),
        "public_shore_access": load_manifest(cfg.public_shore_manifest_path),
        "calendar": load_manifest(cfg.calendar_manifest_path),
    }
    dynamic_manifests = {
        "surface_weather": json.loads(
            cfg.surface_weather_manifest_path.read_text(encoding="utf-8")
        ),
        "daylight": json.loads(cfg.daylight_manifest_path.read_text(encoding="utf-8")),
    }
    paths = {
        "land_static_weights": cfg.static_weights_path,
        "land_static_metadata": cfg.static_weights_path.with_name(
            f"{cfg.static_weights_path.stem}_metadata.json"
        ),
        "transport_manifest": cfg.transport_manifest_path,
        "population_travel_manifest": cfg.population_travel_manifest_path,
        "public_shore_manifest": cfg.public_shore_manifest_path,
        "land_transport_access": cfg.transport_path,
        "population_travel_time": cfg.population_travel_path,
        "public_shore_access": cfg.public_shore_path,
        "calendar": cfg.calendar_path,
        "calendar_manifest": cfg.calendar_manifest_path,
        "surface_weather_manifest": cfg.surface_weather_manifest_path,
        "daylight_manifest": cfg.daylight_manifest_path,
    }
    from human.viewshed.config import load_app_config

    app = load_app_config(cfg.viewshed_config_path)
    paths["viewshed_config"] = cfg.viewshed_config_path
    for name in (
        "regional_dem_path",
        "canopy_height_path",
        "water_polygon_path",
        "land_polygon_path",
        "land_h3_path",
    ):
        path = getattr(app.paths, name)
        members = sorted(path.parent.glob(f"{path.stem}.*")) if path.suffix == ".shp" else [path]
        for index, member in enumerate(members):
            paths[f"viewshed_{name}_{index}"] = member
    for item in manifests["public_shore_access"]["artifacts"]:
        if item["dataset_id"].endswith(".facilities_r7"):
            paths["public_access_facilities"] = Path(item["path"])
    for name, directory in (("weather", cfg.surface_weather_path), ("daylight", cfg.daylight_path)):
        for index, partition in enumerate(sorted(directory.rglob("*.parquet"))):
            paths[f"{name}_partition_{index}"] = partition
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Land-reporting inputs are missing: {missing}")
    missing_datasets = [
        str(path) for path in (cfg.surface_weather_path, cfg.daylight_path) if not path.is_dir()
    ]
    if missing_datasets:
        raise FileNotFoundError(f"Land-reporting dynamic datasets are missing: {missing_datasets}")
    inventory = {
        "status": "resolved_checksum_pinned_inputs",
        "inputs": {
            name: {
                "path": str(path),
                "sha256": sha256_file(path),
                "manifest_config_hash": manifests.get(name, {}).get("config_hash"),
                "source_completeness": manifests.get(name, {}).get("source_completeness"),
                "resolved_config_hash": dynamic_manifests.get(name, {}).get("resolved_config_hash"),
            }
            for name, path in paths.items()
        },
    }
    if cfg.input_inventory_path.exists() and not overwrite:
        raise FileExistsError(f"Land-reporting input inventory exists: {cfg.input_inventory_path}")
    atomic_write_json(cfg.input_inventory_path, inventory, overwrite=overwrite)
    artifact = artifact_record(
        cfg.input_inventory_path,
        dataset_id="human.activity_and_effort.land_reporting_opportunity.raw.input_inventory",
    )
    payload = manifest_payload(
        config=cfg.human,
        stage="download",
        artifacts=[artifact],
        inputs=[artifact_record(path, dataset_id=f"input.{name}") for name, path in paths.items()],
        source_completeness=cfg.source_completeness,
        measurement_statuses=[MeasurementStatus.DERIVED, MeasurementStatus.UNAVAILABLE],
        attribution=[
            attribution
            for manifest in manifests.values()
            for attribution in manifest["attribution"]
        ],
        licenses=[
            license_name for manifest in manifests.values() for license_name in manifest["licenses"]
        ],
        h3_resolution=7,
        limitations=[
            "This stage resolves existing processed products; source acquisition remains owned by each input family.",
            "Transport, population travel, and public-shore inputs are research-only partial evidence.",
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
