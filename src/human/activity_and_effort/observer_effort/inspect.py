"""Inspect the weekly AIS-weighted water reporting-opportunity artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import polars as pl

from human.utils.artifacts import load_manifest
from human.utils.inspection import write_html_report

from .config import DEFAULT_CONFIG_PATH, load_observer_effort_config


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    output_dir: str | Path | None = None,
) -> list[Path]:
    cfg = load_observer_effort_config(config_path)
    manifest = load_manifest(cfg.manifest_path)
    metadata = json.loads(cfg.metadata_path.read_text(encoding="utf-8"))
    weekly = pl.scan_parquet(cfg.weekly_path)
    summary_row = (
        weekly.select(
            pl.len().alias("rows"),
            pl.struct(["WEEK_START", "H3_INDEX"]).n_unique().alias("unique_keys"),
            pl.col("WEEK_START").n_unique().alias("weeks"),
            pl.col("H3_INDEX").n_unique().alias("target_cells"),
            pl.col("WATER_AIS_REPORTING_OPPORTUNITY_RAW").min().alias("raw_minimum"),
            pl.col("WATER_AIS_REPORTING_OPPORTUNITY_RAW").max().alias("raw_maximum"),
            pl.col("WATER_AIS_REPORTING_OPPORTUNITY_INDEX").min().alias("index_minimum"),
            pl.col("WATER_AIS_REPORTING_OPPORTUNITY_INDEX").max().alias("index_maximum"),
            (pl.col("WATER_AIS_REPORTING_OPPORTUNITY_RAW") == 0).sum().alias("zero_rows"),
        )
        .collect(engine="streaming")
        .row(0, named=True)
    )
    summary = {
        **summary_row,
        "key_duplicates": int(summary_row["rows"]) - int(summary_row["unique_keys"]),
        "source_completeness": manifest["source_completeness"],
        "product_status": metadata["status"],
        "viewshed_config_hash_match": metadata["viewshed_contract"]["full_config_hash_match"],
        "viewshed_scientific_hash_match": metadata["viewshed_contract"][
            "scientific_config_hash_match"
        ],
        "index_available": metadata["scaling"]["index_available"],
        "zero_interpretation": metadata["coverage"]["zero_interpretation"],
    }
    sample = (
        weekly.sort("WATER_AIS_REPORTING_OPPORTUNITY_RAW", descending=True)
        .head(1_000)
        .collect()
        .to_pandas()
    )
    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    return [
        write_html_report(
            report,
            title="AIS-weighted water reporting-opportunity inspection",
            summary=summary,
            frame=sample,
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
