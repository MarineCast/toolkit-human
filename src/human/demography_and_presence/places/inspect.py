"""Inspect the normalized places catalog."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pandas as pd

from human.utils.artifacts import load_manifest
from human.utils.inspection import write_html_report

from .config import DEFAULT_CONFIG_PATH, load_places_config
from .utils import read_places_of_interest


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH, output_dir: str | Path | None = None
) -> list[Path]:
    cfg = load_places_config(config_path)
    manifest = load_manifest(cfg.manifest_path)
    frame = pd.DataFrame(read_places_of_interest(cfg.catalog_path))
    has_coordinates = (
        frame[["latitude", "longitude"]].notna().all(axis=1)
        if {"latitude", "longitude"}.issubset(frame.columns)
        else pd.Series(False, index=frame.index)
    )
    west, south, east, north = cfg.bbox
    inside_bbox = (
        has_coordinates
        & frame["longitude"].between(west, east)
        & frame["latitude"].between(south, north)
        if {"latitude", "longitude"}.issubset(frame.columns)
        else pd.Series(False, index=frame.index)
    )
    summary = {
        "rows": len(frame),
        "types": frame["type"].value_counts(dropna=False).to_dict() if "type" in frame else {},
        "categories": (
            frame["category"].value_counts(dropna=False).to_dict() if "category" in frame else {}
        ),
        "sources": (
            frame["source"].value_counts(dropna=False).to_dict() if "source" in frame else {}
        ),
        "duplicate_ids": int(frame["id"].duplicated().sum()) if "id" in frame else None,
        "rows_with_coordinates": int(has_coordinates.sum()),
        "rows_inside_configured_bbox": int(inside_bbox.sum()),
        "map_coverage_fraction": float(inside_bbox.mean()) if len(frame) else None,
        "manifest": manifest["product"],
        "model_eligible": False,
    }
    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    return [
        write_html_report(report, title="Human places inspection", summary=summary, frame=frame)
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
