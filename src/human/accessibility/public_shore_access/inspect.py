"""Inspect public-access state, source coverage, and waterfront fractions."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import geopandas as gpd
import pandas as pd

from human.utils.artifacts import load_manifest
from human.utils.inspection import write_html_report

from .config import DEFAULT_CONFIG_PATH, load_public_shore_config


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    output_dir: str | Path | None = None,
) -> list[Path]:
    cfg = load_public_shore_config(config_path)
    manifest = load_manifest(cfg.manifest_path)
    facilities = gpd.read_parquet(cfg.facilities_path)
    h3_output = pd.read_parquet(cfg.h3_path)
    fraction = h3_output["ACCESSIBLE_WATERFRONT_FRACTION"]
    bc_facilities = facilities.loc[facilities["JURISDICTION"].eq("BC")]
    summary = {
        "facility_rows": len(facilities),
        "h3_rows": len(h3_output),
        "source_completeness": manifest["source_completeness"],
        "source_measurement_status_counts": pd.Series(
            [source["measurement_status"] for source in manifest["sources"]]
        )
        .value_counts(dropna=False)
        .to_dict(),
        "source_counts": facilities["SOURCE_DATASET"].value_counts(dropna=False).to_dict(),
        "facility_access_states": facilities["PUBLIC_ACCESS_STATE"]
        .value_counts(dropna=False)
        .to_dict(),
        "h3_access_states": h3_output["PUBLIC_ACCESS_STATE"].value_counts(dropna=False).to_dict(),
        "cells_with_fraction": int(fraction.notna().sum()),
        "cells_with_unknown_fraction": int(fraction.isna().sum()),
        "fraction_outside_unit_interval": int(((fraction < 0) | (fraction > 1)).sum()),
        "fraction_denominator_mismatch_cells": int(
            h3_output["FRACTION_DENOMINATOR_MISMATCH_QC"].sum()
        ),
        "maximum_raw_ratio": h3_output["ACCESSIBLE_WATERFRONT_RAW_RATIO_QC"].max(),
        "bc_authoritative_facility_rows": int(len(bc_facilities)),
        "bc_verified_public_facility_rows": int(
            bc_facilities["PUBLIC_ACCESS_STATE"].eq("public").sum()
        ),
        "bc_authoritative_coverage": ("partial" if not bc_facilities.empty else "unavailable"),
        "h3_access_evidence_states": h3_output["PUBLIC_ACCESS_EVIDENCE_STATE"]
        .value_counts(dropna=False)
        .to_dict(),
        "duplicate_site_ids": int(facilities["ACCESS_SITE_ID"].duplicated().sum()),
        "duplicate_h3_keys": int(h3_output["H3_INDEX"].duplicated().sum()),
    }
    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    return [
        write_html_report(
            report,
            title="Public shore access inspection",
            summary=summary,
            frame=h3_output,
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
