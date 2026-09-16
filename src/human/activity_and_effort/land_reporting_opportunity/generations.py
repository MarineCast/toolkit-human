"""Immutable land generations with a single atomic manifest publication point."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from human.utils.artifacts import (
    atomic_write_json,
    load_manifest,
    sha256_file,
    validate_manifest,
)

from .config import LandReportingConfig

ACCESS_ARTIFACT_ROLES = {
    "observation_sites",
    "observer_samples",
    "access_kernel",
    "target_water_support",
    "inspection_html",
    "inspection_png",
}

OUTPUT_FIELDS = {
    "source_h3_r7": "source_output_path",
    "target_h3_r7": "target_output_path",
    "daily_h3_r6": "daily_output_path",
    "weekly_h3_r6": "weekly_output_path",
    "dynamic_metadata": "dynamic_metadata_path",
}


def new_generation(cfg: LandReportingConfig) -> LandReportingConfig:
    identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    directory = cfg.manifest_path.parent / "generations" / identifier
    directory.mkdir(parents=True, exist_ok=False)
    return replace(
        cfg, **{field: directory / getattr(cfg, field).name for field in OUTPUT_FIELDS.values()}
    )


def archive_legacy(manifest_path: Path) -> Path | None:
    """Preserve existing flat outputs before the first generation publication."""
    if not manifest_path.exists():
        return None
    payload = load_manifest(manifest_path)
    if payload.get("generation_id"):
        return Path(payload["artifacts"][0]["path"]).parent / "manifest.json"
    directory = manifest_path.parent / "generations" / ("legacy_" + sha256_file(manifest_path)[:16])
    if (directory / "manifest.json").exists():
        return directory / "manifest.json"
    directory.mkdir(parents=True, exist_ok=True)
    for artifact in payload["artifacts"]:
        source = Path(artifact["path"])
        destination = directory / source.name
        shutil.copy2(source, destination)
        artifact["path"] = str(destination.resolve())
    payload["generation_id"] = directory.name
    payload["routing_policy"] = "legacy_routing_unverified"
    validate_manifest(payload)
    return atomic_write_json(directory / "manifest.json", payload)


def publish(cfg: LandReportingConfig, payload: dict) -> None:
    validate_manifest(payload)
    from .schema import validate_land_schema

    validate_land_schema(payload, structural_only=True)
    roles = [item["dataset_id"].split(".")[-1] for item in payload["artifacts"]]
    if not set(OUTPUT_FIELDS).issubset(roles) or len(roles) != len(set(roles)):
        raise ValueError("Incomplete or duplicate land generation roles")
    parents = {Path(item["path"]).parent for item in payload["artifacts"]}
    if parents != {cfg.source_output_path.parent}:
        raise ValueError("Mixed land artifact generations cannot be published")
    if payload.get("land_product_schema_version") in {
        "4.0.0-research",
        "4.1.0-research",
    } and not ACCESS_ARTIFACT_ROLES.issubset(roles):
        raise ValueError(
            "Access-conditioned generation is missing required support or inspection artifacts"
        )
    payload["generation_id"] = cfg.source_output_path.parent.name
    atomic_write_json(cfg.source_output_path.parent / "manifest.json", payload)
    atomic_write_json(cfg.manifest_path, payload, overwrite=True)


def resolve_outputs(cfg: LandReportingConfig) -> LandReportingConfig:
    if not cfg.manifest_path.exists():
        return cfg
    payload = json.loads(cfg.manifest_path.read_text())
    if not payload.get("generation_id"):
        return cfg
    outputs = {
        item["dataset_id"].split(".")[-1]: Path(item["path"]) for item in payload["artifacts"]
    }
    if not set(OUTPUT_FIELDS).issubset(outputs):
        raise ValueError("Incomplete land generation")
    if len({outputs[key].parent for key in OUTPUT_FIELDS}) != 1:
        raise ValueError("Mixed land generation")
    return replace(cfg, **{field: outputs[key] for key, field in OUTPUT_FIELDS.items()})


def switch_generation(config: str | Path, generation: str, *, activate: bool = False) -> dict:
    """Validate a current-compatible generation before an optional atomic switch."""
    from .config import load_land_reporting_config
    from .modeling import load_weekly

    if Path(generation).name != generation or generation in {"", ".", ".."}:
        raise ValueError("Generation must be a single directory name")
    cfg = load_land_reporting_config(config, resolve_generation=False)
    directory = cfg.manifest_path.parent / "generations" / generation
    _, payload = load_weekly(
        directory / cfg.weekly_output_path.name,
        expected_config_hash=cfg.human.config_hash,
        allow_access_conditioned=True,
    )
    roles = {item["dataset_id"].split(".")[-1] for item in payload["artifacts"]}
    if payload.get("generation_id") != generation or not set(OUTPUT_FIELDS).issubset(roles):
        raise ValueError("Invalid or incomplete land generation")
    if activate:
        atomic_write_json(cfg.manifest_path, payload, overwrite=True)
    return {
        "generation_id": generation,
        "valid": True,
        "activated": activate,
        "selector": str(cfg.manifest_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate or atomically reactivate a compatible land generation"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--activate", action="store_true", help="Without this flag, validate only")
    args = parser.parse_args()
    print(
        json.dumps(
            switch_generation(args.config, args.generation, activate=args.activate), indent=2
        )
    )


if __name__ == "__main__":
    main()
