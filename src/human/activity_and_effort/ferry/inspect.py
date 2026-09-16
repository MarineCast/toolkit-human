"""Inspect ferry route-day, daily R7, and weekly R6 products."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pandas as pd

from human.utils.artifacts import load_manifest
from human.utils.inspection import write_html_report

from .config import DEFAULT_CONFIG_PATH, load_ferry_config


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH, output_dir: str | Path | None = None
) -> list[Path]:
    cfg = load_ferry_config(config_path)
    manifest = load_manifest(cfg.manifest_path)
    daily = pd.read_parquet(cfg.daily_path)
    weekly = pd.read_parquet(cfg.weekly_path)
    summary = {
        "daily_rows": len(daily),
        "weekly_rows": len(weekly),
        "daily_key_duplicates": int(daily.duplicated(["service_date", "source_h3"]).sum()),
        "weekly_key_duplicates": int(weekly.duplicated(["week_start", "source_h3"]).sum()),
        "rider_minutes": float(daily["ferry_rider_minutes"].sum()),
        "vessel_minutes": float(daily["ferry_vessel_minutes"].sum(min_count=1)),
        "source_completeness": manifest["source_completeness"],
    }
    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    return [
        write_html_report(report, title="Ferry effort inspection", summary=summary, frame=daily)
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
