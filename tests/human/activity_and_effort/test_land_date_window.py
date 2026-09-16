from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from human.activity_and_effort.land_reporting_opportunity import inputs
from human.activity_and_effort.land_reporting_opportunity.config import (
    load_land_reporting_config,
)
from human.activity_and_effort.land_reporting_opportunity.dynamic import (
    _load_dynamic_inputs,
)


def test_requested_window_retains_missing_weather_days(monkeypatch):
    cfg = replace(
        load_land_reporting_config(), start_date=date(2020, 1, 1), end_date=date(2020, 1, 3)
    )
    monkeypatch.setattr(inputs, "read_pinned_json", lambda *a: {"source_completeness": "complete"})

    def read(cfg, key, *args, **kwargs):
        if key == "calendar":
            return pd.DataFrame({"date": ["2020-01-01"], "calendar_effort_weight": [1.0]})
        return pd.DataFrame({"DATE": ["2020-01-01"], "H3_INDEX": ["cell"]})

    monkeypatch.setattr(inputs, "read_pinned_parquet", read)
    weather, daylight, calendar, dates = _load_dynamic_inputs(cfg)
    assert list(dates) == list(pd.date_range("2020-01-01", "2020-01-03"))
    assert len(weather) == len(daylight) == len(calendar) == 1


def test_canonical_window_and_invalid_bounds(tmp_path):
    from pathlib import Path

    import yaml

    cfg = load_land_reporting_config()
    assert cfg.start_date == date(2020, 1, 1)
    assert cfg.end_date == date(2026, 8, 31)
    raw = yaml.safe_load(
        Path("config/data/human/activity_and_effort/land_reporting_opportunity.yaml").read_text()
    )
    raw["parameters"]["end_date"] = "2019-01-01"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="must not follow"):
        load_land_reporting_config(path)
    raw["parameters"]["end_date"] = None
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="both be supplied"):
        load_land_reporting_config(path)


def test_incompatible_daily_calendars_rejected(monkeypatch):
    def read(cfg, name):
        return {
            "source_completeness": "complete",
            "resolved_config": {
                "timezone": "UTC" if name == "surface_weather_manifest" else "America/Los_Angeles"
            },
        }

    monkeypatch.setattr(inputs, "read_pinned_json", read)
    with pytest.raises(ValueError, match="same daily calendar timezone"):
        _load_dynamic_inputs(load_land_reporting_config())
