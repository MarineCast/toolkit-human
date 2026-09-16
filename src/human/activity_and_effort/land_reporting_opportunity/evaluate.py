"""Evaluate land reporting-opportunity components and composites against reports.

This retrospective analysis is deliberately downstream of the proxy build. Reported
sightings are never used to construct or scale an opportunity feature.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

import h3
import matplotlib
import numpy as np
import pandas as pd
import polars as pl

from human.utils.artifacts import (
    atomic_write_json,
    atomic_write_parquet,
    load_manifest,
    sha256_file,
)

from .config import DEFAULT_CONFIG_PATH, LandReportingConfig, load_land_reporting_config

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

VARIANTS: tuple[tuple[str, str, str], ...] = (
    ("physical_viewability", "LAND_STATIC_WEIGHT_SUM", "#777777"),
    ("population_exact_cell", "POPULATION_EXACT_CELL_RAW", "#457b9d"),
    (
        "land_reachability_component",
        "LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW",
        "#e9c46a",
    ),
    ("mapped_public_access_composite", "MAPPED_PUBLIC_ACCESS_SUPPORTED_RAW", "#2a9d8f"),
    ("verified_public_access_support", "VERIFIED_PUBLIC_ACCESS_SUPPORTED_RAW", "#1d746b"),
)
PRIMARY_VARIANT = "mapped_public_access_composite"
PRIMARY_COLUMN = "MAPPED_PUBLIC_ACCESS_SUPPORTED_RAW"
DYNAMIC_VARIANTS: tuple[tuple[str, str, str], ...] = (
    ("physical_viewability", "PHYSICAL_VIEWABILITY_RAW", "#777777"),
    (
        "population_travel_opportunity",
        "POPULATION_TRAVEL_OPPORTUNITY_RAW",
        "#457b9d",
    ),
    ("road_access_opportunity", "ROAD_ACCESS_OPPORTUNITY_RAW", "#8d6e63"),
    ("city_access_opportunity", "CITY_ACCESS_OPPORTUNITY_RAW", "#6d597a"),
    (
        "transport_access_opportunity",
        "TRANSPORT_ACCESS_OPPORTUNITY_RAW",
        "#f4a261",
    ),
    (
        "land_reachability_opportunity",
        "LAND_REACHABILITY_OPPORTUNITY_RAW",
        "#e9c46a",
    ),
    ("land_effort_proxy_composite", "LAND_EFFORT_PROXY_RAW", "#2a9d8f"),
    (
        "verified_land_effort_lower_bound",
        "VERIFIED_LAND_EFFORT_PROXY_RAW",
        "#1d746b",
    ),
)
POPULATION_PATH = (
    "data/processed/domain/human/demography_and_presence/population/"
    "population_context_h3_r7.parquet"
)
SIGHTINGS_PATH = "data/processed/domain/whale_layer/sightings/observations.parquet"
EVALUATION_OUTPUT_DIR = "outputs/effort/land_source_context"
EVALUATION_START_DATE = "2020-01-01"
RESPONSE_COLUMNS = (
    "REPORTED_SIGHTING_COUNT",
    "REPORTED_SIGHTING_DAYS",
    "HAS_REPORTED_SIGHTING",
)


def _validate_unique(frame: pd.DataFrame, column: str, *, label: str) -> None:
    if frame[column].isna().any() or frame[column].duplicated().any():
        raise ValueError(f"{label} must have unique, non-null {column} values.")


def build_population_baseline(
    static: pd.DataFrame,
    population: pd.DataFrame,
    *,
    cap_quantile: float = 0.99,
) -> tuple[pd.DataFrame, float]:
    """Build an inspectable exact-source-cell population baseline."""
    _validate_unique(population, "H3_INDEX", label="Population context")
    source = (
        static[["source_h3"]]
        .drop_duplicates()
        .merge(
            population[["H3_INDEX", "POPULATION_LOG1P", "POPULATION_CONTEXT_AVAILABLE"]],
            left_on="source_h3",
            right_on="H3_INDEX",
            how="left",
            validate="one_to_one",
        )
    )
    source_available = (
        source["POPULATION_CONTEXT_AVAILABLE"].eq(True) & source["POPULATION_LOG1P"].notna()
    )
    cap = float(source.loc[source_available, "POPULATION_LOG1P"].quantile(cap_quantile))
    if not math.isfinite(cap) or cap <= 0:
        raise ValueError("Population baseline scaling cap is invalid.")
    source["POPULATION_EXACT_CELL_COMPONENT"] = (
        (source["POPULATION_LOG1P"] / cap).clip(0.0, 1.0).where(source_available)
    )
    weighted = static.merge(
        source[["source_h3", "POPULATION_EXACT_CELL_COMPONENT"]],
        on="source_h3",
        how="left",
        validate="many_to_one",
    )
    weighted["_POPULATION_CONTRIBUTION"] = (
        weighted["weight_static_viewability"] * weighted["POPULATION_EXACT_CELL_COMPONENT"]
    )
    weighted["_POPULATION_AVAILABLE_STATIC_WEIGHT"] = weighted["weight_static_viewability"].where(
        weighted["POPULATION_EXACT_CELL_COMPONENT"].notna()
    )
    target = (
        weighted.groupby("target_h3", as_index=False)
        .agg(
            POPULATION_EXACT_CELL_RAW=(
                "_POPULATION_CONTRIBUTION",
                lambda values: values.sum(min_count=1),
            ),
            POPULATION_AVAILABLE_SOURCE_COUNT=("POPULATION_EXACT_CELL_COMPONENT", "count"),
            POPULATION_AVAILABLE_STATIC_WEIGHT=(
                "_POPULATION_AVAILABLE_STATIC_WEIGHT",
                "sum",
            ),
            POPULATION_TOTAL_STATIC_WEIGHT=("weight_static_viewability", "sum"),
        )
        .rename(columns={"target_h3": "H3_INDEX"})
    )
    target["POPULATION_CONTEXT_COVERAGE"] = target["POPULATION_AVAILABLE_STATIC_WEIGHT"] / target[
        "POPULATION_TOTAL_STATIC_WEIGHT"
    ].replace(0.0, np.nan)
    return target, cap


def prepare_sightings(
    observations: pd.DataFrame,
    target_cells: set[str],
    *,
    start_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {
        "OBSERVATION_ID",
        "SIGHTING_DATE",
        "LATITUDE",
        "LONGITUDE",
        "PUBLIC_RELEASE_ELIGIBLE",
    }
    missing = sorted(required.difference(observations.columns))
    if missing:
        raise ValueError(f"Sightings artifact is missing columns: {missing}")
    eligible = observations.loc[observations["PUBLIC_RELEASE_ELIGIBLE"].eq(True)].copy()
    eligible["SIGHTING_DATE"] = pd.to_datetime(
        eligible["SIGHTING_DATE"], errors="coerce"
    ).dt.normalize()
    eligible = eligible.loc[
        eligible["SIGHTING_DATE"].notna()
        & eligible["LATITUDE"].between(-90.0, 90.0)
        & eligible["LONGITUDE"].between(-180.0, 180.0)
        & eligible["SIGHTING_DATE"].ge(pd.Timestamp(start_date))
    ].copy()
    if eligible.empty:
        raise ValueError("No public-release-eligible sightings remain in the evaluation window.")
    eligible["H3_INDEX"] = [
        h3.latlng_to_cell(float(lat), float(lon), 7)
        for lat, lon in zip(eligible["LATITUDE"], eligible["LONGITUDE"], strict=True)
    ]
    end_date = eligible["SIGHTING_DATE"].max()
    eligible = eligible.loc[
        eligible["SIGHTING_DATE"].le(end_date) & eligible["H3_INDEX"].isin(target_cells)
    ].copy()
    grouped = (
        eligible.groupby("H3_INDEX", as_index=False)
        .agg(
            REPORTED_SIGHTING_COUNT=("OBSERVATION_ID", "nunique"),
            REPORTED_SIGHTING_DAYS=("SIGHTING_DATE", "nunique"),
        )
        .sort_values("H3_INDEX")
    )
    return grouped, {
        "start_date": pd.Timestamp(start_date).date().isoformat(),
        "end_date": end_date.date().isoformat(),
        "eligible_sightings_in_target_universe": int(len(eligible)),
        "target_cells_with_reported_sighting": int(len(grouped)),
    }


def build_comparison(
    target: pd.DataFrame,
    population_baseline: pd.DataFrame,
    sightings: pd.DataFrame,
) -> pd.DataFrame:
    _validate_unique(target, "H3_INDEX", label="Land reporting target")
    _validate_unique(population_baseline, "H3_INDEX", label="Population baseline")
    comparison = target.merge(
        population_baseline, on="H3_INDEX", how="left", validate="one_to_one"
    ).merge(sightings, on="H3_INDEX", how="left", validate="one_to_one")
    for column in ("REPORTED_SIGHTING_COUNT", "REPORTED_SIGHTING_DAYS"):
        comparison[column] = comparison[column].fillna(0).astype("int64")
    comparison["HAS_REPORTED_SIGHTING"] = comparison["REPORTED_SIGHTING_COUNT"].gt(0)
    return comparison.sort_values("H3_INDEX").reset_index(drop=True)


def association_table(comparison: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    broad_columns = {
        "LAND_STATIC_WEIGHT_SUM",
        "POPULATION_EXACT_CELL_RAW",
        "LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW",
    }
    broad_support = comparison[list(broad_columns)].notna().all(axis=1)
    for variant, column, _color in VARIANTS:
        scopes = {"native_available": comparison[column].notna()}
        if column in broad_columns:
            scopes["shared_broad_support"] = broad_support
        for scope, mask in scopes.items():
            evaluation = comparison.loc[mask, [column, *RESPONSE_COLUMNS]]
            for method in ("spearman", "pearson"):
                correlations = evaluation.corr(method=method, numeric_only=True)
                for response in RESPONSE_COLUMNS:
                    rows.append(
                        {
                            "pressure_variant": variant,
                            "pressure_column": column,
                            "evaluation_scope": scope,
                            "supported_target_cells": int(len(evaluation)),
                            "method": method,
                            "response": response,
                            "correlation": float(correlations.loc[column, response]),
                        }
                    )
    return pd.DataFrame(rows)


def temporal_holdout_table(
    comparison: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    split = pd.Timestamp("2023-01-01")
    windows = {
        "2020_2022": (pd.Timestamp(start_date), split - pd.Timedelta(days=1)),
        "2023_current": (split, pd.Timestamp(end_date)),
    }
    rows: list[dict[str, Any]] = []
    for window_name, (window_start, window_end) in windows.items():
        window = observations.loc[observations["SIGHTING_DATE"].between(window_start, window_end)]
        counts = window.groupby("H3_INDEX", as_index=False).agg(
            REPORTED_SIGHTING_COUNT=("OBSERVATION_ID", "nunique"),
            REPORTED_SIGHTING_DAYS=("SIGHTING_DATE", "nunique"),
        )
        evaluation = comparison.drop(columns=list(RESPONSE_COLUMNS)).merge(
            counts, on="H3_INDEX", how="left", validate="one_to_one"
        )
        evaluation["REPORTED_SIGHTING_COUNT"] = evaluation["REPORTED_SIGHTING_COUNT"].fillna(0)
        evaluation["REPORTED_SIGHTING_DAYS"] = evaluation["REPORTED_SIGHTING_DAYS"].fillna(0)
        evaluation["HAS_REPORTED_SIGHTING"] = evaluation["REPORTED_SIGHTING_COUNT"].gt(0)
        for variant, column, _color in VARIANTS:
            supported = evaluation.loc[evaluation[column].notna()]
            for response in RESPONSE_COLUMNS:
                rows.append(
                    {
                        "window_name": window_name,
                        "window_start_date": window_start.date().isoformat(),
                        "window_end_date": window_end.date().isoformat(),
                        "pressure_variant": variant,
                        "pressure_column": column,
                        "supported_target_cells": int(len(supported)),
                        "method": "spearman",
                        "response": response,
                        "correlation": float(
                            supported[[column, response]].corr(method="spearman").iloc[0, 1]
                        ),
                    }
                )
    return pd.DataFrame(rows)


def decile_table(comparison: pd.DataFrame) -> pd.DataFrame:
    supported = comparison.loc[comparison[PRIMARY_COLUMN].notna()].copy()
    supported["PRESSURE_PERCENTILE"] = supported[PRIMARY_COLUMN].rank(method="average", pct=True)
    supported["PRESSURE_DECILE"] = pd.Series(pd.NA, index=supported.index, dtype="Int64")
    zero_pressure = supported[PRIMARY_COLUMN].eq(0.0)
    positive_pressure = supported[PRIMARY_COLUMN].gt(0.0)
    supported.loc[zero_pressure, "PRESSURE_DECILE"] = 0
    supported.loc[positive_pressure, "PRESSURE_DECILE"] = (
        pd.qcut(
            supported.loc[positive_pressure, PRIMARY_COLUMN],
            q=10,
            labels=False,
            duplicates="raise",
        )
        .add(1)
        .astype("Int64")
    )
    return (
        supported.groupby("PRESSURE_DECILE", as_index=False)
        .agg(
            TARGET_CELLS=("H3_INDEX", "size"),
            MEAN_PRESSURE_PERCENTILE=("PRESSURE_PERCENTILE", "mean"),
            MEAN_CONTEXT_COVERAGE=("MAPPED_ACCESS_CONTEXT_FRACTION", "mean"),
            REPORTED_SIGHTING_COUNT=("REPORTED_SIGHTING_COUNT", "sum"),
            MEAN_REPORTED_SIGHTING_DAYS=("REPORTED_SIGHTING_DAYS", "mean"),
            FRACTION_WITH_REPORTED_SIGHTING=("HAS_REPORTED_SIGHTING", "mean"),
        )
        .sort_values("PRESSURE_DECILE")
    )


def _atomic_save_figure(figure: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{os.getpid()}.part{path.suffix}")
    try:
        figure.savefig(temporary, dpi=180, bbox_inches="tight")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
        plt.close(figure)


def write_figures(
    comparison: pd.DataFrame,
    associations: pd.DataFrame,
    deciles: pd.DataFrame,
    *,
    output_dir: Path,
    start_date: str,
    end_date: str,
) -> tuple[Path, Path]:
    supported = comparison.loc[comparison[PRIMARY_COLUMN].notna()].copy()
    supported["PRESSURE_PERCENTILE"] = supported[PRIMARY_COLUMN].rank(method="average", pct=True)
    figure, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    hexbin = axes[0].hexbin(
        supported["PRESSURE_PERCENTILE"],
        np.log1p(supported["REPORTED_SIGHTING_DAYS"]),
        gridsize=35,
        mincnt=1,
        cmap="viridis",
    )
    axes[0].set_xlabel("Mapped-public-access composite percentile")
    axes[0].set_ylabel("log1p(reported sighting days)")
    axes[0].set_title("Target-cell reports vs. static composite")
    figure.colorbar(hexbin, ax=axes[0], label="Target cells per hex")
    axes[1].bar(
        deciles["PRESSURE_DECILE"],
        deciles["MEAN_REPORTED_SIGHTING_DAYS"],
        color="#e9c46a",
        alpha=0.9,
    )
    axes[1].set_xlabel("Opportunity group (0 = zero; 1-10 = positive deciles)")
    axes[1].set_ylabel("Mean reported sighting days per target", color="#9a6b00")
    axes[1].set_xticks(range(0, 11))
    axes[1].set_title("Reported sightings by opportunity decile")
    fraction_axis = axes[1].twinx()
    fraction_axis.plot(
        deciles["PRESSURE_DECILE"],
        deciles["FRACTION_WITH_REPORTED_SIGHTING"],
        color="#e76f51",
        marker="o",
        linewidth=2,
    )
    fraction_axis.set_ylabel("Fraction with at least one report", color="#b94f37")
    fraction_axis.set_ylim(0.0, 1.0)
    figure.suptitle(f"Public-release sightings, {start_date} to {end_date}", y=1.02)
    figure.tight_layout()
    primary_path = output_dir / "sightings_vs_land_viewing_pressure.png"
    _atomic_save_figure(figure, primary_path)

    variant_rows = associations.loc[
        associations["evaluation_scope"].eq("native_available")
        & associations["method"].eq("spearman")
        & associations["response"].eq("REPORTED_SIGHTING_DAYS")
    ].sort_values("correlation")
    color_by_variant = {variant: color for variant, _column, color in VARIANTS}
    labels = [
        f"{row.pressure_variant} (n={int(row.supported_target_cells):,})"
        for row in variant_rows.itertuples(index=False)
    ]
    variant_figure, axis = plt.subplots(figsize=(11, 6.5))
    axis.barh(
        labels,
        variant_rows["correlation"],
        color=[color_by_variant[value] for value in variant_rows["pressure_variant"]],
    )
    axis.axvline(0.0, color="#333333", linewidth=0.8)
    axis.set_xlabel("Spearman correlation with reported sighting days")
    axis.set_ylabel("Current packaged construction; native non-null support")
    axis.set_title("Static land reporting-opportunity components and composites")
    variant_figure.tight_layout()
    variants_path = output_dir / "sightings_vs_land_viewing_pressure_variants.png"
    _atomic_save_figure(variant_figure, variants_path)
    return primary_path, variants_path


def _write_csv(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        frame.to_csv(temporary, index=False)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def _dynamic_report_frame(
    observations: pd.DataFrame,
    *,
    target_cells: set[str],
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> pd.DataFrame:
    eligible = observations.loc[observations["PUBLIC_RELEASE_ELIGIBLE"].eq(True)].copy()
    eligible["DATE"] = pd.to_datetime(eligible["SIGHTING_DATE"], errors="coerce").dt.normalize()
    eligible = eligible.loc[
        eligible["DATE"].between(start_date, end_date)
        & eligible["LATITUDE"].between(-90.0, 90.0)
        & eligible["LONGITUDE"].between(-180.0, 180.0)
    ].copy()
    eligible["H3_INDEX"] = [
        h3.latlng_to_cell(float(lat), float(lon), 6)
        for lat, lon in zip(eligible["LATITUDE"], eligible["LONGITUDE"], strict=True)
    ]
    return eligible.loc[eligible["H3_INDEX"].isin(target_cells)].copy()


def _dynamic_associations(
    spatial: pd.DataFrame,
    temporal: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for grain, frame, responses in (
        (
            "spatial_h3_r6",
            spatial,
            ("REPORTED_SIGHTING_COUNT", "REPORTED_SIGHTING_DAYS", "HAS_REPORTED_SIGHTING"),
        ),
        (
            "temporal_daily_region",
            temporal,
            ("REPORTED_SIGHTING_COUNT", "HAS_REPORTED_SIGHTING"),
        ),
    ):
        for variant, column, _color in DYNAMIC_VARIANTS:
            supported = frame.loc[frame[column].notna(), [column, *responses]]
            for method in ("spearman", "pearson"):
                correlations = supported.corr(method=method, numeric_only=True)
                for response in responses:
                    rows.append(
                        {
                            "evaluation_grain": grain,
                            "proxy_variant": variant,
                            "proxy_column": column,
                            "supported_rows": int(len(supported)),
                            "method": method,
                            "response": response,
                            "correlation": float(correlations.loc[column, response]),
                        }
                    )
    return pd.DataFrame(rows)


def evaluate_dynamic_components(
    cfg: LandReportingConfig,
    observations: pd.DataFrame,
    *,
    output_dir: Path,
) -> tuple[list[Path], pd.DataFrame, dict[str, Any]]:
    scan = pl.scan_parquet(cfg.daily_output_path)
    raw_columns = [column for _variant, column, _color in DYNAMIC_VARIANTS]
    bounds = scan.select(
        pl.col("DATE").min().alias("start"),
        pl.col("DATE").max().alias("end"),
    ).collect()
    start_date = max(pd.Timestamp(EVALUATION_START_DATE), pd.Timestamp(bounds["start"].item()))
    eligible_dates = pd.to_datetime(
        observations.loc[observations["PUBLIC_RELEASE_ELIGIBLE"].eq(True), "SIGHTING_DATE"],
        errors="coerce",
    )
    end_date = min(pd.Timestamp(bounds["end"].item()), eligible_dates.max().normalize())
    if pd.isna(end_date) or start_date > end_date:
        raise ValueError("Dynamic proxy and eligible sightings have no shared date range.")
    window = scan.filter(pl.col("DATE").is_between(start_date, end_date))
    target_cells = set(
        window.select("H3_INDEX").unique().collect().get_column("H3_INDEX").to_list()
    )
    reports = _dynamic_report_frame(
        observations,
        target_cells=target_cells,
        start_date=start_date,
        end_date=end_date,
    )

    spatial_proxy = (
        window.group_by("H3_INDEX")
        .agg(
            *[pl.col(column).mean().alias(column) for column in raw_columns],
            *[
                pl.col(column).count().alias(f"{column}_AVAILABLE_DAY_COUNT")
                for column in raw_columns
            ],
        )
        .sort("H3_INDEX")
        .collect()
        .to_pandas()
    )
    spatial_reports = reports.groupby("H3_INDEX", as_index=False).agg(
        REPORTED_SIGHTING_COUNT=("OBSERVATION_ID", "nunique"),
        REPORTED_SIGHTING_DAYS=("DATE", "nunique"),
    )
    spatial = spatial_proxy.merge(spatial_reports, on="H3_INDEX", how="left", validate="one_to_one")
    for column in ("REPORTED_SIGHTING_COUNT", "REPORTED_SIGHTING_DAYS"):
        spatial[column] = spatial[column].fillna(0).astype("int64")
    spatial["HAS_REPORTED_SIGHTING"] = spatial["REPORTED_SIGHTING_COUNT"].gt(0)

    temporal_expressions: list[pl.Expr] = []
    for column in raw_columns:
        temporal_expressions.extend(
            [
                pl.when(pl.col(column).count() > 0)
                .then(pl.col(column).sum())
                .otherwise(pl.lit(None, dtype=pl.Float64))
                .alias(column),
                pl.col(column).count().alias(f"{column}_SUPPORTED_CELL_COUNT"),
            ]
        )
    temporal_proxy = (
        window.group_by("DATE").agg(temporal_expressions).sort("DATE").collect().to_pandas()
    )
    temporal_reports = reports.groupby("DATE", as_index=False).agg(
        REPORTED_SIGHTING_COUNT=("OBSERVATION_ID", "nunique")
    )
    temporal = temporal_proxy.merge(temporal_reports, on="DATE", how="left", validate="one_to_one")
    temporal["REPORTED_SIGHTING_COUNT"] = (
        temporal["REPORTED_SIGHTING_COUNT"].fillna(0).astype("int64")
    )
    temporal["HAS_REPORTED_SIGHTING"] = temporal["REPORTED_SIGHTING_COUNT"].gt(0)
    associations = _dynamic_associations(spatial, temporal)

    spatial_path = output_dir / "dynamic_component_spatial_comparison_h3_r6.parquet"
    temporal_path = output_dir / "dynamic_component_temporal_comparison_daily.parquet"
    association_path = output_dir / "dynamic_component_associations.csv"
    atomic_write_parquet(spatial, spatial_path, overwrite=True)
    atomic_write_parquet(temporal, temporal_path, overwrite=True)
    _write_csv(associations, association_path)

    colors = {variant: color for variant, _column, color in DYNAMIC_VARIANTS}
    figure, axes = plt.subplots(1, 2, figsize=(16, 7), sharex=False)
    for axis, grain, response, title in (
        (
            axes[0],
            "spatial_h3_r6",
            "REPORTED_SIGHTING_DAYS",
            "Spatial: mean daily opportunity vs. report days",
        ),
        (
            axes[1],
            "temporal_daily_region",
            "REPORTED_SIGHTING_COUNT",
            "Temporal: regional daily opportunity vs. reports",
        ),
    ):
        values = associations.loc[
            associations["evaluation_grain"].eq(grain)
            & associations["method"].eq("spearman")
            & associations["response"].eq(response)
        ].sort_values("correlation")
        labels = [
            f"{row.proxy_variant} (n={int(row.supported_rows):,})"
            for row in values.itertuples(index=False)
        ]
        axis.barh(
            labels,
            values["correlation"],
            color=[colors[name] for name in values["proxy_variant"]],
        )
        axis.axvline(0.0, color="#333333", linewidth=0.8)
        axis.set_xlabel("Spearman correlation with public-release reports")
        axis.set_title(title)
    figure.suptitle(
        f"Dynamic land-effort components and composites, {start_date.date()} to {end_date.date()}"
    )
    figure.tight_layout()
    plot_path = output_dir / "dynamic_component_associations.png"
    _atomic_save_figure(figure, plot_path)
    artifacts = [spatial_path, temporal_path, association_path, plot_path]
    summary = {
        "start_date": start_date.date().isoformat(),
        "end_date": end_date.date().isoformat(),
        "eligible_report_records_in_target_universe": int(len(reports)),
        "spatial_rows": int(len(spatial)),
        "temporal_rows": int(len(temporal)),
        "zero_semantics": "No eligible report record on the key; not confirmed whale absence.",
    }
    return artifacts, associations, summary


def evaluate(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    overwrite: bool = False,
) -> Path:
    cfg: LandReportingConfig = load_land_reporting_config(config_path)
    population_path = cfg.human.resolve(POPULATION_PATH)
    sightings_path = cfg.human.resolve(SIGHTINGS_PATH)
    output_dir = cfg.human.resolve(EVALUATION_OUTPUT_DIR)
    if not overwrite:
        expected = output_dir / "sightings_vs_land_viewing_pressure_h3_r7.metadata.json"
        if expected.exists():
            raise FileExistsError(f"Evaluation output already exists: {expected}")
    manifest = load_manifest(cfg.manifest_path)
    static = pd.read_parquet(
        cfg.static_weights_path,
        columns=["source_h3", "target_h3", "weight_static_viewability"],
    )
    target = pd.read_parquet(cfg.target_output_path)
    population = pd.read_parquet(
        population_path,
        columns=[
            "H3_INDEX",
            "POPULATION_LOG1P",
            "POPULATION_CONTEXT_AVAILABLE",
        ],
    )
    observations = pd.read_parquet(
        sightings_path,
        columns=[
            "OBSERVATION_ID",
            "SIGHTING_DATE",
            "LATITUDE",
            "LONGITUDE",
            "PUBLIC_RELEASE_ELIGIBLE",
        ],
    )
    population_baseline, population_cap = build_population_baseline(static, population)
    sighting_counts, sighting_summary = prepare_sightings(
        observations,
        set(target["H3_INDEX"].astype(str)),
        start_date=EVALUATION_START_DATE,
    )
    eligible_for_holdouts = observations.loc[
        observations["PUBLIC_RELEASE_ELIGIBLE"].eq(True)
    ].copy()
    eligible_for_holdouts["SIGHTING_DATE"] = pd.to_datetime(
        eligible_for_holdouts["SIGHTING_DATE"], errors="coerce"
    ).dt.normalize()
    eligible_for_holdouts = eligible_for_holdouts.loc[
        eligible_for_holdouts["SIGHTING_DATE"].between(
            pd.Timestamp(sighting_summary["start_date"]),
            pd.Timestamp(sighting_summary["end_date"]),
        )
        & eligible_for_holdouts["LATITUDE"].between(-90.0, 90.0)
        & eligible_for_holdouts["LONGITUDE"].between(-180.0, 180.0)
    ].copy()
    eligible_for_holdouts["H3_INDEX"] = [
        h3.latlng_to_cell(float(lat), float(lon), 7)
        for lat, lon in zip(
            eligible_for_holdouts["LATITUDE"],
            eligible_for_holdouts["LONGITUDE"],
            strict=True,
        )
    ]
    eligible_for_holdouts = eligible_for_holdouts.loc[
        eligible_for_holdouts["H3_INDEX"].isin(set(target["H3_INDEX"].astype(str)))
    ]
    comparison = build_comparison(target, population_baseline, sighting_counts)
    associations = association_table(comparison)
    holdouts = temporal_holdout_table(
        comparison,
        eligible_for_holdouts,
        start_date=sighting_summary["start_date"],
        end_date=sighting_summary["end_date"],
    )
    deciles = decile_table(comparison)
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = output_dir / "sightings_vs_land_viewing_pressure_h3_r7.parquet"
    associations_path = output_dir / "target_pressure_variant_associations.csv"
    holdouts_path = output_dir / "target_pressure_temporal_holdout_associations.csv"
    deciles_path = output_dir / "sightings_vs_land_viewing_pressure_deciles.csv"
    atomic_write_parquet(comparison, comparison_path, overwrite=True)
    _write_csv(associations, associations_path)
    _write_csv(holdouts, holdouts_path)
    _write_csv(deciles, deciles_path)
    plot_path, variants_plot_path = write_figures(
        comparison,
        associations,
        deciles,
        output_dir=output_dir,
        start_date=sighting_summary["start_date"],
        end_date=sighting_summary["end_date"],
    )
    dynamic_artifacts, dynamic_associations, dynamic_summary = evaluate_dynamic_components(
        cfg,
        observations,
        output_dir=output_dir,
    )
    artifacts = [
        comparison_path,
        associations_path,
        holdouts_path,
        deciles_path,
        plot_path,
        variants_plot_path,
        *dynamic_artifacts,
    ]
    metadata_path = output_dir / "sightings_vs_land_viewing_pressure_h3_r7.metadata.json"
    payload = {
        "schema_version": "1.0.0",
        "product": "analysis.land_reporting_opportunity_vs_reported_sightings_h3_r7",
        "status": "retrospective_component_and_composite_association",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "grain": ["H3_INDEX"],
        "h3_resolution": 7,
        "rows": int(len(comparison)),
        "comparison_window": sighting_summary,
        "proxy_manifest": {
            "path": str(cfg.manifest_path),
            "sha256": sha256_file(cfg.manifest_path),
            "config_hash": manifest["config_hash"],
            "build_time_utc": manifest["build_time_utc"],
            "source_completeness": manifest["source_completeness"],
        },
        "inputs": [
            {"path": str(cfg.target_output_path), "sha256": sha256_file(cfg.target_output_path)},
            {"path": str(cfg.static_weights_path), "sha256": sha256_file(cfg.static_weights_path)},
            {"path": str(cfg.daily_output_path), "sha256": sha256_file(cfg.daily_output_path)},
            {"path": str(population_path), "sha256": sha256_file(population_path)},
            {"path": str(sightings_path), "sha256": sha256_file(sightings_path)},
        ],
        "population_baseline": {
            "formula": "viewshed weight times clipped source POPULATION_LOG1P divided by source q99",
            "log1p_cap_quantile": 0.99,
            "log1p_cap": population_cap,
        },
        "primary_variant": PRIMARY_VARIANT,
        "association_summary": associations.to_dict(orient="records"),
        "dynamic_evaluation": dynamic_summary,
        "dynamic_association_summary": dynamic_associations.to_dict(orient="records"),
        "temporal_holdout_association_summary": holdouts.to_dict(orient="records"),
        "artifacts": [{"path": str(path), "sha256": sha256_file(path)} for path in artifacts],
        "zero_semantics": (
            "No public-release-eligible canonical record in the window; not confirmed whale absence."
        ),
        "interpretation": (
            "Associations test whether the proxy tracks where reports occur. They do not estimate "
            "whale preference, whale presence, causal observer effort, or detection probability."
        ),
        "known_limitations": [
            "The proxy manifest is partial and all component and composite formulations remain research-only.",
            "Mapped and verified access variants use native non-null support; their correlations are not directly comparable to broad-coverage variants without considering support differences.",
            "Spatial autocorrelation makes naive correlation magnitudes look more certain than independent samples would justify.",
            "Public sightings reflect whale occurrence, observer access, reporting adoption, and source coverage simultaneously.",
            "The population baseline is an evaluation comparator, not the promoted primary proxy.",
        ],
    }
    atomic_write_json(metadata_path, payload, overwrite=True)
    return metadata_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(evaluate(args.config, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
