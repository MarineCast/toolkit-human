"""Build deterministic daily calendar features from the holiday snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_parquet,
    load_manifest,
    manifest_payload,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, load_calendar_config
from .features import (
    add_calendar_effort_score,
    add_long_weekend_features,
    add_season_features,
    add_weekday_features,
    make_date_frame,
)
from .validation import REQUIRED_CALENDAR_COLUMNS, validate_calendar_features


def _holiday_columns(
    dates: pd.Series,
    snapshot: pd.DataFrame,
    *,
    country: str,
    subdivision: str | None,
    prefix: str,
) -> pd.DataFrame:
    values = snapshot.loc[snapshot["COUNTRY_CODE"].eq(country)].copy()
    if subdivision is not None:
        values = values.loc[values["SUBDIVISION"].fillna("").eq(subdivision)]
    values["DATE"] = pd.to_datetime(values["DATE"]).dt.normalize()
    names = values.groupby("DATE")["HOLIDAY_NAME"].agg("; ".join)
    date_values = pd.to_datetime(dates).dt.normalize()
    holiday_days = np.array(sorted(day.toordinal() for day in names.index), dtype=np.int64)
    ordinals = np.array([day.toordinal() for day in date_values], dtype=np.int64)
    if holiday_days.size:
        distance = np.abs(ordinals[:, None] - holiday_days[None, :]).min(axis=1)
    else:
        distance = np.full(len(ordinals), -1, dtype=np.int64)
    output = pd.DataFrame({"date": date_values.dt.date.astype("string")})
    output[f"{prefix}_holiday_name"] = date_values.map(names).fillna("")
    output[f"is_{prefix}_holiday"] = output[f"{prefix}_holiday_name"].ne("")
    output[f"days_to_nearest_{prefix}_holiday"] = (
        pd.Series(distance).mask(distance < 0).astype("Int64")
    )
    output[f"is_{prefix}_holiday_window_3d"] = (
        output[f"days_to_nearest_{prefix}_holiday"].le(3).fillna(False)
    )
    return output


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    del allow_partial
    cfg = load_calendar_config(config_path)
    raw_manifest = load_manifest(cfg.raw_manifest_path)
    snapshot = pd.read_parquet(cfg.holiday_snapshot)
    frame = make_date_frame(cfg.start_date, cfg.end_date)
    frame = add_weekday_features(frame)
    frame = add_season_features(
        frame,
        peak_months=cfg.peak_months,
        shoulder_months=cfg.shoulder_months,
        summer_months=cfg.summer_months,
    )
    jurisdiction = dict(cfg.jurisdictions)
    us = _holiday_columns(
        frame["date"], snapshot, country="US", subdivision=jurisdiction.get("US"), prefix="us"
    )
    ca = _holiday_columns(
        frame["date"], snapshot, country="CA", subdivision=jurisdiction.get("CA"), prefix="ca"
    )
    frame = frame.merge(us, on="date", how="left").merge(ca, on="date", how="left")
    frame["is_us_or_ca_holiday"] = frame["is_us_holiday"] | frame["is_ca_holiday"]
    frame["is_us_or_ca_holiday_window_3d"] = (
        frame["is_us_holiday_window_3d"] | frame["is_ca_holiday_window_3d"]
    )
    frame = add_long_weekend_features(frame)
    frame = add_calendar_effort_score(frame)
    frame = frame[REQUIRED_CALENDAR_COLUMNS]
    validate_calendar_features(frame)
    atomic_write_parquet(frame, cfg.daily_path, overwrite=overwrite)
    artifact = artifact_record(cfg.daily_path, dataset_id="human.calendar.daily", frame=frame)
    payload = manifest_payload(
        config=cfg.human,
        stage="build",
        artifacts=[artifact],
        inputs=raw_manifest["artifacts"],
        sources=raw_manifest["sources"],
        source_completeness="complete",
        measurement_statuses=[MeasurementStatus.OBSERVED, MeasurementStatus.DERIVED],
        attribution=raw_manifest["attribution"],
        licenses=raw_manifest["licenses"],
        temporal=artifact["temporal_coverage"],
        limitations=[
            "Calendar effort fields are availability proxies, not direct observer effort."
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
