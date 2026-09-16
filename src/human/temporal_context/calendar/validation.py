"""Validation and QA summaries for calendar-effort features."""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_CALENDAR_COLUMNS = [
    "date",
    "year",
    "month",
    "day",
    "day_of_year",
    "week_of_year",
    "day_of_week",
    "day_name",
    "is_monday",
    "is_tuesday",
    "is_wednesday",
    "is_thursday",
    "is_friday",
    "is_saturday",
    "is_sunday",
    "is_weekend",
    "is_us_holiday",
    "is_ca_holiday",
    "is_us_or_ca_holiday",
    "us_holiday_name",
    "ca_holiday_name",
    "days_to_nearest_us_holiday",
    "days_to_nearest_ca_holiday",
    "is_us_holiday_window_3d",
    "is_ca_holiday_window_3d",
    "is_us_or_ca_holiday_window_3d",
    "is_long_weekend_us",
    "is_long_weekend_ca",
    "is_long_weekend_us_or_ca",
    "is_summer",
    "is_peak_viewing_season",
    "is_shoulder_viewing_season",
    "calendar_effort_multiplier",
    "calendar_effort_weight",
]

BOOLEAN_COLUMNS = [col for col in REQUIRED_CALENDAR_COLUMNS if col.startswith("is_")]


def _bool_column_is_valid(series: pd.Series) -> bool:
    return bool(series.dropna().map(lambda value: isinstance(value, (bool, np.bool_))).all())


def validate_calendar_features(df: pd.DataFrame, strict: bool = True) -> dict[str, object]:
    """Validate schema, ranges, and derived boolean contracts."""
    missing = [col for col in REQUIRED_CALENDAR_COLUMNS if col not in df.columns]
    errors: list[str] = []
    checks: dict[str, bool] = {}
    null_counts = {
        col: int(df[col].isna().sum()) for col in df.columns if int(df[col].isna().sum()) > 0
    }

    if missing:
        errors.append(f"Missing required columns: {missing}")

    checks["not_empty"] = not df.empty
    if df.empty:
        errors.append("Calendar feature dataframe is empty.")

    duplicate_dates = 0
    start_date = None
    end_date = None
    if "date" in df.columns:
        dates = pd.to_datetime(df["date"], errors="coerce")
        checks["date_no_nulls"] = bool(dates.notna().all())
        if not checks["date_no_nulls"]:
            errors.append("date contains null or invalid values.")
        duplicate_dates = int(dates.duplicated().sum())
        checks["date_unique"] = duplicate_dates == 0
        if duplicate_dates:
            errors.append(f"Duplicate dates found: {duplicate_dates}")
        checks["date_monotonic_increasing"] = bool(dates.is_monotonic_increasing)
        if not checks["date_monotonic_increasing"]:
            errors.append("date is not monotonic increasing.")
        if dates.notna().any():
            start_date = str(dates.min().date())
            end_date = str(dates.max().date())

    if not missing:
        ranges = {
            "year": (1, 9999),
            "month": (1, 12),
            "day": (1, 31),
            "day_of_year": (1, 366),
            "week_of_year": (1, 53),
            "day_of_week": (0, 6),
        }
        for col, (low, high) in ranges.items():
            values = pd.to_numeric(df[col], errors="coerce")
            ok = bool(values.between(low, high).all())
            checks[f"{col}_range"] = ok
            if not ok:
                errors.append(f"{col} contains values outside [{low}, {high}].")

        for col in BOOLEAN_COLUMNS:
            ok = _bool_column_is_valid(df[col])
            checks[f"{col}_boolean"] = ok
            if not ok:
                errors.append(f"{col} must contain bool values, not strings or numbers.")

        multiplier = pd.to_numeric(df["calendar_effort_multiplier"], errors="coerce")
        weight = pd.to_numeric(df["calendar_effort_weight"], errors="coerce")
        checks["calendar_effort_multiplier_min"] = bool(multiplier.ge(1.0).all())
        checks["calendar_effort_weight_range"] = bool(weight.between(0.0, 1.0).all())
        if not checks["calendar_effort_multiplier_min"]:
            errors.append("calendar_effort_multiplier contains values < 1.0.")
        if not checks["calendar_effort_weight_range"]:
            errors.append("calendar_effort_weight contains values outside [0, 1].")

        if all(checks.get(f"{col}_boolean", False) for col in BOOLEAN_COLUMNS):
            contracts = {
                "is_us_or_ca_holiday": df["is_us_holiday"] | df["is_ca_holiday"],
                "is_us_or_ca_holiday_window_3d": (
                    df["is_us_holiday_window_3d"] | df["is_ca_holiday_window_3d"]
                ),
                "is_long_weekend_us_or_ca": df["is_long_weekend_us"] | df["is_long_weekend_ca"],
            }
            for col, expected in contracts.items():
                ok = bool(df[col].equals(expected))
                checks[f"{col}_contract"] = ok
                if not ok:
                    errors.append(f"{col} does not match its OR contract.")

    diagnostics = {
        "n_rows": int(len(df)),
        "start_date": start_date,
        "end_date": end_date,
        "missing_columns": missing,
        "duplicate_dates": duplicate_dates,
        "null_counts": null_counts,
        "checks": checks,
        "errors": errors,
        "is_valid": not errors,
    }
    if errors and strict:
        raise ValueError("Calendar feature validation failed: " + "; ".join(errors))
    return diagnostics


def summarize_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return a one-row summary of calendar-effort features."""
    dates = (
        pd.to_datetime(df["date"], errors="coerce")
        if "date" in df.columns
        else pd.Series(dtype="datetime64[ns]")
    )
    multiplier = pd.to_numeric(
        df.get("calendar_effort_multiplier", pd.Series(dtype=float)), errors="coerce"
    )
    weight = pd.to_numeric(
        df.get("calendar_effort_weight", pd.Series(dtype=float)), errors="coerce"
    )
    return pd.DataFrame(
        [
            {
                "n_dates": int(len(df)),
                "min_date": str(dates.min().date()) if dates.notna().any() else None,
                "max_date": str(dates.max().date()) if dates.notna().any() else None,
                "count_weekends": int(df.get("is_weekend", pd.Series(dtype=bool)).sum()),
                "count_us_holidays": int(df.get("is_us_holiday", pd.Series(dtype=bool)).sum()),
                "count_ca_holidays": int(df.get("is_ca_holiday", pd.Series(dtype=bool)).sum()),
                "count_long_weekends": int(
                    df.get("is_long_weekend_us_or_ca", pd.Series(dtype=bool)).sum()
                ),
                "calendar_effort_multiplier_min": (
                    float(multiplier.min()) if multiplier.notna().any() else None
                ),
                "calendar_effort_multiplier_mean": (
                    float(multiplier.mean()) if multiplier.notna().any() else None
                ),
                "calendar_effort_multiplier_max": (
                    float(multiplier.max()) if multiplier.notna().any() else None
                ),
                "calendar_effort_weight_min": float(weight.min()) if weight.notna().any() else None,
                "calendar_effort_weight_mean": (
                    float(weight.mean()) if weight.notna().any() else None
                ),
                "calendar_effort_weight_max": float(weight.max()) if weight.notna().any() else None,
            }
        ]
    )
