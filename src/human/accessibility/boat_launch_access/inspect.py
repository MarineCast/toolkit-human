"""Inspect boat-launch provenance, public status, and capability coverage."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import geopandas as gpd
import pandas as pd

from human.utils.artifacts import load_manifest
from human.utils.inspection import write_html_report

from .config import DEFAULT_CONFIG_PATH, load_boat_launch_config


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    output_dir: str | Path | None = None,
) -> list[Path]:
    cfg = load_boat_launch_config(config_path)
    manifest = load_manifest(cfg.manifest_path)
    facilities = gpd.read_parquet(cfg.facilities_path)
    h3_output = pd.read_parquet(cfg.h3_path)
    summary = {
        "facility_rows": len(facilities),
        "occupied_h3_cells": len(h3_output),
        "source_completeness": manifest["source_completeness"],
        "source_measurement_status_counts": pd.Series(
            [source["measurement_status"] for source in manifest["sources"]]
        )
        .value_counts(dropna=False)
        .to_dict(),
        "jurisdiction_counts": facilities["JURISDICTION"].value_counts(dropna=False).to_dict(),
        "source_counts": facilities["SOURCE_DATASET"].value_counts(dropna=False).to_dict(),
        "public_access_state_counts": facilities["PUBLIC_ACCESS_STATE"]
        .value_counts(dropna=False)
        .to_dict(),
        "ramp_capability_counts": facilities["RAMP_CAPABILITY_STATE"]
        .value_counts(dropna=False)
        .to_dict(),
        "seasonal_operation_known_fraction": (
            float(facilities["SEASONAL_OPERATION_STATE"].ne("unknown").mean())
            if len(facilities)
            else None
        ),
        "duplicate_site_ids": int(facilities["ACCESS_SITE_ID"].duplicated().sum()),
        "duplicate_h3_keys": int(h3_output["H3_INDEX"].duplicated().sum()),
    }
    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    return [
        write_html_report(
            report,
            title="Boat-launch access inspection",
            summary=summary,
            frame=facilities.drop(columns="geometry"),
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
