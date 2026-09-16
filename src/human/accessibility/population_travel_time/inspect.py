"""Inspect population travel coverage and challenger distributions."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pandas as pd

from human.utils.artifacts import load_manifest
from human.utils.inspection import write_html_report

from .config import DEFAULT_CONFIG_PATH, load_population_travel_config


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    output_dir: str | Path | None = None,
) -> list[Path]:
    cfg = load_population_travel_config(config_path)
    manifest = load_manifest(cfg.manifest_path)
    frame = pd.read_parquet(cfg.output_path)
    index = frame["POPULATION_TRAVEL_OPPORTUNITY_INDEX"]
    summary = {
        "rows": len(frame),
        "source_completeness": manifest["source_completeness"],
        "available_rows": int(frame["POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE"].sum()),
        "unavailable_rows": int((~frame["POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE"]).sum()),
        "median_index": index.median(),
        "p10_index": index.quantile(0.1),
        "p90_index": index.quantile(0.9),
        "median_routed_population_fraction": frame[
            "POPULATION_TRAVEL_ROUTED_SELECTED_POPULATION_FRACTION"
        ].median(),
        "duplicate_h3_keys": int(frame["H3_INDEX"].duplicated().sum()),
    }
    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    return [
        write_html_report(
            report,
            title="Population travel-time inspection",
            summary=summary,
            frame=frame,
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
