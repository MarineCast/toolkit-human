"""Build scalable daily and weekly H3 R6 AIS activity evidence."""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

import h3
import polars as pl
import pyarrow.parquet as pq

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_json,
    load_manifest,
    manifest_payload,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, AisConfig, load_ais_config

REQUIRED_SOURCE_COLUMNS = {
    "MMSI",
    "DATE",
    "HOUR_BIN",
    "H3_CELL",
    "MEAN_SOG",
    "N_PINGS",
    "VesselType",
}
VESSEL_CLASSES = (
    "fishing",
    "towing",
    "recreational",
    "passenger",
    "cargo",
    "tanker",
    "unknown",
)


def _vessel_class_expr() -> pl.Expr:
    code = pl.col("VesselType").cast(pl.Int32, strict=False)
    return (
        pl.when(code == 30)
        .then(pl.lit("fishing"))
        .when(code.is_in([31, 32, 52]))
        .then(pl.lit("towing"))
        .when(code.is_in([36, 37]))
        .then(pl.lit("recreational"))
        .when(code.is_between(60, 69, closed="both"))
        .then(pl.lit("passenger"))
        .when(code.is_between(70, 79, closed="both"))
        .then(pl.lit("cargo"))
        .when(code.is_between(80, 89, closed="both"))
        .then(pl.lit("tanker"))
        .otherwise(pl.lit("unknown"))
        .alias("VESSEL_CLASS")
    )


def _validate_input_schemas(paths: Sequence[Path], *, h3_resolution: int) -> dict[str, object]:
    if not paths:
        raise ValueError("AIS build manifest contains no Parquet inputs.")
    input_rows = 0
    input_bytes = 0
    all_cells: set[str] = set()
    for path in paths:
        parquet = pq.ParquetFile(path)
        missing = sorted(REQUIRED_SOURCE_COLUMNS.difference(parquet.schema_arrow.names))
        if missing:
            raise ValueError(f"AIS source is missing required columns {missing}: {path}")
        input_rows += int(parquet.metadata.num_rows)
        input_bytes += int(path.stat().st_size)
        cells = (
            pl.scan_parquet(path)
            .select(pl.col("H3_CELL").cast(pl.String))
            .unique()
            .collect(engine="streaming")
            .get_column("H3_CELL")
            .to_list()
        )
        all_cells.update(str(cell) for cell in cells)
    invalid = sorted(cell for cell in all_cells if not h3.is_valid_cell(cell))
    if invalid:
        raise ValueError(f"AIS source contains invalid H3 cells; first invalid value: {invalid[0]}")
    if not all_cells:
        raise ValueError("AIS source contains no H3 cells.")
    resolutions = sorted({h3.get_resolution(cell) for cell in all_cells})
    if resolutions != [h3_resolution]:
        raise ValueError(f"AIS source H3 resolutions {resolutions} do not equal {h3_resolution}.")
    centroids = [h3.cell_to_latlng(cell) for cell in all_cells]
    return {
        "input_files": len(paths),
        "input_rows": input_rows,
        "input_bytes": input_bytes,
        "unique_h3_cells": len(all_cells),
        "h3_resolution": h3_resolution,
        "centroid_bounds_wgs84": {
            "minimum_longitude": min(lon for lat, lon in centroids),
            "minimum_latitude": min(lat for lat, lon in centroids),
            "maximum_longitude": max(lon for lat, lon in centroids),
            "maximum_latitude": max(lat for lat, lon in centroids),
        },
    }


def _source_lazy(
    paths: Sequence[Path], cfg: AisConfig, *, validate_required: bool = True
) -> pl.LazyFrame:
    normalized: list[pl.LazyFrame] = []
    for path in paths:
        scan = pl.scan_parquet(path).select(sorted(REQUIRED_SOURCE_COLUMNS))
        schema = scan.collect_schema()
        date_dtype = schema["DATE"]
        hour_dtype = schema["HOUR_BIN"]
        if date_dtype == pl.String:
            date_expr = pl.col("DATE").str.to_datetime(strict=False).dt.date()
        else:
            date_expr = pl.col("DATE").cast(pl.Datetime, strict=False).dt.date()
        if hour_dtype.is_numeric():
            hour_expr = date_expr.cast(pl.Datetime("us")) + pl.duration(
                hours=pl.col("HOUR_BIN").cast(pl.Int64, strict=False)
            )
        elif hour_dtype == pl.String:
            hour_expr = pl.col("HOUR_BIN").str.to_datetime(strict=False)
        else:
            hour_expr = pl.col("HOUR_BIN").cast(pl.Datetime("us"), strict=False)
        normalized.append(
            scan.with_columns(
                # MMSI is an identifier, but retaining its compact integer
                # representation avoids hashing 104 million temporary strings.
                pl.col("MMSI").cast(pl.Int64, strict=False),
                date_expr.alias("DATE"),
                hour_expr.alias("HOUR_BIN"),
                pl.col("H3_CELL").cast(pl.String),
                pl.col("MEAN_SOG").cast(pl.Float64, strict=False),
                pl.col("N_PINGS").cast(pl.UInt64, strict=False).fill_null(0),
                pl.col("VesselType").cast(pl.Float64, strict=False),
                _vessel_class_expr(),
            )
        )
    source = pl.concat(normalized, how="vertical_relaxed")
    if validate_required:
        invalid_required = source.select(
            (pl.col("DATE").is_null() | pl.col("HOUR_BIN").is_null()).sum().alias("invalid_time"),
            pl.col("H3_CELL").is_null().sum().alias("invalid_h3"),
            pl.col("MMSI").is_null().sum().alias("invalid_mmsi"),
        ).collect(engine="streaming")
        invalid = invalid_required.row(0, named=True)
        if any(int(value) for value in invalid.values()):
            raise ValueError(f"AIS required source fields contain null/invalid values: {invalid}")

    valid_sog = (
        pl.col("MEAN_SOG").is_not_null()
        & pl.col("MEAN_SOG").is_finite()
        & (pl.col("MEAN_SOG") >= 0)
        & (pl.col("MEAN_SOG") < cfg.sog_not_available_min_knots)
    )
    return source.with_columns(
        pl.when(valid_sog)
        .then(pl.col("MEAN_SOG") * pl.col("N_PINGS"))
        .otherwise(0.0)
        .alias("VALID_SOG_PING_WEIGHT"),
        pl.when(valid_sog).then(pl.col("N_PINGS")).otherwise(0).alias("VALID_SOG_PING_COUNT"),
        pl.when(valid_sog).then(0).otherwise(pl.col("N_PINGS")).alias("INVALID_SOG_PING_COUNT"),
        (~valid_sog).cast(pl.UInt64).alias("INVALID_SOG_GROUP_COUNT"),
    )


def _aggregate_lazy(
    source: pl.LazyFrame,
    *,
    weekly: bool,
    source_complete: bool,
    observer_classes: Sequence[str],
    time_lower_bound: date | None = None,
    time_upper_bound: date | None = None,
) -> pl.LazyFrame:
    time_key = "WEEK_START" if weekly else "DATE"
    work = source
    if weekly:
        work = work.with_columns(
            pl.col("DATE").cast(pl.Datetime).dt.truncate("1w").dt.date().alias(time_key)
        )
    if time_lower_bound is not None:
        work = work.filter(pl.col(time_key) >= pl.lit(time_lower_bound))
    if time_upper_bound is not None:
        work = work.filter(pl.col(time_key) < pl.lit(time_upper_bound))

    group_keys = [time_key, "H3_CELL"]
    vessel_hour = pl.struct(["MMSI", "HOUR_BIN"])
    aggregations: list[pl.Expr] = [
        pl.col("MMSI").n_unique().alias("UNIQUE_VESSELS"),
        vessel_hour.n_unique().alias("ACTIVE_VESSEL_HOURS_PROXY"),
        pl.col("N_PINGS").sum().alias("PING_COUNT"),
        pl.col("HOUR_BIN").n_unique().alias("OBSERVED_HOURS"),
        pl.col("VALID_SOG_PING_WEIGHT").sum(),
        pl.col("VALID_SOG_PING_COUNT").sum(),
        pl.col("INVALID_SOG_PING_COUNT").sum(),
        pl.col("INVALID_SOG_GROUP_COUNT").sum(),
    ]
    for vessel_class in VESSEL_CLASSES:
        class_filter = pl.col("VESSEL_CLASS") == vessel_class
        prefix = vessel_class.upper()
        aggregations.extend(
            [
                pl.col("MMSI").filter(class_filter).n_unique().alias(f"UNIQUE_{prefix}_VESSELS"),
                vessel_hour.filter(class_filter).n_unique().alias(f"{prefix}_VESSEL_HOURS_PROXY"),
            ]
        )

    grouped = work.group_by(group_keys).agg(aggregations)
    source_hours = work.group_by(time_key).agg(
        pl.col("HOUR_BIN").n_unique().alias("SOURCE_OBSERVED_HOURS")
    )
    expected_hours = 168 if weekly else 24
    observer_hour_columns = [
        f"{vessel_class.upper()}_VESSEL_HOURS_PROXY" for vessel_class in observer_classes
    ]
    return (
        grouped.join(source_hours, on=time_key, how="left")
        .with_columns(
            pl.when(pl.col("VALID_SOG_PING_COUNT") > 0)
            .then(pl.col("VALID_SOG_PING_WEIGHT") / pl.col("VALID_SOG_PING_COUNT"))
            .otherwise(None)
            .alias("MEAN_SOG"),
            pl.sum_horizontal(*observer_hour_columns).alias("OBSERVER_CAPABLE_VESSEL_HOURS_PROXY"),
            pl.when(pl.col("UNIQUE_VESSELS") > 0)
            .then(pl.col("UNIQUE_UNKNOWN_VESSELS") / pl.col("UNIQUE_VESSELS"))
            .otherwise(None)
            .alias("UNKNOWN_VESSEL_TYPE_FRACTION"),
            pl.lit(expected_hours).cast(pl.UInt16).alias("SOURCE_EXPECTED_HOURS"),
            (pl.col("SOURCE_OBSERVED_HOURS") / expected_hours)
            .clip(0.0, 1.0)
            .alias("SOURCE_TEMPORAL_COVERAGE_FRACTION"),
            pl.lit(bool(source_complete)).alias("SOURCE_COVERAGE_COMPLETE"),
            pl.lit("complete" if source_complete else "partial_unknown_acquisition_coverage").alias(
                "SOURCE_COVERAGE_STATUS"
            ),
            pl.lit(MeasurementStatus.OBSERVED.value).alias("MEASUREMENT_STATUS"),
        )
        .drop("VALID_SOG_PING_WEIGHT")
        .rename({"H3_CELL": "H3_INDEX"})
        .with_columns(pl.lit(6).cast(pl.Int8).alias("H3_RESOLUTION"))
        .select(
            time_key,
            "H3_INDEX",
            "H3_RESOLUTION",
            "UNIQUE_VESSELS",
            "ACTIVE_VESSEL_HOURS_PROXY",
            "OBSERVER_CAPABLE_VESSEL_HOURS_PROXY",
            *[
                column
                for vessel_class in VESSEL_CLASSES
                for column in (
                    f"UNIQUE_{vessel_class.upper()}_VESSELS",
                    f"{vessel_class.upper()}_VESSEL_HOURS_PROXY",
                )
            ],
            "MEAN_SOG",
            "VALID_SOG_PING_COUNT",
            "INVALID_SOG_PING_COUNT",
            "INVALID_SOG_GROUP_COUNT",
            "PING_COUNT",
            "OBSERVED_HOURS",
            "SOURCE_OBSERVED_HOURS",
            "SOURCE_EXPECTED_HOURS",
            "SOURCE_TEMPORAL_COVERAGE_FRACTION",
            "SOURCE_COVERAGE_COMPLETE",
            "SOURCE_COVERAGE_STATUS",
            "UNKNOWN_VESSEL_TYPE_FRACTION",
            "MEASUREMENT_STATUS",
        )
        .sort([time_key, "H3_INDEX"])
    )


def _atomic_sink_parquet(frame: pl.LazyFrame, path: Path, *, overwrite: bool = False) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        frame.sink_parquet(
            temporary,
            compression="zstd",
            statistics=True,
            row_group_size=250_000,
            maintain_order=True,
            engine="streaming",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def _date_bounds(path: Path) -> tuple[date, date]:
    dtype = pl.scan_parquet(path).collect_schema()["DATE"]
    if dtype == pl.String:
        values = pl.col("DATE").str.to_datetime(strict=False).dt.date()
    else:
        values = pl.col("DATE").cast(pl.Datetime, strict=False).dt.date()
    result = (
        pl.scan_parquet(path)
        .select(values.min().alias("minimum"), values.max().alias("maximum"))
        .collect(engine="streaming")
        .row(0, named=True)
    )
    minimum = result["minimum"]
    maximum = result["maximum"]
    if not isinstance(minimum, date) or not isinstance(maximum, date):
        raise ValueError(f"AIS input has no valid date bounds: {path}")
    return minimum, maximum


def _build_partitioned_outputs(paths: Sequence[Path], cfg: AisConfig, *, overwrite: bool) -> None:
    bounds = {path: _date_bounds(path) for path in paths}
    source_complete = cfg.source_completeness == "complete"
    with tempfile.TemporaryDirectory(prefix="orcacast-ais-build-") as temporary_dir:
        temporary_root = Path(temporary_dir)
        daily_parts: list[Path] = []
        for index, path in enumerate(sorted(paths, key=lambda value: bounds[value][0])):
            part = temporary_root / f"daily-{index:04d}.parquet"
            frame = _aggregate_lazy(
                _source_lazy([path], cfg, validate_required=False),
                weekly=False,
                source_complete=source_complete,
                observer_classes=cfg.observer_capable_classes,
            )
            frame.sink_parquet(
                part,
                compression="zstd",
                statistics=True,
                row_group_size=250_000,
                maintain_order=True,
                engine="streaming",
            )
            daily_parts.append(part)

        minimum_date = min(value[0] for value in bounds.values())
        maximum_date = max(value[1] for value in bounds.values())
        minimum_week = minimum_date - timedelta(days=minimum_date.weekday())
        maximum_week = maximum_date - timedelta(days=maximum_date.weekday())
        weekly_parts: list[Path] = []
        for week_year in range(minimum_week.year, maximum_week.year + 1):
            lower = minimum_week if week_year == minimum_week.year else date(week_year, 1, 1)
            upper = date(week_year + 1, 1, 1)
            relevant = [
                path
                for path, (path_minimum, path_maximum) in bounds.items()
                if path_maximum >= lower and path_minimum < upper + timedelta(days=7)
            ]
            part = temporary_root / f"weekly-{week_year}.parquet"
            frame = _aggregate_lazy(
                _source_lazy(relevant, cfg, validate_required=False),
                weekly=True,
                source_complete=source_complete,
                observer_classes=cfg.observer_capable_classes,
                time_lower_bound=lower,
                time_upper_bound=upper,
            )
            frame.sink_parquet(
                part,
                compression="zstd",
                statistics=True,
                row_group_size=250_000,
                maintain_order=True,
                engine="streaming",
            )
            weekly_parts.append(part)

        _atomic_sink_parquet(
            pl.concat([pl.scan_parquet(path) for path in daily_parts]),
            cfg.daily_path,
            overwrite=overwrite,
        )
        _atomic_sink_parquet(
            pl.concat([pl.scan_parquet(path) for path in weekly_parts]),
            cfg.weekly_path,
            overwrite=overwrite,
        )


def _source_qc(source: pl.LazyFrame, input_summary: dict[str, object]) -> dict[str, object]:
    summary = (
        source.select(
            pl.len().alias("rows"),
            pl.struct(["MMSI", "HOUR_BIN", "H3_CELL"]).n_unique().alias("unique_keys"),
            pl.col("DATE").min().alias("minimum_date"),
            pl.col("DATE").max().alias("maximum_date"),
            pl.col("DATE").n_unique().alias("observed_dates"),
            pl.col("HOUR_BIN").n_unique().alias("observed_hour_bins"),
            pl.col("MMSI").n_unique().alias("unique_mmsi"),
            pl.col("INVALID_SOG_GROUP_COUNT").sum().alias("invalid_sog_groups"),
            pl.col("INVALID_SOG_PING_COUNT").sum().alias("invalid_sog_pings"),
            pl.col("VesselType").is_null().sum().alias("missing_vessel_type_groups"),
        )
        .collect(engine="streaming")
        .row(0, named=True)
    )
    minimum = summary["minimum_date"]
    maximum = summary["maximum_date"]
    if not isinstance(minimum, date) or not isinstance(maximum, date):
        raise ValueError("AIS source contains no valid date coverage.")
    expected_dates = (maximum - minimum).days + 1
    expected_hours = expected_dates * 24
    return {
        "schema_version": 1,
        "source_contract": "legacy_hourly_h3_r6_partial_research_v1",
        **input_summary,
        **{
            key: value
            for key, value in summary.items()
            if key not in {"minimum_date", "maximum_date"}
        },
        "minimum_date": minimum.isoformat(),
        "maximum_date": maximum.isoformat(),
        "expected_calendar_dates": expected_dates,
        "missing_calendar_dates": expected_dates - int(summary["observed_dates"]),
        "expected_calendar_hours": expected_hours,
        "missing_global_hour_bins": expected_hours - int(summary["observed_hour_bins"]),
        "duplicate_source_keys": int(summary["rows"]) - int(summary["unique_keys"]),
        "speed_qc_rule": "MEAN_SOG values below 0 or at/above the configured not-available threshold do not enter speed means.",
        "absence_semantics": "Missing H3-hours are unknown under partial acquisition coverage, not confirmed zero vessel activity.",
    }


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    cfg = load_ais_config(config_path)
    raw_manifest = load_manifest(cfg.raw_manifest_path)
    if cfg.source_completeness != "complete" and not allow_partial:
        raise ValueError(
            "AIS source coverage is partial; rerun with allow_partial=True for research use."
        )
    paths = tuple(Path(str(item["path"])) for item in raw_manifest["artifacts"])
    input_summary = _validate_input_schemas(paths, h3_resolution=cfg.h3_resolution)
    source = _source_lazy(paths, cfg)
    qc = _source_qc(source, input_summary)
    if int(qc["duplicate_source_keys"]):
        raise ValueError("AIS hourly H3 source keys are not unique.")

    _build_partitioned_outputs(paths, cfg, overwrite=overwrite)
    atomic_write_json(cfg.qc_path, qc, overwrite=overwrite)

    artifacts = [
        artifact_record(cfg.daily_path, dataset_id="human.ais.daily_r6", h3_resolution=6),
        artifact_record(cfg.weekly_path, dataset_id="human.ais.weekly_r6", h3_resolution=6),
        artifact_record(cfg.qc_path, dataset_id="human.ais.qc", h3_resolution=6),
    ]
    payload = manifest_payload(
        config=cfg.human,
        stage="build",
        artifacts=artifacts,
        sources=raw_manifest["sources"],
        inputs=raw_manifest["artifacts"],
        source_completeness=cfg.source_completeness,
        measurement_statuses=[MeasurementStatus.OBSERVED, MeasurementStatus.DERIVED],
        attribution=raw_manifest["attribution"],
        licenses=raw_manifest["licenses"],
        h3_resolution=6,
        spatial_bounds=qc["centroid_bounds_wgs84"],
        temporal={"minimum": qc["minimum_date"], "maximum": qc["maximum_date"]},
        limitations=[
            "Active vessel-hours are unique MMSI-hour proxies at H3 R6, not exact duration.",
            "The hourly archive cannot support vessel-kilometres or track-based acoustic exposure.",
            "Original acquisition endpoint, license, receiver coverage, and jurisdiction completeness are unresolved.",
            "Missing cell-hours remain unknown under partial source coverage and are not filled with zero.",
            "Observer-capable activity includes only the configured vessel classes and is not direct observer effort.",
        ],
    )
    write_manifest(cfg.manifest_path, payload)
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
