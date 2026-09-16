"""Build route-day, native R7 daily, and model R6 weekly ferry effort."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Sequence

import h3
import pandas as pd

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_parquet,
    load_manifest,
    manifest_payload,
)

from .config import DEFAULT_CONFIG_PATH, load_ferry_config
from .generations import new_generation, publish
from .pipeline import build_ferry_daily_source_weights


def build_weekly_r6(route_daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate route-level R7 allocations without collapsing evidence flags."""
    work = route_daily.copy()
    estimated = work["ridership_is_estimated"].fillna(False).astype(bool)
    work["has_observed_ridership"] = work["ferry_rider_minutes"].notna() & ~estimated
    work["has_estimated_ridership"] = work["ferry_rider_minutes"].notna() & estimated
    work["has_duration_fallback"] = (
        work["voyage_duration_source"]
        .astype(str)
        .str.contains("fallback|configured", case=False, regex=True, na=False)
    )
    work["week_start"] = pd.to_datetime(work["service_date"]) - pd.to_timedelta(
        pd.to_datetime(work["service_date"]).dt.dayofweek, unit="D"
    )
    work["source_h3"] = work["source_h3"].astype(str).map(lambda cell: h3.cell_to_parent(cell, 6))
    metrics = [
        "ferry_rider_minutes",
        "ferry_rider_hours",
        "ferry_rider_km",
        "ferry_vessel_minutes",
        "ferry_vessel_hours",
        "ferry_vessel_km",
    ]
    grouped = work.groupby(["week_start", "source_h3"], sort=True, observed=True)
    output = grouped[metrics].sum(min_count=1).reset_index()
    output["ferry_route_count"] = grouped["route_geometry_id"].nunique().to_numpy()
    output["ferry_operator_count"] = grouped["operator"].nunique().to_numpy()
    output["has_observed_ridership"] = grouped["has_observed_ridership"].max().to_numpy()
    output["has_estimated_ridership"] = grouped["has_estimated_ridership"].max().to_numpy()
    output["has_platform_effort"] = grouped["platform_effort_available"].max().to_numpy()
    output["has_duration_fallback"] = grouped["has_duration_fallback"].max().to_numpy()
    output.insert(2, "h3_resolution", 6)
    output["source_coverage_complete"] = False
    output["week_start"] = output["week_start"].dt.date.astype("string")
    if output.duplicated(["week_start", "source_h3"]).any():
        raise ValueError("Ferry weekly R6 key is not unique.")
    return output


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    cfg = load_ferry_config(config_path, resolve_generation=False)
    if cfg.manifest_path.exists() and not overwrite:
        raise FileExistsError(f"Ferry selector exists: {cfg.manifest_path}")
    raw_manifest = load_manifest(cfg.raw_manifest_path)
    if cfg.source_completeness != "complete" and not allow_partial:
        raise ValueError(
            "Ferry sources are partial; rerun with allow_partial=True for research use."
        )
    paths = {
        item["dataset_id"].rsplit(".", 1)[-1]: Path(item["path"])
        for item in raw_manifest["artifacts"]
    }
    with tempfile.TemporaryDirectory(prefix="orcacast-ferry-build-") as temporary:
        result = build_ferry_daily_source_weights(
            wsf_ridership_path=paths["wsf_ridership"],
            bc_ridership_path=paths["bc_ridership"],
            route_segments_path=paths["route_segments"],
            route_config_path=paths["route_mapping"],
            output_dir=temporary,
            wsf_vessel_history_path=paths["wsf_vessel_history"],
            h3_resolution=cfg.native_h3_resolution,
            allow_incomplete_routes=allow_partial,
        )
        route_daily = pd.read_parquet(result.route_level_path)
        daily = pd.read_parquet(result.collapsed_path)
    estimated = route_daily["ridership_is_estimated"].fillna(False).astype(bool)
    route_daily["has_observed_ridership"] = route_daily["ferry_rider_minutes"].notna() & ~estimated
    route_daily["has_estimated_ridership"] = route_daily["ferry_rider_minutes"].notna() & estimated
    route_daily["has_duration_fallback"] = (
        route_daily["voyage_duration_source"]
        .astype(str)
        .str.contains("fallback|configured", case=False, regex=True, na=False)
    )
    route_daily["ferry_route_count"] = 1
    route_daily["platform_effort_coverage_fraction"] = (
        route_daily["platform_effort_available"].fillna(False).astype(float)
    ).where(route_daily["ferry_rider_minutes"].fillna(0) > 0)
    route_daily["source_coverage_complete"] = cfg.source_completeness == "complete"
    evidence = (
        route_daily.groupby(["service_date", "source_h3"], sort=True, observed=True)[
            ["has_observed_ridership", "has_estimated_ridership", "has_duration_fallback"]
        ]
        .max()
        .reset_index()
    )
    daily = daily.merge(
        evidence, on=["service_date", "source_h3"], how="left", validate="one_to_one"
    )
    daily["source_coverage_complete"] = cfg.source_completeness == "complete"
    weekly = build_weekly_r6(route_daily)
    weekly["source_coverage_complete"] = cfg.source_completeness == "complete"
    cfg = new_generation(cfg)
    atomic_write_parquet(route_daily, cfg.route_daily_path)
    atomic_write_parquet(daily, cfg.daily_path)
    atomic_write_parquet(weekly, cfg.weekly_path)
    artifacts = [
        artifact_record(
            cfg.route_daily_path,
            dataset_id="human.ferry.route_daily_r7",
            frame=route_daily,
            h3_resolution=7,
        ),
        artifact_record(
            cfg.daily_path, dataset_id="human.ferry.daily_r7", frame=daily, h3_resolution=7
        ),
        artifact_record(
            cfg.weekly_path, dataset_id="human.ferry.weekly_r6", frame=weekly, h3_resolution=6
        ),
    ]
    payload = manifest_payload(
        config=cfg.human,
        stage="build",
        artifacts=artifacts,
        sources=raw_manifest["sources"],
        inputs=raw_manifest["artifacts"],
        source_completeness=cfg.source_completeness,
        measurement_statuses=[
            MeasurementStatus.OBSERVED,
            MeasurementStatus.DERIVED,
            MeasurementStatus.ESTIMATED,
            MeasurementStatus.FALLBACK,
        ],
        attribution=raw_manifest["attribution"],
        licenses=raw_manifest["licenses"],
        h3_resolution=7,
        temporal=artifacts[1]["temporal_coverage"],
        limitations=[
            "Rider-minutes represent observer opportunity; vessel-minutes and vessel-km represent platform activity.",
            "Partial route and vessel-history coverage remains explicit and is never filled with zero.",
        ],
    )
    payload["routing_policy"] = "immutable_generation_manifest_last"
    publish(cfg, payload)
    return cfg.daily_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(build(args.config, allow_partial=args.allow_partial, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
