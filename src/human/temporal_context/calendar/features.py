"""Build daily calendar-effort features for human observer effort."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .holidays import build_holiday_lookup, normalize_subdivision
from .validation import REQUIRED_CALENDAR_COLUMNS, validate_calendar_features

DEFAULT_START_DATE = "2020-01-01"


def get_default_end_date() -> str:
    """Return the current local/system date as an ISO date string."""
    return date.today().isoformat()


def _coerce_date(value: str | date | None, default: str | None = None) -> pd.Timestamp:
    raw = default if value is None else value
    ts = pd.Timestamp(raw).normalize()
    if pd.isna(ts):
        raise ValueError(f"Invalid date value: {value!r}")
    return ts


def make_date_frame(
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> pd.DataFrame:
    """Build an inclusive daily date frame with base calendar columns."""
    start = _coerce_date(start_date, DEFAULT_START_DATE)
    end = _coerce_date(end_date, get_default_end_date())
    if end < start:
        raise ValueError("end_date must be on or after start_date.")

    dates = pd.date_range(start, end, freq="D")
    out = pd.DataFrame({"date": dates})
    iso = out["date"].dt.isocalendar()
    out["year"] = out["date"].dt.year.astype("int64")
    out["month"] = out["date"].dt.month.astype("int64")
    out["day"] = out["date"].dt.day.astype("int64")
    out["day_of_year"] = out["date"].dt.dayofyear.astype("int64")
    out["week_of_year"] = iso.week.astype("int64")
    out["day_of_week"] = out["date"].dt.dayofweek.astype("int64")
    out["day_name"] = out["date"].dt.day_name()
    out["date"] = out["date"].dt.date.astype("string")
    return out


def add_weekday_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add weekday indicator columns and weekend flag."""
    out = df.copy()
    names = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    for idx, name in enumerate(names):
        out[f"is_{name}"] = out["day_of_week"].eq(idx)
    out["is_weekend"] = out["day_of_week"].isin([5, 6])
    return out


def add_season_features(
    df: pd.DataFrame,
    peak_months: tuple[int, ...] = (6, 7, 8, 9),
    shoulder_months: tuple[int, ...] = (4, 5, 10),
    summer_months: tuple[int, ...] = (6, 7, 8),
) -> pd.DataFrame:
    """Add human viewing-season proxy flags."""
    out = df.copy()
    out["is_summer"] = out["month"].isin(summer_months)
    out["is_peak_viewing_season"] = out["month"].isin(peak_months)
    out["is_shoulder_viewing_season"] = out["month"].isin(shoulder_months)
    return out


def add_holiday_features(
    df: pd.DataFrame,
    us_subdiv: str | None = "WA",
    ca_subdiv: str | None = "BC",
) -> pd.DataFrame:
    """Add US and Canada holiday flags and distance-window features."""
    out = df.copy()
    us = build_holiday_lookup(out["date"], "US", normalize_subdivision(us_subdiv), prefix="us")
    ca = build_holiday_lookup(out["date"], "CA", normalize_subdivision(ca_subdiv), prefix="ca")
    out = out.merge(us, on="date", how="left").merge(ca, on="date", how="left")
    out["us_holiday_name"] = out["us_holiday_name"].fillna("")
    out["ca_holiday_name"] = out["ca_holiday_name"].fillna("")
    for col in [
        "is_us_holiday",
        "is_ca_holiday",
        "is_us_holiday_window_3d",
        "is_ca_holiday_window_3d",
    ]:
        out[col] = out[col].fillna(False).astype(bool)
    out["is_us_or_ca_holiday"] = out["is_us_holiday"] | out["is_ca_holiday"]
    out["is_us_or_ca_holiday_window_3d"] = (
        out["is_us_holiday_window_3d"] | out["is_ca_holiday_window_3d"]
    )
    return out


def _long_weekend_dates(df: pd.DataFrame, holiday_col: str) -> set[str]:
    dates = pd.to_datetime(df["date"], errors="coerce")
    holiday_dates = dates[df[holiday_col].astype(bool)]
    marked: set[str] = set()
    for holiday in holiday_dates:
        if int(holiday.dayofweek) not in {0, 4, 5, 6}:
            continue
        friday = holiday - pd.Timedelta(days=(holiday.dayofweek - 4) % 7)
        for offset in range(4):
            marked.add(str((friday + pd.Timedelta(days=offset)).date()))
    valid_dates = set(df["date"].astype(str))
    return marked & valid_dates


def add_long_weekend_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add long-weekend flags around Friday-Monday US and CA holidays."""
    out = df.copy()
    us_dates = _long_weekend_dates(out, "is_us_holiday")
    ca_dates = _long_weekend_dates(out, "is_ca_holiday")
    out["is_long_weekend_us"] = out["date"].astype(str).isin(us_dates)
    out["is_long_weekend_ca"] = out["date"].astype(str).isin(ca_dates)
    out["is_long_weekend_us_or_ca"] = out["is_long_weekend_us"] | out["is_long_weekend_ca"]
    return out


def add_calendar_effort_score(
    df: pd.DataFrame,
    weekend_bonus: float = 0.20,
    holiday_bonus: float = 0.15,
    holiday_window_bonus: float = 0.05,
    long_weekend_bonus: float = 0.10,
    peak_season_bonus: float = 0.10,
    shoulder_season_bonus: float = 0.05,
    max_multiplier: float = 1.60,
) -> pd.DataFrame:
    """Add explicit human calendar-effort multiplier and normalized weight."""
    if max_multiplier < 1.0:
        raise ValueError("max_multiplier must be >= 1.0.")
    out = df.copy()
    multiplier = pd.Series(1.0, index=out.index, dtype="float64")
    multiplier += out["is_weekend"].astype(float) * float(weekend_bonus)
    multiplier += out["is_us_or_ca_holiday"].astype(float) * float(holiday_bonus)
    multiplier += out["is_us_or_ca_holiday_window_3d"].astype(float) * float(holiday_window_bonus)
    multiplier += out["is_long_weekend_us_or_ca"].astype(float) * float(long_weekend_bonus)
    multiplier += out["is_peak_viewing_season"].astype(float) * float(peak_season_bonus)
    multiplier += out["is_shoulder_viewing_season"].astype(float) * float(shoulder_season_bonus)
    out["calendar_effort_multiplier"] = multiplier.clip(1.0, float(max_multiplier))
    out["calendar_effort_weight"] = out["calendar_effort_multiplier"] / float(max_multiplier)
    return out


def build_calendar_features_from_dates(
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    us_subdiv: str | None = "WA",
    ca_subdiv: str | None = "BC",
    peak_months: tuple[int, ...] = (6, 7, 8, 9),
    shoulder_months: tuple[int, ...] = (4, 5, 10),
    summer_months: tuple[int, ...] = (6, 7, 8),
    validate: bool = True,
) -> pd.DataFrame:
    """Build ordered deterministic daily calendar-effort features."""
    df = make_date_frame(start_date=start_date, end_date=end_date)
    df = add_weekday_features(df)
    df = add_season_features(
        df,
        peak_months=peak_months,
        shoulder_months=shoulder_months,
        summer_months=summer_months,
    )
    df = add_holiday_features(df, us_subdiv=us_subdiv, ca_subdiv=ca_subdiv)
    df = add_long_weekend_features(df)
    df = add_calendar_effort_score(df)
    df = df[REQUIRED_CALENDAR_COLUMNS].copy()
    if validate:
        validate_calendar_features(df, strict=True)
    return df


def build_calendar_features(
    output_path: str | Path,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    us_subdiv: str | None = "WA",
    ca_subdiv: str | None = "BC",
    overwrite: bool = False,
    validate: bool = True,
) -> pd.DataFrame:
    """Build and write calendar-effort features to parquet or csv."""
    path = Path(output_path).expanduser()
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {path}")
    df = build_calendar_features_from_dates(
        start_date=start_date,
        end_date=end_date,
        us_subdiv=us_subdiv,
        ca_subdiv=ca_subdiv,
        validate=validate,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        df.to_parquet(path, index=False)
    elif suffix == ".csv":
        df.to_csv(path, index=False)
    else:
        raise ValueError(f"Unsupported output format {suffix!r}; expected .parquet or .csv.")
    return df
