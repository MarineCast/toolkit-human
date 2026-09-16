"""Manifest and atomic-publication contracts for human data products."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from .config import HumanConfig

MANIFEST_SCHEMA_VERSION = 1


class MeasurementStatus(StrEnum):
    OBSERVED = "observed"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def atomic_write_json(path: str | Path, payload: Any, *, overwrite: bool = False) -> Path:
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.part")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def atomic_write_parquet(
    frame: pd.DataFrame,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.part")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, destination)
    return destination


def temporal_coverage(frame: pd.DataFrame) -> dict[str, str | None]:
    for column in ("DATE", "date", "SERVICE_DATE", "service_date", "WEEK_START"):
        if column not in frame:
            continue
        values = pd.to_datetime(frame[column], errors="coerce")
        if values.notna().any():
            return {
                "column": column,
                "minimum": values.min().date().isoformat(),
                "maximum": values.max().date().isoformat(),
            }
    return {"column": None, "minimum": None, "maximum": None}


def artifact_record(
    path: str | Path,
    *,
    dataset_id: str,
    frame: pd.DataFrame | None = None,
    h3_resolution: int | None = None,
) -> dict[str, Any]:
    artifact_path = Path(path).resolve()
    if not artifact_path.is_file():
        raise FileNotFoundError(f"Artifact does not exist: {artifact_path}")
    record: dict[str, Any] = {
        "dataset_id": dataset_id,
        "path": str(artifact_path),
        "sha256": sha256_file(artifact_path),
        "bytes": artifact_path.stat().st_size,
        "rows": None,
        "schema": [],
        "h3_resolution": h3_resolution,
        "temporal_coverage": {},
    }
    if frame is not None:
        record.update(
            rows=int(len(frame)),
            schema=[
                {"name": str(name), "dtype": str(dtype)} for name, dtype in frame.dtypes.items()
            ],
            temporal_coverage=temporal_coverage(frame),
        )
    elif artifact_path.suffix.lower() in {".parquet", ".geoparquet"}:
        import pyarrow.parquet as pq

        parquet = pq.ParquetFile(artifact_path)
        record.update(
            rows=int(parquet.metadata.num_rows),
            schema=[
                {"name": field.name, "dtype": str(field.type)} for field in parquet.schema_arrow
            ],
        )
    return record


def source_record(
    path: str | Path,
    *,
    name: str,
    provider: str,
    license_name: str,
    attribution: str,
    url: str | None = None,
    measurement_status: MeasurementStatus = MeasurementStatus.OBSERVED,
    temporal: Mapping[str, Any] | None = None,
    spatial: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_path = Path(path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source snapshot does not exist: {source_path}")
    return {
        "name": name,
        "provider": provider,
        "path": str(source_path),
        "url": url,
        "retrieved_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "sha256": sha256_file(source_path),
        "bytes": source_path.stat().st_size,
        "license": license_name,
        "attribution": attribution,
        "measurement_status": str(measurement_status),
        "temporal_coverage": dict(temporal or {}),
        "spatial_coverage": dict(spatial or {}),
    }


def manifest_payload(
    *,
    config: HumanConfig,
    stage: str,
    artifacts: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]] = (),
    inputs: Sequence[Mapping[str, Any]] = (),
    source_completeness: str,
    measurement_statuses: Sequence[MeasurementStatus | str],
    attribution: Sequence[str],
    licenses: Sequence[str],
    h3_resolution: int | None = None,
    spatial_bounds: Mapping[str, Any] | None = None,
    temporal: Mapping[str, Any] | None = None,
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    if not artifacts:
        raise ValueError("A human manifest requires at least one artifact.")
    statuses = sorted({str(value) for value in measurement_statuses})
    invalid = sorted(set(statuses).difference(status.value for status in MeasurementStatus))
    if invalid:
        raise ValueError(f"Unknown human measurement statuses: {invalid}")
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "product": f"human.{config.category}.{config.product}",
        "stage": stage,
        "run_id": config.config_hash[:16],
        "build_time_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "config_path": str(config.path),
        "config_hash": config.config_hash,
        "resolved_config": config.manifest_safe(),
        "sources": [dict(value) for value in sources],
        "inputs": [dict(value) for value in inputs],
        "artifacts": [dict(value) for value in artifacts],
        "h3_resolution": h3_resolution,
        "spatial_bounds_wgs84": dict(spatial_bounds or {}),
        "temporal_coverage": dict(temporal or {}),
        "source_completeness": source_completeness,
        "measurement_statuses": statuses,
        "attribution": sorted(set(attribution)),
        "licenses": sorted(set(licenses)),
        "known_limitations": list(limitations),
    }


def validate_manifest(payload: Mapping[str, Any], *, verify_artifacts: bool = True) -> None:
    required = {
        "manifest_schema_version",
        "product",
        "stage",
        "run_id",
        "build_time_utc",
        "config_path",
        "config_hash",
        "resolved_config",
        "sources",
        "inputs",
        "artifacts",
        "h3_resolution",
        "spatial_bounds_wgs84",
        "temporal_coverage",
        "source_completeness",
        "measurement_statuses",
        "attribution",
        "licenses",
        "known_limitations",
    }
    missing = sorted(required.difference(payload))
    if missing:
        raise ValueError(f"Human manifest is missing fields: {missing}")
    if int(payload["manifest_schema_version"]) != MANIFEST_SCHEMA_VERSION:
        raise ValueError("Unsupported human manifest schema version.")
    if not isinstance(payload["artifacts"], list) or not payload["artifacts"]:
        raise ValueError("Human manifest artifacts must be a non-empty list.")
    for status in payload["measurement_statuses"]:
        MeasurementStatus(str(status))
    if verify_artifacts:
        for artifact in payload["artifacts"]:
            path = Path(str(artifact["path"]))
            if not path.is_file():
                raise FileNotFoundError(f"Manifest artifact does not exist: {path}")
            if sha256_file(path) != artifact["sha256"]:
                raise ValueError(f"Manifest artifact checksum mismatch: {path}")


def write_manifest(path: str | Path, payload: Mapping[str, Any]) -> Path:
    validate_manifest(payload, verify_artifacts=True)
    return atomic_write_json(path, dict(payload), overwrite=True)


def load_manifest(path: str | Path, *, verify_artifacts: bool = True) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Human manifest root must be a mapping: {path}")
    validate_manifest(payload, verify_artifacts=verify_artifacts)
    return payload
