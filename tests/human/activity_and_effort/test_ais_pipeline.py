from __future__ import annotations

import json

import h3
import pandas as pd
import pytest
import yaml

from human.activity_and_effort.ais.build import build
from human.activity_and_effort.ais.download import download
from human.activity_and_effort.ais.inspect import inspect


def test_ais_download_build_inspect_fixture_preserves_unknowns(tmp_path) -> None:
    cell = h3.latlng_to_cell(48.5, -123.0, 6)
    supplied = tmp_path / "supplied.parquet"
    pd.DataFrame(
        {
            "MMSI": ["1", "1", "2"],
            "DATE": ["2026-01-01"] * 3,
            "HOUR_BIN": [1, 2, 1],
            "H3_CELL": [cell] * 3,
            "MEAN_SOG": [10.0, 12.0, 102.0],
            "N_PINGS": [2, 2, 1],
            "VesselType": [70.0, 70.0, None],
        }
    ).to_parquet(supplied, index=False)
    config = tmp_path / "ais.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "product": "ais",
                "category": "activity_and_effort",
                "source": {
                    "provider": "fixture",
                    "license": "fixture",
                    "attribution": "fixture",
                    "source_completeness": "complete",
                    "measurement_status": "observed",
                    "supplied_paths": [str(supplied)],
                },
                "parameters": {"h3_resolution": 6, "output_grains": ["daily", "weekly"]},
                "raw": {
                    "snapshot_dir": str(tmp_path / "raw/yearly"),
                    "manifest_path": str(tmp_path / "raw/manifest.json"),
                },
                "output": {
                    "daily_path": str(tmp_path / "processed/daily.parquet"),
                    "weekly_path": str(tmp_path / "processed/weekly.parquet"),
                    "manifest_path": str(tmp_path / "processed/manifest.json"),
                },
                "inspection": {"report_path": str(tmp_path / "outputs/ais.html")},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    download(config)
    daily_path = build(config)
    reports = inspect(config)
    daily = pd.read_parquet(daily_path)
    assert daily.loc[0, "UNIQUE_VESSELS"] == 2
    assert daily.loc[0, "ACTIVE_VESSEL_HOURS_PROXY"] == 3
    assert daily.loc[0, "UNIQUE_UNKNOWN_VESSELS"] == 1
    assert daily.loc[0, "INVALID_SOG_PING_COUNT"] == 1
    assert daily.loc[0, "MEAN_SOG"] == pytest.approx(11.0)
    assert daily.loc[0, "OBSERVER_CAPABLE_VESSEL_HOURS_PROXY"] == 0
    assert daily.loc[0, "SOURCE_COVERAGE_COMPLETE"]
    assert reports[0].is_file()


def test_ais_reference_existing_partial_source_is_not_copied_or_treated_as_complete(
    tmp_path,
) -> None:
    cell = h3.latlng_to_cell(48.5, -123.0, 6)
    supplied = tmp_path / "legacy.parquet"
    pd.DataFrame(
        {
            "MMSI": ["1", "2"],
            "DATE": ["2024-01-01", "2024-01-01"],
            "HOUR_BIN": pd.to_datetime(["2024-01-01 01:00", "2024-01-01 02:00"]),
            "H3_CELL": [cell, cell],
            "MEAN_SOG": [8.0, 102.0],
            "N_PINGS": [3, 2],
            "VesselType": [60.0, 36.0],
        }
    ).to_parquet(supplied, index=False)
    config = tmp_path / "ais.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "product": "ais",
                "category": "activity_and_effort",
                "source": {
                    "provider": "legacy fixture",
                    "license": "unresolved",
                    "attribution": "unresolved",
                    "source_completeness": "partial",
                    "measurement_status": "observed",
                    "materialization_mode": "reference_existing",
                    "supplied_paths": [str(supplied)],
                },
                "parameters": {
                    "h3_resolution": 6,
                    "output_grains": ["daily", "weekly"],
                    "sog_not_available_min_knots": 102.0,
                    "observer_capable_classes": ["recreational", "passenger"],
                },
                "raw": {
                    "snapshot_dir": str(tmp_path / "raw/yearly"),
                    "manifest_path": str(tmp_path / "raw/manifest.json"),
                },
                "output": {
                    "daily_path": str(tmp_path / "processed/daily.parquet"),
                    "weekly_path": str(tmp_path / "processed/weekly.parquet"),
                    "qc_path": str(tmp_path / "processed/qc.json"),
                    "manifest_path": str(tmp_path / "processed/manifest.json"),
                },
                "inspection": {"report_path": str(tmp_path / "outputs/ais.html")},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    raw_manifest = download(config)
    payload = json.loads(raw_manifest.read_text(encoding="utf-8"))
    assert payload["artifacts"][0]["path"] == str(supplied.resolve())
    assert not (tmp_path / "raw/yearly/legacy.parquet").exists()
    with pytest.raises(ValueError, match="partial"):
        build(config)

    daily_path = build(config, allow_partial=True)
    daily = pd.read_parquet(daily_path)
    assert not daily.loc[0, "SOURCE_COVERAGE_COMPLETE"]
    assert daily.loc[0, "SOURCE_COVERAGE_STATUS"] == "partial_unknown_acquisition_coverage"
    assert daily.loc[0, "OBSERVER_CAPABLE_VESSEL_HOURS_PROXY"] == 2
    assert daily.loc[0, "INVALID_SOG_PING_COUNT"] == 2
