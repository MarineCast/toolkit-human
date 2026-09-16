from __future__ import annotations

import yaml

from human.temporal_context.calendar.build import build
from human.temporal_context.calendar.download import download
from human.temporal_context.calendar.inspect import inspect
from human.utils.artifacts import load_manifest


def test_calendar_download_build_inspect_fixture(tmp_path) -> None:
    config = tmp_path / "calendar.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "product": "calendar",
                "category": "temporal_context",
                "time": {"start_date": "2024-07-01", "end_date": "2024-07-07", "timezone": "UTC"},
                "jurisdictions": [
                    {"country": "US", "subdivision": "WA"},
                    {"country": "CA", "subdivision": "BC"},
                ],
                "features": {"peak_months": [7], "shoulder_months": [], "summer_months": [7]},
                "raw": {
                    "holiday_snapshot": str(tmp_path / "raw/holidays.parquet"),
                    "manifest_path": str(tmp_path / "raw/manifest.json"),
                },
                "output": {
                    "daily_path": str(tmp_path / "processed/calendar.parquet"),
                    "manifest_path": str(tmp_path / "processed/manifest.json"),
                },
                "inspection": {"report_path": str(tmp_path / "outputs/calendar.html")},
                "sources": {
                    "provider": "python-holidays",
                    "license": "MIT",
                    "attribution": "fixture",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    download(config)
    product = build(config)
    reports = inspect(config)
    assert len(__import__("pandas").read_parquet(product)) == 7
    assert reports[0].is_file()
    assert load_manifest(tmp_path / "processed/manifest.json")["source_completeness"] == "complete"
