"""Inspect country and harmonized population products."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pandas as pd

from human.utils.artifacts import load_manifest
from human.utils.inspection import write_html_report

from .config import DEFAULT_CONFIG_PATH, load_population_family_config


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH, output_dir: str | Path | None = None
) -> list[Path]:
    cfg = load_population_family_config(config_path)
    manifest = load_manifest(cfg.manifest_path)
    frame = pd.read_parquet(cfg.cross_border_path)
    context = pd.read_parquet(cfg.context_path)
    summary = {
        "country_qualified_rows": len(frame),
        "context_rows": len(context),
        "country_rows": frame.groupby("COUNTRY_CODE").size().to_dict(),
        "population": frame.groupby("COUNTRY_CODE")["POPULATION"].sum().to_dict(),
        "duplicate_country_h3_keys": int(frame.duplicated(["COUNTRY_CODE", "H3_INDEX"]).sum()),
        "duplicate_context_h3_keys": int(context["H3_INDEX"].duplicated().sum()),
        "mixed_vintage_context_cells": int(context["CENSUS_VINTAGE_MIXED_QC"].sum()),
        "context_population": float(context["POPULATION"].sum()),
        "marine_transfer_applied_rows": int(context["MARINE_TRANSFER_APPLIED"].sum()),
        "manifest": manifest["product"],
    }
    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    return [
        write_html_report(
            report, title="Cross-border population inspection", summary=summary, frame=context
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
