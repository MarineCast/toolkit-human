"""Immutable ferry generations with manifest-last selector publication."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from human.utils.artifacts import atomic_write_json, validate_manifest

from .config import OUTPUT_FIELDS, FerryConfig


def new_generation(cfg: FerryConfig) -> FerryConfig:
    identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    directory = cfg.manifest_path.parent / "generations" / identifier
    directory.mkdir(parents=True, exist_ok=False)
    return replace(
        cfg,
        output_dir=directory,
        **{field: directory / getattr(cfg, field).name for field in OUTPUT_FIELDS.values()},
    )


def publish(cfg: FerryConfig, payload: dict[str, object]) -> None:
    validate_manifest(payload)
    roles = [item["dataset_id"].split(".")[-1] for item in payload["artifacts"]]
    if not set(OUTPUT_FIELDS).issubset(roles) or len(roles) != len(set(roles)):
        raise ValueError("Incomplete or duplicate ferry generation roles.")
    if {Path(item["path"]).parent for item in payload["artifacts"]} != {cfg.output_dir}:
        raise ValueError("Mixed ferry generations cannot be published.")
    payload["generation_id"] = cfg.output_dir.name
    atomic_write_json(cfg.output_dir / "manifest.json", payload)
    atomic_write_json(cfg.manifest_path, payload, overwrite=True)
