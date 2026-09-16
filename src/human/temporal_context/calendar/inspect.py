"""Inspect the daily human calendar product."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pandas as pd

from human.utils.artifacts import load_manifest
from human.utils.inspection import write_html_report

from .config import DEFAULT_CONFIG_PATH, load_calendar_config
from .validation import summarize_calendar_features, validate_calendar_features


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    output_dir: str | Path | None = None,
) -> list[Path]:
    cfg = load_calendar_config(config_path)
    manifest = load_manifest(cfg.manifest_path)
    frame = pd.read_parquet(cfg.daily_path)
    holidays = pd.read_parquet(cfg.holiday_snapshot)
    diagnostics = validate_calendar_features(frame)
    diagnostics["summary"] = summarize_calendar_features(frame).iloc[0].to_dict()
    expected_dates = pd.date_range(cfg.start_date, cfg.end_date, freq="D")
    actual_dates = pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="coerce").dropna())
    expected_jurisdictions = {
        (country, subdivision or "") for country, subdivision in cfg.jurisdictions
    }
    actual_jurisdictions = {
        (str(country), "" if pd.isna(subdivision) else str(subdivision))
        for country, subdivision in holidays[["COUNTRY_CODE", "SUBDIVISION"]].itertuples(
            index=False, name=None
        )
    }
    diagnostics["date_completeness"] = {
        "expected_dates": len(expected_dates),
        "observed_dates": len(actual_dates),
        "missing_dates": [
            value.date().isoformat() for value in expected_dates.difference(actual_dates)
        ],
        "unexpected_dates": [
            value.date().isoformat() for value in actual_dates.difference(expected_dates)
        ],
    }
    diagnostics["jurisdiction_completeness"] = {
        "expected": sorted(expected_jurisdictions),
        "observed": sorted(actual_jurisdictions),
        "missing": sorted(expected_jurisdictions.difference(actual_jurisdictions)),
    }
    diagnostics["manifest"] = manifest["product"]
    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    return [
        write_html_report(
            report, title="Human calendar inspection", summary=diagnostics, frame=frame
        )
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)
    for path in inspect(args.config, args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
