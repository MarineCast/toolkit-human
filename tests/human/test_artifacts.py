from __future__ import annotations

import json

import pandas as pd
import pytest
import yaml

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_parquet,
    load_manifest,
    manifest_payload,
    write_manifest,
)
from human.utils.config import HumanConfig
from human.utils.publication import publish_file


def _config(tmp_path) -> HumanConfig:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "product": "fixture",
                "category": "activity_and_effort",
                "raw": {},
                "output": {},
                "inspection": {},
            }
        ),
        encoding="utf-8",
    )
    return HumanConfig.load(path)


def test_manifest_verifies_artifact_checksum_and_schema(tmp_path) -> None:
    config = _config(tmp_path)
    artifact_path = atomic_write_parquet(
        pd.DataFrame({"DATE": ["2026-01-01"], "VALUE": [1.0]}),
        tmp_path / "artifact.parquet",
    )
    artifact = artifact_record(
        artifact_path,
        dataset_id="human.fixture.daily",
        frame=pd.read_parquet(artifact_path),
    )
    payload = manifest_payload(
        config=config,
        stage="build",
        artifacts=[artifact],
        source_completeness="complete",
        measurement_statuses=[MeasurementStatus.DERIVED],
        attribution=["fixture"],
        licenses=["fixture"],
    )
    manifest = write_manifest(tmp_path / "manifest.json", payload)
    assert load_manifest(manifest)["artifacts"][0]["rows"] == 1
    artifact_path.write_bytes(artifact_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_manifest(manifest)


def test_manifest_rejects_unknown_measurement_status(tmp_path) -> None:
    config = _config(tmp_path)
    path = atomic_write_parquet(pd.DataFrame({"x": [1]}), tmp_path / "x.parquet")
    with pytest.raises(ValueError, match="Unknown human measurement statuses"):
        manifest_payload(
            config=config,
            stage="build",
            artifacts=[artifact_record(path, dataset_id="human.fixture")],
            source_completeness="complete",
            measurement_statuses=["mixed"],
            attribution=[],
            licenses=[],
        )


def test_manifest_contains_required_provenance_fields(tmp_path) -> None:
    config = _config(tmp_path)
    path = atomic_write_parquet(pd.DataFrame({"x": [1]}), tmp_path / "x.parquet")
    payload = manifest_payload(
        config=config,
        stage="build",
        artifacts=[artifact_record(path, dataset_id="human.fixture")],
        source_completeness="partial",
        measurement_statuses=["unavailable"],
        attribution=["unknown"],
        licenses=["unresolved"],
    )
    assert {
        "config_hash",
        "sources",
        "inputs",
        "artifacts",
        "spatial_bounds_wgs84",
        "temporal_coverage",
        "source_completeness",
        "measurement_statuses",
        "attribution",
        "licenses",
        "known_limitations",
    }.issubset(json.loads(json.dumps(payload)))


def test_publication_is_atomic_and_checksum_verified(tmp_path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"value": 1}\n', encoding="utf-8")
    destination = tmp_path / "published" / "artifact.json"

    with pytest.raises(ValueError, match="checksum mismatch"):
        publish_file(source, destination, expected_sha256="not-the-source-checksum")
    assert not destination.exists()
    assert publish_file(source, destination) == destination
    assert destination.read_bytes() == source.read_bytes()
    assert not list(destination.parent.glob("*.part"))
