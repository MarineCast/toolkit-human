"""Read-only release-v1 input adapter; no species pipeline dependency."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from human.core.artifacts import checksum_path
RELEASE_SCHEMA_VERSION = "1"

@dataclass(frozen=True)
class SightingsReleaseArtifact:
    """One checksum-verified artifact resolved from an immutable release."""

    release_id: str
    manifest_path: Path
    dataset_id: str
    path: Path
    checksum: str
    schema_version: str
    producer: str
    row_count: int | None
    file_count: int
    snapshot_id: str | None
    processing_mode: str | None
    sensitivity: str
    config_hash: str
    end_date: str
    created_at_utc: str
    coverage_status: str
    coverage_through: str | None

def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def resolve_sightings_release_manifest(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_dir():
        candidate = candidate / "latest.json"
    if candidate.name == "latest.json":
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        pointer = json.loads(candidate.read_text(encoding="utf-8"))
        reference = Path(str(pointer.get("manifest", "")))
        if not str(reference):
            raise ValueError(f"Sightings release pointer has no manifest: {candidate}")
        if reference.is_absolute():
            raise ValueError("Sightings release pointers must use relative manifest paths")
        candidate = (candidate.parent / reference).resolve()
    return candidate

def _release_coverage(identity: Mapping[str, Any]) -> tuple[str, str | None]:
    gates = identity.get("gates", ())
    if not isinstance(gates, list):
        return "unverified", None
    coverage = next(
        (
            item
            for item in gates
            if isinstance(item, dict) and item.get("name") == "verified_target_cohort"
        ),
        None,
    )
    if coverage is None:
        return "unverified", None
    status = str(coverage.get("coverage_status") or "unverified")
    through = coverage.get("coverage_through")
    return status, str(through) if through else None

def resolve_sightings_release_artifact(
    path: str | Path,
    dataset_id: str,
    *,
    verify_checksum: bool = True,
) -> SightingsReleaseArtifact:
    """Resolve exactly one release artifact without consulting mutable stage pointers.

    The release identity, inventory membership, path containment, and selected
    artifact checksum are validated on every resolution.  This is intentionally
    narrower than :func:`validate_sightings_release`, which validates every
    artifact and can be expensive for multi-gigabyte source snapshots.
    """

    manifest_path = resolve_sightings_release_manifest(path)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing sightings release manifest: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if str(payload.get("schema_version")) != RELEASE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported sightings release schema: {payload.get('schema_version')}")
    identity = payload.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("Sightings release is missing its identity payload")
    release_id = str(payload.get("release_id") or "")
    if release_id != _canonical_hash(identity):
        raise ValueError("Sightings release id does not match its canonical identity")

    inventory = payload.get("inventory")
    if not isinstance(inventory, list):
        raise ValueError("Sightings release inventory is missing")
    matches = [
        item
        for item in inventory
        if isinstance(item, dict) and str(item.get("dataset_id")) == dataset_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Sightings release must inventory exactly one {dataset_id}; found {len(matches)}"
        )
    entry = matches[0]
    identity_matches = [
        item
        for item in identity.get("artifacts", ())
        if isinstance(item, dict) and str(item.get("dataset_id")) == dataset_id
    ]
    if len(identity_matches) != 1 or str(identity_matches[0].get("checksum")) != str(
        entry.get("checksum")
    ):
        raise ValueError(f"Release identity does not bind inventory artifact {dataset_id}")

    relative = Path(str(entry.get("path") or ""))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"Invalid release inventory path for {dataset_id}: {relative}")
    generation = manifest_path.parent.resolve()
    artifact_path = (generation / relative).resolve()
    try:
        artifact_path.relative_to(generation)
    except ValueError as exc:
        raise ValueError(f"Release inventory escapes its generation: {relative}") from exc
    if not artifact_path.exists():
        raise FileNotFoundError(f"Release artifact is missing: {artifact_path}")
    expected_checksum = str(entry.get("checksum") or "")
    if not expected_checksum:
        raise ValueError(f"Release artifact has no checksum: {dataset_id}")
    if verify_checksum and checksum_path(artifact_path) != expected_checksum:
        raise ValueError(f"Release artifact checksum mismatch: {dataset_id}")
    coverage_status, coverage_through = _release_coverage(identity)
    return SightingsReleaseArtifact(
        release_id=release_id,
        manifest_path=manifest_path,
        dataset_id=dataset_id,
        path=artifact_path,
        checksum=expected_checksum,
        schema_version=str(entry.get("schema_version") or "1"),
        producer=str(entry.get("producer") or "unknown"),
        row_count=(int(entry["row_count"]) if entry.get("row_count") is not None else None),
        file_count=int(entry.get("file_count") or 0),
        snapshot_id=(str(entry["snapshot_id"]) if entry.get("snapshot_id") else None),
        processing_mode=(str(entry["processing_mode"]) if entry.get("processing_mode") else None),
        sensitivity=str(entry.get("sensitivity") or "internal"),
        config_hash=str(identity.get("config_hash") or ""),
        end_date=str(identity.get("end_date") or ""),
        created_at_utc=str(payload.get("created_at_utc") or ""),
        coverage_status=coverage_status,
        coverage_through=coverage_through,
    )
