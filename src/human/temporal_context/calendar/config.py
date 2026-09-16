"""Validated configuration for the human calendar pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from human.utils.config import HumanConfig, require_mapping

DEFAULT_CONFIG_PATH = "config/data/human/temporal_context/calendar.yaml"


@dataclass(frozen=True)
class CalendarConfig:
    human: HumanConfig
    start_date: str
    end_date: str
    timezone: str
    jurisdictions: tuple[tuple[str, str | None], ...]
    peak_months: tuple[int, ...]
    shoulder_months: tuple[int, ...]
    summer_months: tuple[int, ...]
    holiday_snapshot: Path
    raw_manifest_path: Path
    daily_path: Path
    manifest_path: Path
    report_path: Path


def load_calendar_config(path: str | Path = DEFAULT_CONFIG_PATH) -> CalendarConfig:
    human = HumanConfig.load(path, product="calendar", category="temporal_context")
    time = require_mapping(human.raw.get("time"), "time")
    start = pd.Timestamp(str(time.get("start_date"))).normalize()
    end = pd.Timestamp(str(time.get("end_date"))).normalize()
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ValueError("calendar time requires a valid start_date <= end_date.")
    jurisdictions_raw = human.raw.get("jurisdictions")
    if not isinstance(jurisdictions_raw, list) or not jurisdictions_raw:
        raise ValueError("calendar jurisdictions must be a non-empty list.")
    jurisdictions: list[tuple[str, str | None]] = []
    for index, value in enumerate(jurisdictions_raw):
        row = require_mapping(value, f"jurisdictions[{index}]")
        country = str(row.get("country", "")).upper()
        if country not in {"US", "CA"}:
            raise ValueError("calendar jurisdictions currently support only US and CA.")
        subdivision = str(row["subdivision"]) if row.get("subdivision") else None
        jurisdictions.append((country, subdivision))
    features = human.section("features")
    return CalendarConfig(
        human=human,
        start_date=start.date().isoformat(),
        end_date=end.date().isoformat(),
        timezone=str(time.get("timezone", "UTC")),
        jurisdictions=tuple(jurisdictions),
        peak_months=tuple(int(value) for value in features.get("peak_months", (6, 7, 8, 9))),
        shoulder_months=tuple(int(value) for value in features.get("shoulder_months", (4, 5, 10))),
        summer_months=tuple(int(value) for value in features.get("summer_months", (6, 7, 8))),
        holiday_snapshot=human.path_value("raw", "holiday_snapshot"),
        raw_manifest_path=human.path_value("raw", "manifest_path"),
        daily_path=human.path_value("output", "daily_path"),
        manifest_path=human.path_value("output", "manifest_path"),
        report_path=human.path_value("inspection", "report_path"),
    )
