"""Immutable generation routing for water observation-opportunity artifacts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from human.activity_and_effort.observation_opportunity_contract import (
    SCHEMA_VERSION,
)
from human.utils.artifacts import atomic_write_json, validate_manifest

from .config import OUTPUT_FIELDS, WaterObservationConfig


def new_generation(cfg: WaterObservationConfig) -> WaterObservationConfig:
    identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    directory = cfg.manifest_path.parent / "generations" / identifier
    directory.mkdir(parents=True, exist_ok=False)
    return replace(
        cfg, **{field: directory / getattr(cfg, field).name for field in OUTPUT_FIELDS.values()}
    )


def publish(cfg: WaterObservationConfig, payload: dict) -> None:
    validate_manifest(payload)
    roles = [item["dataset_id"].split(".")[-1] for item in payload["artifacts"]]
    if not set(OUTPUT_FIELDS).issubset(roles) or len(roles) != len(set(roles)):
        raise ValueError("Incomplete or duplicate water observation generation roles.")
    parents = {Path(item["path"]).parent for item in payload["artifacts"]}
    if parents != {cfg.source_daily_path.parent}:
        raise ValueError("Mixed water observation generations cannot be published.")
    payload["generation_id"] = cfg.source_daily_path.parent.name
    payload["schema_version"] = SCHEMA_VERSION
    atomic_write_json(cfg.source_daily_path.parent / "manifest.json", payload)
    atomic_write_json(cfg.manifest_path, payload, overwrite=True)
