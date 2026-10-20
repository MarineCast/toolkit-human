"""Holiday lookup helpers for calendar-effort features."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def _require_holidays():
    try:
        import holidays as holidays_lib
    except ImportError as exc:
        raise ImportError(
            "The `holidays` package is required for calendar holiday feature "
            "generation. Install it in the OrcaCast environment, for example: "
            "`pip install holidays`."
        ) from exc
    return holidays_lib


def normalize_subdivision(value: str | None) -> str | None:
    """Normalize optional subdivision values from config or CLI."""
    if value is None:
        return None
    cleaned = str(value).strip()
    if cleaned.lower() in {"", "none", "null"}:
        return None
    return cleaned


def get_holiday_calendar(country: str, years: list[int], subdiv: str | None = None):
    """Return a holidays calendar object for country US or CA."""
    country_norm = country.upper()
    if country_norm not in {"US", "CA"}:
        raise ValueError("country must be 'US' or 'CA'.")
    holidays_lib = _require_holidays()
    return holidays_lib.country_holidays(
        country_norm,
        years=sorted(set(int(year) for year in years)),
        subdiv=normalize_subdivision(subdiv),
        observed=True,
    )


def _holiday_name(calendar, value: date) -> str:
    if value not in calendar:
        return ""
    if hasattr(calendar, "get_list"):
        names = calendar.get_list(value)
        if isinstance(names, list):
            return "; ".join(str(name) for name in names)
    return str(calendar.get(value, ""))


def build_holiday_lookup(
    dates: pd.Series,
    country: str,
    subdiv: str | None = None,
    prefix: str = "us",
) -> pd.DataFrame:
    """Build holiday flags, names, and distance-to-holiday features for dates."""
    date_series = pd.to_datetime(dates, errors="coerce").dt.normalize()
    if date_series.isna().any():
        raise ValueError("dates contains invalid or null date values.")

    years = sorted(date_series.dt.year.unique().astype(int).tolist())
    calendar = get_holiday_calendar(country, years, subdiv=normalize_subdivision(subdiv))
    holiday_dates = sorted(pd.Timestamp(day).normalize() for day in calendar.keys())
    holiday_set = {day.date() for day in holiday_dates}

    out = pd.DataFrame({"date": date_series.dt.date.astype("string")})
    names = [_holiday_name(calendar, ts.date()) for ts in date_series]
    out[f"is_{prefix}_holiday"] = [bool(ts.date() in holiday_set) for ts in date_series]
    out[f"{prefix}_holiday_name"] = [name if name else "" for name in names]

    if not holiday_dates:
        out[f"days_to_nearest_{prefix}_holiday"] = pd.Series([pd.NA] * len(out), dtype="Int64")
        out[f"is_{prefix}_holiday_window_3d"] = False
        return out

    holiday_ordinals = np.array([day.toordinal() for day in holiday_dates], dtype=np.int64)
    date_ordinals = np.array([ts.toordinal() for ts in date_series], dtype=np.int64)
    insert_idx = np.searchsorted(holiday_ordinals, date_ordinals)
    nearest = np.full(len(date_ordinals), np.iinfo(np.int64).max, dtype=np.int64)
    valid_prev = insert_idx > 0
    nearest[valid_prev] = np.minimum(
        nearest[valid_prev],
        np.abs(date_ordinals[valid_prev] - holiday_ordinals[insert_idx[valid_prev] - 1]),
    )
    valid_next = insert_idx < len(holiday_ordinals)
    nearest[valid_next] = np.minimum(
        nearest[valid_next],
        np.abs(date_ordinals[valid_next] - holiday_ordinals[insert_idx[valid_next]]),
    )
    out[f"days_to_nearest_{prefix}_holiday"] = pd.Series(nearest, dtype="Int64")
    out[f"is_{prefix}_holiday_window_3d"] = (
        out[f"days_to_nearest_{prefix}_holiday"].le(3).fillna(False)
    )
    return out
