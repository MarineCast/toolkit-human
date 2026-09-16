"""Inspect daily and weekly AIS activity evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import polars as pl

from human.utils.artifacts import load_manifest
from human.utils.inspection import write_html_report

from .config import DEFAULT_CONFIG_PATH, load_ais_config


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH, output_dir: str | Path | None = None
) -> list[Path]:
    cfg = load_ais_config(config_path)
    manifest = load_manifest(cfg.manifest_path)
    daily = pl.scan_parquet(cfg.daily_path)
    weekly = pl.scan_parquet(cfg.weekly_path)
    daily_summary = (
        daily.select(
            pl.len().alias("rows"),
            pl.struct(["DATE", "H3_INDEX"]).n_unique().alias("unique_keys"),
            pl.col("DATE").min().alias("minimum_date"),
            pl.col("DATE").max().alias("maximum_date"),
            pl.col("H3_INDEX").n_unique().alias("h3_cells"),
            pl.col("SOURCE_TEMPORAL_COVERAGE_FRACTION").min().alias("minimum_temporal_coverage"),
            pl.col("INVALID_SOG_PING_COUNT").sum().alias("invalid_sog_pings"),
        )
        .collect(engine="streaming")
        .row(0, named=True)
    )
    weekly_summary = (
        weekly.select(
            pl.len().alias("rows"),
            pl.struct(["WEEK_START", "H3_INDEX"]).n_unique().alias("unique_keys"),
        )
        .collect(engine="streaming")
        .row(0, named=True)
    )
    qc = json.loads(cfg.qc_path.read_text(encoding="utf-8"))
    summary = {
        "daily_rows": int(daily_summary["rows"]),
        "weekly_rows": int(weekly_summary["rows"]),
        "daily_key_duplicates": int(daily_summary["rows"]) - int(daily_summary["unique_keys"]),
        "weekly_key_duplicates": int(weekly_summary["rows"]) - int(weekly_summary["unique_keys"]),
        "source_completeness": manifest["source_completeness"],
        "minimum_date": daily_summary["minimum_date"],
        "maximum_date": daily_summary["maximum_date"],
        "h3_cells": int(daily_summary["h3_cells"]),
        "minimum_temporal_coverage": float(daily_summary["minimum_temporal_coverage"]),
        "invalid_sog_pings": int(daily_summary["invalid_sog_pings"]),
        "absence_semantics": qc["absence_semantics"],
    }
    sample = daily.head(1_000).collect().to_pandas()
    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    return [
        write_html_report(report, title="AIS activity inspection", summary=summary, frame=sample)
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
