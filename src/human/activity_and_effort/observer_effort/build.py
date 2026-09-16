"""Build an AIS-weighted weekly water reporting-opportunity research product."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence

import h3
import polars as pl

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_json,
    load_manifest,
    manifest_payload,
    sha256_file,
    write_manifest,
)
from human.viewshed.config import load_app_config
from human.viewshed.finalize.final_artifacts import (
    static_scientific_config_hash,
    validate_static_artifact_metadata,
)

from .config import DEFAULT_CONFIG_PATH, ObserverEffortConfig, load_observer_effort_config

STATIC_COLUMNS = {
    "source_h3",
    "target_h3",
    "weight_terrain",
    "weight_distance",
    "weight_vegetation",
    "weight_static_viewability",
}


def _h3_parent_map(cells: Sequence[str], *, resolution: int, name: str) -> pl.DataFrame:
    invalid = [cell for cell in cells if not h3.is_valid_cell(cell)]
    if invalid:
        raise ValueError(f"Invalid {name} H3 cell: {invalid[0]}")
    resolutions = sorted({h3.get_resolution(cell) for cell in cells})
    if resolutions != [7]:
        raise ValueError(f"{name} viewshed cells must be H3 R7; received {resolutions}.")
    return pl.DataFrame(
        {
            name: cells,
            f"{name}_r{resolution}": [h3.cell_to_parent(cell, resolution) for cell in cells],
        }
    )


def build_parent_kernel(path: Path) -> tuple[pl.DataFrame, dict[str, object]]:
    static = pl.read_parquet(path)
    missing = sorted(STATIC_COLUMNS.difference(static.columns))
    if missing:
        raise ValueError(f"Water static viewshed artifact is missing columns: {missing}")
    if static.select(pl.struct(["source_h3", "target_h3"]).is_duplicated().sum()).item():
        raise ValueError("Water static viewshed source-target keys are not unique.")
    for column in (
        "weight_terrain",
        "weight_distance",
        "weight_vegetation",
        "weight_static_viewability",
    ):
        invalid = static.select(
            (
                pl.col(column).is_null()
                | ~pl.col(column).is_finite()
                | ~pl.col(column).is_between(0, 1)
            ).sum()
        ).item()
        if invalid:
            raise ValueError(f"Water static viewshed column {column} has {invalid} invalid rows.")

    source_cells = sorted(static.get_column("source_h3").unique().to_list())
    target_cells = sorted(static.get_column("target_h3").unique().to_list())
    source_map = _h3_parent_map(source_cells, resolution=6, name="source_h3")
    target_map = _h3_parent_map(target_cells, resolution=6, name="target_h3")
    source_counts = (
        source_map.group_by("source_h3_r6").len().rename({"len": "MODELED_SOURCE_R7_CHILD_COUNT"})
    )
    target_counts = (
        target_map.group_by("target_h3_r6").len().rename({"len": "MODELED_TARGET_R7_CHILD_COUNT"})
    )
    kernel = (
        static.join(source_map, on="source_h3", how="left")
        .join(target_map, on="target_h3", how="left")
        .group_by(["source_h3_r6", "target_h3_r6"])
        .agg(
            pl.col("weight_static_viewability").sum().alias("STATIC_WEIGHT_SUM_R7_PAIRS"),
            pl.len().alias("STATIC_CANDIDATE_R7_PAIR_COUNT"),
        )
        .join(source_counts, on="source_h3_r6", how="left")
        .join(target_counts, on="target_h3_r6", how="left")
        .with_columns(
            (
                pl.col("STATIC_WEIGHT_SUM_R7_PAIRS")
                / (
                    pl.col("MODELED_SOURCE_R7_CHILD_COUNT")
                    * pl.col("MODELED_TARGET_R7_CHILD_COUNT")
                )
            ).alias("WATER_STATIC_KERNEL_R6")
        )
        .select(
            "source_h3_r6",
            "target_h3_r6",
            "WATER_STATIC_KERNEL_R6",
            "STATIC_CANDIDATE_R7_PAIR_COUNT",
            "MODELED_SOURCE_R7_CHILD_COUNT",
            "MODELED_TARGET_R7_CHILD_COUNT",
        )
        .sort(["source_h3_r6", "target_h3_r6"])
    )
    if kernel.select(
        (
            pl.col("WATER_STATIC_KERNEL_R6").is_null()
            | ~pl.col("WATER_STATIC_KERNEL_R6").is_finite()
            | ~pl.col("WATER_STATIC_KERNEL_R6").is_between(0, 1)
        ).sum()
    ).item():
        raise ValueError("Aggregated water static kernel is invalid.")
    return kernel, {
        "native_source_r7_cells": len(source_cells),
        "native_target_r7_cells": len(target_cells),
        "source_r6_cells": source_counts.height,
        "target_r6_cells": target_counts.height,
        "kernel_r6_pairs": kernel.height,
        "kernel_minimum": kernel.get_column("WATER_STATIC_KERNEL_R6").min(),
        "kernel_maximum": kernel.get_column("WATER_STATIC_KERNEL_R6").max(),
        "uniform_within_parent_assumption": (
            "Each observed R6 vessel-hour is distributed uniformly across modeled R7 water-source "
            "children before applying the R7 static kernel; target R7 children are averaged to R6."
        ),
    }


def _raw_column_name(activity_column: str, primary: str) -> str:
    if activity_column == primary:
        return "WATER_AIS_REPORTING_OPPORTUNITY_RAW"
    prefix = activity_column.removesuffix("_VESSEL_HOURS_PROXY")
    if prefix == "ACTIVE":
        prefix = "ALL_VESSEL"
    return f"WATER_{prefix}_VIEWABILITY_RAW"


def _complete_reference_weeks(
    weekly_coverage: pl.DataFrame,
    *,
    required_weeks: int,
) -> list[object]:
    """Return the first consecutive, explicitly complete scaling window."""

    candidates = (
        weekly_coverage.filter(pl.col("SOURCE_COVERAGE_COMPLETE"))
        .get_column("WEEK_START")
        .to_list()
    )
    run: list[object] = []
    previous = None
    for week in candidates:
        if previous is None or (week - previous).days == 7:
            run.append(week)
        else:
            run = [week]
        if len(run) == required_weeks:
            return run
        previous = week
    return []


def _build_dynamic(
    cfg: ObserverEffortConfig, kernel: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, object]]:
    required = {
        "WEEK_START",
        "H3_INDEX",
        "H3_RESOLUTION",
        cfg.primary_activity_column,
        *cfg.component_activity_columns,
        "SOURCE_OBSERVED_HOURS",
        "SOURCE_EXPECTED_HOURS",
        "SOURCE_TEMPORAL_COVERAGE_FRACTION",
        "SOURCE_COVERAGE_COMPLETE",
        "SOURCE_COVERAGE_STATUS",
    }
    ais_schema = pl.scan_parquet(cfg.ais_weekly_path).collect_schema()
    missing = sorted(required.difference(ais_schema.names()))
    if missing:
        raise ValueError(f"Weekly AIS artifact is missing columns: {missing}")
    source_cells = kernel.get_column("source_h3_r6").unique().to_list()
    target_cells = sorted(kernel.get_column("target_h3_r6").unique().to_list())
    activity_columns = [cfg.primary_activity_column, *cfg.component_activity_columns]
    ais = (
        pl.scan_parquet(cfg.ais_weekly_path)
        .select(sorted(required))
        .filter(pl.col("H3_INDEX").is_in(source_cells))
        .rename({"H3_INDEX": "source_h3_r6"})
    )
    if ais.select(pl.struct(["WEEK_START", "source_h3_r6"]).is_duplicated().sum()).collect().item():
        raise ValueError("Weekly AIS source keys are not unique.")

    weighted_expressions = [
        (pl.col(column) * pl.col("WATER_STATIC_KERNEL_R6"))
        .sum()
        .alias(_raw_column_name(column, cfg.primary_activity_column))
        for column in activity_columns
    ]
    dynamic = (
        ais.join(kernel.lazy(), on="source_h3_r6", how="inner")
        .group_by(["WEEK_START", "target_h3_r6"])
        .agg(
            *weighted_expressions,
            pl.col("source_h3_r6")
            .filter(pl.col(cfg.primary_activity_column) > 0)
            .n_unique()
            .alias("AIS_SOURCE_R6_WITH_OBSERVER_ACTIVITY"),
        )
    )
    weekly_coverage = (
        pl.scan_parquet(cfg.ais_weekly_path)
        .group_by("WEEK_START")
        .agg(
            pl.col("SOURCE_OBSERVED_HOURS").max(),
            pl.col("SOURCE_EXPECTED_HOURS").max(),
            pl.col("SOURCE_TEMPORAL_COVERAGE_FRACTION").max(),
            pl.col("SOURCE_COVERAGE_COMPLETE").all(),
            pl.col("SOURCE_COVERAGE_STATUS").first(),
        )
        .sort("WEEK_START")
        .collect(engine="streaming")
    )
    weeks = weekly_coverage.get_column("WEEK_START").to_list()
    grid = pl.DataFrame({"WEEK_START": weeks}).join(
        pl.DataFrame({"target_h3_r6": target_cells}), how="cross"
    )
    raw_columns = [
        _raw_column_name(column, cfg.primary_activity_column) for column in activity_columns
    ]
    frame = (
        grid.lazy()
        .join(dynamic, on=["WEEK_START", "target_h3_r6"], how="left")
        .join(weekly_coverage.lazy(), on="WEEK_START", how="left")
        .with_columns(
            *[
                pl.when(pl.col("SOURCE_COVERAGE_COMPLETE"))
                .then(pl.col(column).fill_null(0.0))
                .when(pl.col(column) > 0)
                .then(pl.col(column))
                .otherwise(pl.lit(None, dtype=pl.Float64))
                .alias(column)
                for column in raw_columns
            ],
            pl.col("AIS_SOURCE_R6_WITH_OBSERVER_ACTIVITY").fill_null(0),
        )
        .collect(engine="streaming")
        .sort(["WEEK_START", "target_h3_r6"])
    )
    reference_weeks = _complete_reference_weeks(
        weekly_coverage,
        required_weeks=cfg.scaling_reference_weeks,
    )
    primary_raw = "WATER_AIS_REPORTING_OPPORTUNITY_RAW"
    log_cap = None
    if reference_weeks:
        log_cap = (
            frame.filter(pl.col("WEEK_START").is_in(reference_weeks))
            .select(
                pl.col(primary_raw)
                .drop_nulls()
                .log1p()
                .quantile(cfg.scaling_quantile, interpolation="linear")
            )
            .item()
        )
        if log_cap is None or not 0 < float(log_cap):
            raise ValueError("AIS water reporting-opportunity scaling cap is not positive.")
    scaled_index = (
        (pl.col(primary_raw).log1p() / float(log_cap)).clip(0.0, 1.0)
        if log_cap is not None
        else pl.lit(None, dtype=pl.Float64)
    )
    frame = (
        frame.with_columns(
            scaled_index.alias("WATER_AIS_REPORTING_OPPORTUNITY_INDEX"),
            pl.when(pl.col(primary_raw).is_null())
            .then(pl.lit("unavailable_partial_spatial_or_acquisition_coverage"))
            .when(pl.col("SOURCE_COVERAGE_COMPLETE") & (pl.col(primary_raw) == 0))
            .then(pl.lit("derived_zero_complete_coverage"))
            .when(pl.col("SOURCE_COVERAGE_COMPLETE"))
            .then(pl.lit("derived_observed_activity_complete_coverage"))
            .when(pl.col(primary_raw) > 0)
            .then(pl.lit("derived_observed_activity_partial_coverage"))
            .otherwise(pl.lit("unavailable_partial_spatial_or_acquisition_coverage"))
            .alias("WATER_VIEWABILITY_STATUS"),
            pl.lit(6).cast(pl.Int8).alias("H3_RESOLUTION"),
            pl.when(pl.col(primary_raw).is_null())
            .then(pl.lit(MeasurementStatus.UNAVAILABLE.value))
            .otherwise(pl.lit(MeasurementStatus.DERIVED.value))
            .alias("MEASUREMENT_STATUS"),
        )
        .rename({"target_h3_r6": "H3_INDEX"})
        .select(
            "WEEK_START",
            "H3_INDEX",
            "H3_RESOLUTION",
            primary_raw,
            "WATER_AIS_REPORTING_OPPORTUNITY_INDEX",
            *[column for column in raw_columns if column != primary_raw],
            "AIS_SOURCE_R6_WITH_OBSERVER_ACTIVITY",
            "SOURCE_OBSERVED_HOURS",
            "SOURCE_EXPECTED_HOURS",
            "SOURCE_TEMPORAL_COVERAGE_FRACTION",
            "SOURCE_COVERAGE_COMPLETE",
            "SOURCE_COVERAGE_STATUS",
            "WATER_VIEWABILITY_STATUS",
            "MEASUREMENT_STATUS",
        )
    )
    return frame, {
        "scaling_quantile": cfg.scaling_quantile,
        "required_reference_weeks": cfg.scaling_reference_weeks,
        "complete_reference_weeks": len(reference_weeks),
        "reference_start_week": str(reference_weeks[0]) if reference_weeks else None,
        "reference_end_week": str(reference_weeks[-1]) if reference_weeks else None,
        "primary_log1p_q99_cap": float(log_cap) if log_cap is not None else None,
        "index_available": log_cap is not None,
    }


def _write_parquet(frame: pl.DataFrame, path: Path, *, overwrite: bool) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        frame.write_parquet(temporary, compression="zstd", statistics=True)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    cfg = load_observer_effort_config(config_path)
    ais_manifest = load_manifest(cfg.ais_manifest_path)
    if ais_manifest["source_completeness"] != cfg.source_completeness:
        raise ValueError("Observer-effort config and AIS manifest disagree on source completeness.")
    if ais_manifest["source_completeness"] != "complete" and not allow_partial:
        raise ValueError(
            "Observer-effort AIS source coverage is partial; rerun with allow_partial=True "
            "for research use."
        )
    app = load_app_config(cfg.viewshed_config_path)
    static_metadata = validate_static_artifact_metadata(
        cfg.water_static_weights_path,
        raw=app.raw_config,
        source_type="water",
    )
    kernel, kernel_metadata = build_parent_kernel(cfg.water_static_weights_path)
    frame, scaling_metadata = _build_dynamic(cfg, kernel)
    if frame.select(pl.struct(["WEEK_START", "H3_INDEX"]).is_duplicated().sum()).item():
        raise ValueError("Water AIS reporting-opportunity keys are not unique.")
    primary_raw = "WATER_AIS_REPORTING_OPPORTUNITY_RAW"
    if frame.select(
        (
            (pl.col(primary_raw).is_not_null() & ~pl.col(primary_raw).is_finite())
            | (pl.col(primary_raw) < 0).fill_null(False)
            | (
                pl.col("WATER_AIS_REPORTING_OPPORTUNITY_INDEX").is_not_null()
                & ~pl.col("WATER_AIS_REPORTING_OPPORTUNITY_INDEX").is_between(0, 1)
            )
            | (pl.col("SOURCE_COVERAGE_COMPLETE") & pl.col(primary_raw).is_null())
        ).sum()
    ).item():
        raise ValueError("Water AIS reporting-opportunity output contains invalid values.")

    artifact_viewshed_hash = str(static_metadata.get("config_hash", "unknown"))
    artifact_scientific_hash = str(static_metadata.get("scientific_config_hash", "unknown"))
    current_viewshed_hash = app.config_hash
    current_scientific_hash = static_scientific_config_hash(app.raw_config)
    if artifact_scientific_hash != current_scientific_hash:
        raise ValueError(
            "Water static viewshed scientific configuration does not match the active "
            "viewshed configuration. Rebuild the paired static artifacts before observer "
            f"effort: artifact={artifact_scientific_hash} current={current_scientific_hash}"
        )
    known_limitations = [
        "Legacy AIS provider, license, attribution, receiver coverage, and jurisdiction completeness are unresolved.",
        "Activity is an MMSI-hour proxy at H3 R6, not exact duration or vessel kilometres.",
        "Passenger and recreational classes are observer-capable proxies, not direct observer effort.",
        (
            "One small-craft-height static water kernel is applied to every AIS class; "
            "passenger and ferry bridge-height opportunity requires a separate "
            "class-specific kernel."
        ),
        "Ferry activity is represented only through AIS here; no separate ferry effort is added or double counted.",
        "R6 activity is distributed uniformly across modeled R7 source children.",
    ]
    if artifact_viewshed_hash != current_viewshed_hash:
        known_limitations.append(
            "The full viewshed config hash differs, but the scientific static-kernel hash matches; "
            "the difference is confined to non-scientific configuration."
        )
    metadata = {
        "schema_version": "0.1.0-research",
        "product": "human.water_ais_reporting_opportunity_weekly_r6",
        "status": "deprecated_compatibility_sensitivity_research_partial_legacy_ais",
        "product_role": "deprecated_compatibility_sensitivity",
        "canonical_primary_composite": False,
        "canonical_replacement": "human.water_observation_opportunity",
        "grain": ["WEEK_START", "H3_INDEX", "H3_RESOLUTION"],
        "rows": frame.height,
        "weeks": frame.get_column("WEEK_START").n_unique(),
        "target_cells": frame.get_column("H3_INDEX").n_unique(),
        "week_start_minimum": str(frame.get_column("WEEK_START").min()),
        "week_start_maximum": str(frame.get_column("WEEK_START").max()),
        "primary_activity_column": cfg.primary_activity_column,
        "component_activity_columns": list(cfg.component_activity_columns),
        "formula": (
            "sum_source_r6(activity_vessel_hours_proxy * mean_R7_child_pair_static_weight), "
            "then log1p/q99 fixed-reference scaling"
        ),
        "kernel": kernel_metadata,
        "scaling": scaling_metadata,
        "coverage": {
            "source_completeness": ais_manifest["source_completeness"],
            "unavailable_is_zero": False,
            "zero_interpretation": (
                "Zero is emitted only for a coverage-complete week with no activity contribution. "
                "Incomplete target-weeks are null unless a positive partial contribution was observed."
            ),
        },
        "viewshed_contract": {
            "static_weights_path": str(cfg.water_static_weights_path),
            "static_weights_sha256": sha256_file(cfg.water_static_weights_path),
            "artifact_config_hash": artifact_viewshed_hash,
            "current_config_hash": current_viewshed_hash,
            "artifact_scientific_config_hash": artifact_scientific_hash,
            "current_scientific_config_hash": current_scientific_hash,
            "scientific_config_hash_match": True,
            "full_config_hash_match": artifact_viewshed_hash == current_viewshed_hash,
            "observer_height_class": app.raw_config.get("water_viewing", {}).get(
                "default_observer_height_class"
            ),
            "target_visibility_height_m": app.raw_config.get("water_viewing", {}).get(
                "target_visibility_height_m"
            ),
        },
        "known_limitations": known_limitations,
    }
    _write_parquet(frame, cfg.weekly_path, overwrite=overwrite)
    atomic_write_json(cfg.metadata_path, metadata, overwrite=overwrite)
    artifacts = [
        artifact_record(
            cfg.weekly_path,
            dataset_id="human.observer_effort.water_ais_reporting_opportunity_weekly_r6",
            h3_resolution=6,
        ),
        artifact_record(
            cfg.metadata_path,
            dataset_id="human.observer_effort.water_ais_reporting_opportunity_metadata",
            h3_resolution=6,
        ),
    ]
    payload = manifest_payload(
        config=cfg.human,
        stage="build",
        artifacts=artifacts,
        sources=ais_manifest["sources"],
        inputs=[
            artifact_record(cfg.ais_weekly_path, dataset_id="human.ais.weekly_r6", h3_resolution=6),
            artifact_record(
                cfg.water_static_weights_path,
                dataset_id="human.viewshed.water_static_weights_r7",
                h3_resolution=7,
            ),
            artifact_record(
                cfg.viewshed_config_path,
                dataset_id="human.viewshed.configuration",
                h3_resolution=7,
            ),
        ],
        source_completeness="partial",
        measurement_statuses=[MeasurementStatus.OBSERVED, MeasurementStatus.DERIVED],
        attribution=ais_manifest["attribution"],
        licenses=ais_manifest["licenses"],
        h3_resolution=6,
        temporal={
            "minimum": metadata["week_start_minimum"],
            "maximum": metadata["week_start_maximum"],
        },
        limitations=metadata["known_limitations"],
    )
    write_manifest(cfg.manifest_path, payload)
    return cfg.weekly_path


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
