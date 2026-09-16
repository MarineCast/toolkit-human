import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from human.activity_and_effort.land_reporting_opportunity.config import (
    load_land_reporting_config,
)
from human.activity_and_effort.land_reporting_opportunity.generations import (
    OUTPUT_FIELDS,
    new_generation,
    publish,
    switch_generation,
)
from human.activity_and_effort.land_reporting_opportunity.modeling import (
    resolve_weekly,
    shadow_predictions,
)
from human.utils.artifacts import artifact_record, manifest_payload


def fixture(tmp_path):
    cfg = replace(
        load_land_reporting_config(resolve_generation=False),
        manifest_path=tmp_path / "manifest.json",
    )
    cfg = new_generation(cfg)
    artifacts = []
    for role, field in OUTPUT_FIELDS.items():
        path = getattr(cfg, field)
        if path.suffix == ".json":
            path.write_text("{}")
        else:
            pd.DataFrame({"x": [1]}).to_parquet(path)
        artifacts.append(artifact_record(path, dataset_id=f"test.{role}"))
    payload = manifest_payload(
        config=cfg.human,
        stage="build",
        artifacts=artifacts,
        source_completeness="partial",
        measurement_statuses=["derived"],
        attribution=[],
        licenses=[],
    )
    return cfg, payload


def test_failed_publication_keeps_previous_manifest(tmp_path: Path):
    cfg, payload = fixture(tmp_path)
    cfg.manifest_path.write_text('{"previous": true}')
    payload["artifacts"][0]["sha256"] = "incorrect"
    with pytest.raises(ValueError, match="checksum"):
        publish(cfg, payload)
    assert json.loads(cfg.manifest_path.read_text()) == {"previous": True}


def test_mixed_generation_rejected(tmp_path: Path):
    cfg, payload = fixture(tmp_path)
    other = tmp_path / "other.parquet"
    pd.DataFrame({"x": [2]}).to_parquet(other)
    payload["artifacts"].append(artifact_record(other, dataset_id="test.other"))
    with pytest.raises(ValueError, match="Mixed"):
        publish(cfg, payload)


def test_manifest_resolves_one_generation_and_checks_upstream(tmp_path: Path):
    cfg, payload = fixture(tmp_path)
    upstream = tmp_path / "input.parquet"
    pd.DataFrame({"x": [1]}).to_parquet(upstream)
    payload["inputs"] = [artifact_record(upstream, dataset_id="input")]
    publish(cfg, payload)
    assert resolve_weekly(tmp_path / "weekly.parquet")[0] == cfg.weekly_output_path
    pd.DataFrame({"x": [2]}).to_parquet(upstream)
    with pytest.raises(ValueError, match="Stale"):
        resolve_weekly(tmp_path / "weekly.parquet")


def test_shadow_keeps_incumbent_and_exposes_missing_context():
    frame = pd.DataFrame({"previous_week_land_land_effort_proxy_raw": [2.0, None]})
    result = shadow_predictions(
        frame, formulation="composite", incumbent=[0.2, 0.3], candidate=[0.6, 0.7]
    )
    assert result.served_prediction.tolist() == [0.2, 0.3]
    assert result.candidate_with_fallback.tolist() == [0.6, 0.3]
    assert result.routing_status.tolist() == ["shadow_only", "incumbent_fallback"]


def test_generation_switch_rejects_path_traversal():
    with pytest.raises(ValueError, match="single directory"):
        switch_generation("unused.yaml", "../another_product", activate=True)
