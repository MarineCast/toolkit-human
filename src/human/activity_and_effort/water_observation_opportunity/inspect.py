"""Validate schema-v3 water products and write the integrated HTML diagnostic report."""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
from pathlib import Path
from typing import Any, Sequence

import h3
import numpy as np
import pandas as pd
import polars as pl

from human.activity_and_effort.observation_opportunity_contract import (
    LINEAGE_COLUMNS,
    PRODUCT_CONTRACTS,
    validate_component_table,
    validate_product_contract,
)
from human.utils.artifacts import load_manifest, validate_manifest
from human.viewshed.config.distance import (
    DistanceWeightConfig,
)
from human.viewshed.weights.distance.compute import distance_weight_values

from .config import DEFAULT_CONFIG_PATH, load_water_observation_config
from .pipeline import AIS_TARGET_COLUMNS, FERRY_TARGET_COLUMNS

COMPONENTS = [
    *AIS_TARGET_COLUMNS.values(),
    *FERRY_TARGET_COLUMNS.values(),
    "WATER_WHALE_WATCH_OBSERVATION_OPPORTUNITY_RAW",
]
REPORT_COMPONENTS = [
    "LINE_OF_SIGHT_SUPPORT",
    "PHYSICAL_VIEWABILITY_RAW",
    "DISTANCE_DETECTION_WEIGHT",
    "DISTANCE_ADJUSTED_VIEWABILITY_RAW",
    "ATMOSPHERIC_VISIBILITY_WEIGHT",
    "DAYLIGHT_WEIGHT",
    "WIND_WEIGHT",
    "PRECIPITATION_WEIGHT",
    "SEA_STATE_WEIGHT",
    *COMPONENTS,
    "REPORTING_CAPTURE_WEIGHT",
]


def _table(frame: pd.DataFrame, *, limit: int = 100) -> str:
    return frame.head(limit).to_html(index=False, border=0, classes="data-table", escape=True)


def _json(value: Any) -> str:
    return html.escape(json.dumps(value, indent=2, sort_keys=True, default=str))


def _available_case_dominant_component(frame: pd.DataFrame) -> pd.Series:
    """Return row-wise dominant columns without assigning all-missing rows."""

    result = pd.Series(pd.NA, index=frame.index, dtype="string")
    available = frame.notna().any(axis=1)
    result.loc[available] = frame.loc[available].idxmax(axis=1).astype("string")
    return result


def _map_svg(frame: pd.DataFrame, value: str, *, title: str) -> str:
    work = frame.dropna(subset=[value]).copy()
    if work.empty:
        return f"<p>No mapped values are available for {html.escape(title)}.</p>"
    coordinates = np.asarray([h3.cell_to_latlng(cell) for cell in work["H3_INDEX"]])
    lon = coordinates[:, 1]
    lat = coordinates[:, 0]
    x = 25 + (lon - lon.min()) / max(float(lon.max() - lon.min()), 1e-9) * 650
    y = 25 + (lat.max() - lat) / max(float(lat.max() - lat.min()), 1e-9) * 430
    values = work[value].to_numpy(dtype="float64")
    low, high = np.nanquantile(values, [0.02, 0.98])
    normalized = np.clip((values - low) / max(float(high - low), 1e-12), 0.0, 1.0)
    circles = "".join(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" fill="rgb({int(28+210*v)},{int(70+110*(1-v))},{int(145-90*v)})"><title>{html.escape(str(cell))}: {raw:.4g}</title></circle>'
        for cx, cy, v, raw, cell in zip(x, y, normalized, values, work["H3_INDEX"])
    )
    return (
        f'<svg class="map" viewBox="0 0 700 480" role="img" aria-label="{html.escape(title)}">'
        f'<text x="20" y="18">{html.escape(title)}</text>{circles}</svg>'
    )


def _validate_table(
    path: Path,
    *,
    period_column: str,
    expected_periods: int,
    required_columns: tuple[str, ...],
    expected_lineage: dict[str, Any],
    product_contract: str,
) -> None:
    scan = pl.scan_parquet(path)
    schema = scan.collect_schema().names()
    missing = sorted(
        {period_column, "H3_INDEX", *required_columns, *LINEAGE_COLUMNS}.difference(schema)
    )
    if missing:
        raise ValueError(f"{path.name} is missing contract columns: {missing}")
    state_columns = [
        state
        for _value, state in PRODUCT_CONTRACTS[product_contract].component_state_pairs
        if state in schema
    ]
    checks = (
        scan.select(
            pl.len().alias("rows"),
            pl.struct([period_column, "H3_INDEX"]).n_unique().alias("unique_keys"),
            *[pl.col(column).n_unique().alias(f"lineage__{column}") for column in LINEAGE_COLUMNS],
            *[
                pl.col(column).first().alias(f"lineage_value__{column}")
                for column in LINEAGE_COLUMNS
            ],
            *[
                (
                    pl.col(column).is_null()
                    | ~pl.col(column).is_in(
                        [
                            "positive",
                            "observed_zero",
                            "derived_zero",
                            "unknown",
                            "partial",
                            "unmapped",
                            "source_unavailable",
                            "outside_jurisdiction",
                            "not_applicable",
                            "processing_failure",
                        ]
                    )
                )
                .sum()
                .alias(f"invalid__{column}")
                for column in state_columns
            ],
        )
        .collect(engine="streaming")
        .row(0, named=True)
    )
    if checks["rows"] != checks["unique_keys"]:
        raise ValueError(f"{path.name} has duplicate period/H3 keys.")
    invalid_states = {
        key.removeprefix("invalid__"): value
        for key, value in checks.items()
        if key.startswith("invalid__") and value
    }
    if invalid_states:
        raise ValueError(f"{path.name} has invalid controlled states: {invalid_states}")
    for column in LINEAGE_COLUMNS:
        if checks[f"lineage__{column}"] != 1:
            raise ValueError(f"{path.name} does not have one stable {column} value.")
        actual = checks[f"lineage_value__{column}"]
        if actual != expected_lineage[column]:
            raise ValueError(f"{path.name} {column} does not match generation lineage.")
    period_counts = (
        scan.group_by("H3_INDEX")
        .len()
        .select(pl.col("len").min().alias("minimum"), pl.col("len").max().alias("maximum"))
        .collect(engine="streaming")
        .row(0, named=True)
    )
    if set(period_counts.values()) != {expected_periods}:
        raise ValueError(
            f"{path.name} does not contain {expected_periods} periods for every H3 cell."
        )
    if "PERIOD_DAY_COUNT" in schema:
        available_day_columns = [
            column for column in schema if column.endswith("_AVAILABLE_DAY_COUNT")
        ]
        invalid_available_days = (
            scan.select(
                pl.sum_horizontal(
                    *[
                        (pl.col(column) > pl.col("PERIOD_DAY_COUNT")).cast(pl.UInt32)
                        for column in available_day_columns
                    ]
                )
                .sum()
                .alias("invalid")
            )
            .collect(engine="streaming")
            .item()
        )
        if invalid_available_days:
            raise ValueError(f"{path.name} has available-day counts above period length.")
    validate_product_contract(scan.head(100_000).collect(engine="streaming"), product_contract)


def _validate_unavailable_semantics(path: Path) -> None:
    scan = pl.scan_parquet(path)
    schema = scan.collect_schema().names()
    pairs = {
        "SEA_STATE_WEIGHT": "SEA_STATE_STATE",
        "REPORTING_CAPTURE_WEIGHT": "REPORTING_CAPTURE_STATE",
        "WHALE_WATCH_ACTIVITY_HOURS": "WHALE_WATCH_ACTIVITY_HOURS_STATE",
        "WATER_WHALE_WATCH_OBSERVATION_OPPORTUNITY_RAW": (
            "WATER_WHALE_WATCH_OBSERVATION_OPPORTUNITY_STATE"
        ),
    }
    for value, state in pairs.items():
        if value not in schema:
            continue
        invalid = (
            scan.select(
                (pl.col(value).is_not_null() | pl.col(state).ne("source_unavailable")).sum()
            )
            .collect(engine="streaming")
            .item()
        )
        if invalid:
            raise ValueError(f"{path.name} does not preserve unavailable semantics for {value}.")
    if "PRIMARY_WATER_COMPOSITE" in schema:
        invalid = (
            scan.select(
                (
                    pl.col("PRIMARY_WATER_COMPOSITE").is_not_null()
                    | pl.col("PRIMARY_WATER_COMPOSITE_STATE").ne("not_applicable")
                ).sum()
            )
            .collect(engine="streaming")
            .item()
        )
        if invalid:
            raise ValueError(f"{path.name} unexpectedly defines a primary water composite.")


def inspect(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    output_dir: str | Path | None = None,
    *,
    _candidate_config: Any | None = None,
    _candidate_manifest: dict[str, Any] | None = None,
) -> list[Path]:
    cfg = _candidate_config or load_water_observation_config(config_path)
    manifest = _candidate_manifest or load_manifest(cfg.manifest_path)
    validate_manifest(manifest, verify_artifacts=True)
    metadata = json.loads(cfg.metadata_path.read_text(encoding="utf-8"))
    expected_lineage = metadata["lineage"]
    for path, period_column, expected_periods, required, product_contract in (
        (
            cfg.source_daily_path,
            "DATE",
            1827,
            ("AIS_SOURCE_COVERAGE_COMPLETE",),
            "water_source_daily_r7",
        ),
        (
            cfg.source_weekly_path,
            "WEEK_START",
            262,
            ("PERIOD_DAY_COUNT", "INCOMPLETE_WEEK"),
            "water_source_weekly_r7",
        ),
        (
            cfg.target_daily_path,
            "DATE",
            1827,
            ("DISTANCE_ADJUSTED_VIEWABILITY_RAW", "PRIMARY_WATER_COMPOSITE"),
            "water_target_daily_r6",
        ),
        (
            cfg.target_weekly_path,
            "WEEK_START",
            262,
            (
                "DISTANCE_ADJUSTED_VIEWABILITY_RAW",
                "PERIOD_DAY_COUNT",
                "INCOMPLETE_WEEK",
                "PRIMARY_WATER_COMPOSITE",
            ),
            "water_target_weekly_r6",
        ),
    ):
        _validate_table(
            path,
            period_column=period_column,
            expected_periods=expected_periods,
            required_columns=required,
            expected_lineage=expected_lineage,
            product_contract=product_contract,
        )
        _validate_unavailable_semantics(path)
    daily_scan = pl.scan_parquet(cfg.target_daily_path)
    schema = daily_scan.collect_schema().names()
    state_columns = [
        f"{column.removesuffix('_RAW')}_STATE"
        for column in COMPONENTS
        if f"{column.removesuffix('_RAW')}_STATE" in schema
    ] + ["SEA_STATE_STATE", "REPORTING_CAPTURE_STATE"]
    validation_sample = daily_scan.head(10_000).collect()
    validate_component_table(
        validation_sample,
        keys=["DATE", "H3_INDEX"],
        component_state_columns=state_columns,
    )
    counts = (
        daily_scan.select(
            pl.len().alias("rows"),
            pl.struct(["DATE", "H3_INDEX"]).n_unique().alias("unique_keys"),
            pl.col("DATE").n_unique().alias("days"),
            pl.col("H3_INDEX").n_unique().alias("target_cells"),
        )
        .collect(engine="streaming")
        .row(0, named=True)
    )
    if counts["rows"] != counts["unique_keys"]:
        raise ValueError("Target daily water observation keys are duplicated.")

    component_columns = [column for column in REPORT_COMPONENTS if column in schema]
    distribution = (
        daily_scan.select(
            *[
                expression
                for column in component_columns
                for expression in (
                    pl.col(column).count().alias(f"{column}__available"),
                    pl.col(column).null_count().alias(f"{column}__missing"),
                    pl.col(column).mean().alias(f"{column}__mean"),
                    pl.col(column).quantile(0.5).alias(f"{column}__median"),
                    pl.col(column).quantile(0.95).alias(f"{column}__p95"),
                )
            ]
        )
        .collect(engine="streaming")
        .to_pandas()
        .T.reset_index()
    )
    distribution.columns = ["metric", "value"]

    weekly = pl.read_parquet(cfg.target_weekly_path)
    numeric = [
        column for column in component_columns if weekly[column].null_count() < weekly.height
    ]
    correlations = (
        weekly.select(numeric).to_pandas().corr(min_periods=20).reset_index()
        if numeric
        else pd.DataFrame()
    )
    weekly_pandas = weekly.to_pandas()
    transform_rows: list[dict[str, Any]] = []
    for raw_column in component_columns:
        base = raw_column.removesuffix("_RAW")
        log_column = f"{base}_LOG1P"
        rank_column = f"{base}_SPATIAL_RANK"
        available = [
            name for name in (raw_column, log_column, rank_column) if name in weekly_pandas
        ]
        if len(available) < 2:
            continue
        correlation = weekly_pandas[available].corr(min_periods=20)
        transform_rows.append(
            {
                "component": raw_column,
                "available_rows": int(weekly_pandas[raw_column].notna().sum()),
                "raw_log_correlation": (
                    correlation.loc[raw_column, log_column] if log_column in correlation else np.nan
                ),
                "raw_rank_correlation": (
                    correlation.loc[raw_column, rank_column]
                    if rank_column in correlation
                    else np.nan
                ),
            }
        )
    transform_comparison = pd.DataFrame(transform_rows)

    sensitivity_components = [
        column
        for column in (
            AIS_TARGET_COLUMNS["COMMERCIAL_AIS_ACTIVITY_HOURS_PROXY"],
            AIS_TARGET_COLUMNS["FISHING_AIS_ACTIVITY_HOURS_PROXY"],
            *FERRY_TARGET_COLUMNS.values(),
        )
        if f"{column.removesuffix('_RAW')}_SPATIAL_RANK" in weekly_pandas
    ]
    leave_one_out_rows: list[dict[str, Any]] = []
    dominance_rows: list[dict[str, Any]] = []
    if sensitivity_components:
        rank_columns = {
            component: f"{component.removesuffix('_RAW')}_SPATIAL_RANK"
            for component in sensitivity_components
        }
        full_sensitivity = weekly_pandas[list(rank_columns.values())].mean(axis=1, skipna=True)
        raw_total = weekly_pandas[sensitivity_components].sum(axis=1, min_count=1)
        dominant_component = _available_case_dominant_component(
            weekly_pandas[sensitivity_components]
        )
        dominant_denominator = int(dominant_component.notna().sum())
        for component in sensitivity_components:
            remaining = [rank for name, rank in rank_columns.items() if name != component]
            omitted = weekly_pandas[remaining].mean(axis=1, skipna=True)
            leave_one_out_rows.append(
                {
                    "omitted_component": component,
                    "available_case_correlation_with_full": full_sensitivity.corr(omitted),
                    "full_available_count": int(full_sensitivity.notna().sum()),
                    "omitted_available_count": int(omitted.notna().sum()),
                }
            )
            share = weekly_pandas[component].div(raw_total.where(raw_total > 0.0))
            dominance_rows.append(
                {
                    "component": component,
                    "mean_raw_share": share.mean(),
                    "p95_raw_share": share.quantile(0.95),
                    "dominant_row_rate": (
                        int(dominant_component.eq(component).sum()) / dominant_denominator
                        if dominant_denominator
                        else np.nan
                    ),
                    "dominance_available_case_count": dominant_denominator,
                }
            )
    leave_one_out = pd.DataFrame(leave_one_out_rows)
    dominance = pd.DataFrame(dominance_rows)
    sensitivity_distances = np.arange(0.0, 30.0 + 2.5, 2.5)
    distance_curve_rows: list[dict[str, Any]] = []
    for alternative in cfg.distance_curve_sensitivity:
        parameters = dict(alternative)
        label = str(parameters.pop("label"))
        values = distance_weight_values(
            sensitivity_distances,
            DistanceWeightConfig(**parameters),
            max_distance_km=float(parameters["hard_cutoff_km"]),
        )
        distance_curve_rows.extend(
            {
                "alternative": label,
                "distance_km": float(distance_km),
                "distance_detection_weight": float(weight),
                "status": "uncalibrated_diagnostic_only",
            }
            for distance_km, weight in zip(sensitivity_distances, values, strict=True)
        )
    distance_curve_sensitivity = pd.DataFrame(distance_curve_rows)
    spatial = (
        daily_scan.group_by("H3_INDEX")
        .agg(
            *[pl.col(column).mean().alias(column) for column in component_columns],
            *[
                pl.col(column).is_null().mean().alias(f"{column}__MISSING_FRACTION")
                for column in component_columns
            ],
        )
        .collect(engine="streaming")
        .to_pandas()
    )

    sensitivity_path = Path(
        "data/processed/domain/human/activity_and_effort/observer_effort/"
        "water_ais_reporting_opportunity_weekly_r6.parquet"
    )
    sensitivity = pd.DataFrame(
        [{"status": "legacy compatibility artifact unavailable", "canonical_primary": None}]
    )
    if sensitivity_path.is_file():
        legacy = (
            pl.scan_parquet(sensitivity_path)
            .select("WEEK_START", "H3_INDEX", "WATER_AIS_REPORTING_OPPORTUNITY_RAW")
            .with_columns(pl.col("WEEK_START").cast(pl.Datetime("ns")))
        )
        candidates = [
            AIS_TARGET_COLUMNS["PASSENGER_AIS_ACTIVITY_HOURS_PROXY"],
            AIS_TARGET_COLUMNS["RECREATIONAL_AIS_ACTIVITY_HOURS_PROXY"],
        ]
        joined = (
            weekly.lazy()
            .select("WEEK_START", "H3_INDEX", *candidates)
            .join(legacy, on=["WEEK_START", "H3_INDEX"], how="inner")
            .with_columns(
                pl.sum_horizontal(*[pl.col(column) for column in candidates]).alias(
                    "DYNAMIC_PASSENGER_PLUS_RECREATIONAL_SENSITIVITY"
                )
            )
            .collect(engine="streaming")
            .to_pandas()
        )
        sensitivity = (
            joined[
                [
                    "WATER_AIS_REPORTING_OPPORTUNITY_RAW",
                    "DYNAMIC_PASSENGER_PLUS_RECREATIONAL_SENSITIVITY",
                ]
            ]
            .corr(min_periods=20)
            .reset_index()
        )

    seasonal = (
        daily_scan.with_columns(
            pl.col("DATE").dt.year().alias("YEAR"),
            pl.col("DATE").dt.month().alias("MONTH"),
        )
        .group_by("YEAR", "MONTH")
        .agg(*[pl.col(column).mean().alias(column) for column in component_columns])
        .sort("YEAR", "MONTH")
        .collect(engine="streaming")
        .to_pandas()
    )
    annual = (
        daily_scan.with_columns(pl.col("DATE").dt.year().alias("YEAR"))
        .group_by("YEAR")
        .agg(*[pl.col(column).mean().alias(column) for column in component_columns])
        .sort("YEAR")
        .collect(engine="streaming")
        .to_pandas()
    )
    source_coverage = (
        pl.scan_parquet(cfg.source_daily_path)
        .group_by("DATE")
        .agg(
            pl.col("AIS_SOURCE_TEMPORAL_COVERAGE_FRACTION").max(),
            pl.col("AIS_SOURCE_COVERAGE_COMPLETE").all(),
            pl.col("AIS_SOURCE_COVERAGE_STATUS").first(),
        )
        .sort("DATE")
        .collect(engine="streaming")
        .to_pandas()
    )
    border = pd.read_csv(cfg.border_summary_path)
    sightings = pd.read_csv(cfg.sighting_summary_path)
    sighting_detail = pl.scan_parquet(cfg.sighting_detail_path)
    sighting_schema = sighting_detail.collect_schema().names()
    percentile_columns = [name for name in sighting_schema if name.endswith("_SPATIAL_RANK")]
    sighting_percentiles = (
        sighting_detail.select(
            *[
                pl.col(column).median().alias(f"{column}__median_at_positive_sightings")
                for column in percentile_columns
            ]
        )
        .collect(engine="streaming")
        .to_pandas()
        .T.reset_index()
    )
    sighting_percentiles.columns = ["metric", "value"]
    contradiction_flags = [
        name
        for name in sighting_schema
        if name.endswith("_LOWEST_01PCT_FLAG") or name.endswith("_ZERO_FLAG")
    ]
    contradiction_clusters = (
        sighting_detail.group_by("H3_INDEX")
        .agg(
            pl.len().alias("SIGHTING_COUNT"),
            pl.sum_horizontal(*[pl.col(name).cast(pl.Int64) for name in contradiction_flags])
            .sum()
            .alias("CONTRADICTION_COUNT"),
        )
        .sort("CONTRADICTION_COUNT", descending=True)
        .collect(engine="streaming")
        .to_pandas()
        if contradiction_flags
        else pd.DataFrame(columns=["H3_INDEX", "SIGHTING_COUNT", "CONTRADICTION_COUNT"])
    )

    report = Path(output_dir) / cfg.report_path.name if output_dir else cfg.report_path
    report.parent.mkdir(parents=True, exist_ok=True)
    component_maps = "".join(
        _map_svg(spatial, column, title=column)
        for column in component_columns
        if spatial[column].notna().any()
    )
    missingness_maps = "".join(
        _map_svg(
            spatial,
            f"{column}__MISSING_FRACTION",
            title=f"{column} missing fraction",
        )
        for column in component_columns
    )
    contradiction_map = _map_svg(
        contradiction_clusters,
        "CONTRADICTION_COUNT",
        title="Positive-sighting contradiction count by H3 R6",
    )
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OrcaCast observation opportunity diagnostics</title>
<style>
:root{{--ink:#17242b;--muted:#64747b;--paper:#f7f5ef;--card:#fff;--sea:#0b7285;--line:#d8e0df}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:1240px;margin:auto;padding:32px}} h1{{font-size:2.2rem;margin-bottom:.2rem}} h2{{margin-top:2.2rem;border-bottom:1px solid var(--line);padding-bottom:.35rem}} h3{{margin-top:1.4rem}}
.lede{{color:var(--muted);max-width:78ch}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:20px 0}} .card{{background:var(--card);padding:16px;border:1px solid var(--line);border-radius:10px}} .value{{font-size:1.6rem;font-weight:700;color:var(--sea)}}
.scroll{{overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px}} table{{border-collapse:collapse;width:100%;font-size:12px}} th,td{{padding:7px;border-bottom:1px solid #e8eceb;text-align:left;white-space:nowrap}} th{{position:sticky;top:0;background:#eef5f4}} .map{{width:100%;max-width:700px;background:#edf6f7;border:1px solid var(--line);border-radius:10px}} pre{{white-space:pre-wrap;background:#13272c;color:#d8f3f0;padding:14px;border-radius:10px;font-size:12px}} code{{font-family:ui-monospace,monospace}}
.notice{{border-left:4px solid #d97706;background:#fff7ed;padding:12px 16px}} @media(max-width:700px){{main{{padding:18px}}}}
</style></head><body><main>
<h1>Observation / reporting opportunity diagnostics</h1>
<p class="lede">Schema v3 research products. Physical viewability, observer/platform activity, viewing conditions, and reporting capture remain separate. No value in this report is a detection probability, and no component is model eligible.</p>
<div class="notice"><strong>No canonical water composite.</strong> Passenger-plus-recreational AIS remains a deprecated compatibility/sensitivity interface. Whale-watch, sea state, and reporting capture are explicitly unavailable.</div>
<div class="cards"><div class="card"><div class="value">{counts['rows']:,}</div>daily target rows</div><div class="card"><div class="value">{counts['days']:,}</div>days</div><div class="card"><div class="value">{counts['target_cells']:,}</div>H3 R6 targets</div><div class="card"><div class="value">{html.escape(metadata['status'])}</div>promotion status</div></div>
<h2>Lineage and source coverage</h2><pre>{_json(metadata['lineage'])}</pre>{_table(source_coverage.describe(include='all').reset_index())}
<h2>Component distributions and correlations</h2><div class="scroll">{_table(distribution, limit=500)}</div><h3>Available-case correlation matrix</h3><div class="scroll">{_table(correlations, limit=100)}</div><h3>Raw, log1p, and spatial-rank comparison</h3><div class="scroll">{_table(transform_comparison, limit=100)}</div>
<h2>Composite sensitivity</h2><p>The comparisons below are diagnostic only and do not promote a composite. The leave-one-out surface uses only configured exogenous ferry, commercial, and fishing components.</p><div class="scroll">{_table(sensitivity)}</div><h3>Leave-one-component-out</h3><div class="scroll">{_table(leave_one_out, limit=100)}</div><h3>Dominance statistics</h3><div class="scroll">{_table(dominance, limit=100)}</div><h3>Uncalibrated distance-curve alternatives</h3><p>These alternatives are declared in configuration and are not selected or tuned using whale sightings.</p><div class="scroll">{_table(distance_curve_sensitivity, limit=500)}</div>
<h2>Component maps</h2>{component_maps}
<h2>Availability and missingness maps</h2>{missingness_maps}
<h2>WA / BC discontinuity diagnostics</h2><p>Nearest cross-jurisdiction coastal-source matches are reported at configured 15/30/50 km bands. No correction is applied.</p><div class="scroll">{_table(border, limit=500)}</div>
<h2>Seasonal and annual summaries</h2><h3>Monthly by year</h3><div class="scroll">{_table(seasonal, limit=200)}</div><h3>Annual</h3><div class="scroll">{_table(annual, limit=100)}</div>
<h2>Positive-sighting audit</h2><p>All checksum-bound canonical positives are retained internally; public eligibility is only a reporting stratum. Platform is classified from explicit structured fields, otherwise <code>unknown</code>. Unknown-platform records keep both land and water evaluations.</p><div class="scroll">{_table(sighting_percentiles, limit=500)}</div><h3>Contradiction clusters</h3>{contradiction_map}<div class="scroll">{_table(contradiction_clusters, limit=300)}</div><h3>Grouped summaries</h3><div class="scroll">{_table(sightings, limit=300)}</div>
<h2>Manifest contract</h2><pre>{_json({'generation_id': manifest.get('generation_id'), 'schema_version': manifest.get('schema_version'), 'model_eligible': manifest.get('model_eligible'), 'known_limitations': manifest.get('known_limitations')})}</pre>
</main></body></html>"""
    temporary = report.with_name(f".{report.name}.{os.getpid()}.part")
    if (
        _candidate_manifest is None
        and output_dir is None
        and cfg.report_artifact_path.is_file()
        and cfg.report_artifact_path != report
    ):
        shutil.copyfile(cfg.report_artifact_path, temporary)
    else:
        temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, report)
    rendered = report.read_text(encoding="utf-8")
    required_sections = (
        "Component distributions and correlations",
        "Composite sensitivity",
        "Component maps",
        "Availability and missingness maps",
        "WA / BC discontinuity diagnostics",
        "Seasonal and annual summaries",
        "Positive-sighting audit",
        "Manifest contract",
    )
    if report.stat().st_size < 10_000 or any(
        section not in rendered for section in required_sections
    ):
        raise ValueError("Observation-opportunity HTML report validation failed.")
    return [report]


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
