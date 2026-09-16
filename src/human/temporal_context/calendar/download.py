"""Materialize a versioned US/Canada holiday source snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pandas as pd

from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_parquet,
    manifest_payload,
    source_record,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, load_calendar_config
from .holidays import _require_holidays, get_holiday_calendar


def download(config_path: str | Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    cfg = load_calendar_config(config_path)
    years = range(pd.Timestamp(cfg.start_date).year - 1, pd.Timestamp(cfg.end_date).year + 2)
    rows: list[dict[str, object]] = []
    holidays_lib = _require_holidays()
    for country, subdivision in cfg.jurisdictions:
        calendar = get_holiday_calendar(country, list(years), subdivision)
        for day in sorted(calendar):
            names = calendar.get_list(day) if hasattr(calendar, "get_list") else [calendar[day]]
            rows.append(
                {
                    "DATE": pd.Timestamp(day).date().isoformat(),
                    "COUNTRY_CODE": country,
                    "SUBDIVISION": subdivision,
                    "HOLIDAY_NAME": "; ".join(str(name) for name in names),
                    "SOURCE_VERSION": str(getattr(holidays_lib, "__version__", "unknown")),
                    "MEASUREMENT_STATUS": MeasurementStatus.OBSERVED.value,
                }
            )
    frame = pd.DataFrame(rows).sort_values(["DATE", "COUNTRY_CODE", "SUBDIVISION"])
    atomic_write_parquet(frame, cfg.holiday_snapshot, overwrite=overwrite)
    source_cfg = cfg.human.section("sources")
    source = source_record(
        cfg.holiday_snapshot,
        name="regional_holiday_snapshot",
        provider=str(source_cfg.get("provider", "python-holidays")),
        license_name=str(source_cfg.get("license", "MIT")),
        attribution=str(source_cfg.get("attribution", "python-holidays contributors")),
        measurement_status=MeasurementStatus.OBSERVED,
        temporal={"minimum": frame["DATE"].min(), "maximum": frame["DATE"].max()},
    )
    payload = manifest_payload(
        config=cfg.human,
        stage="download",
        artifacts=[
            artifact_record(cfg.holiday_snapshot, dataset_id="human.calendar.holidays", frame=frame)
        ],
        sources=[source],
        source_completeness="complete",
        measurement_statuses=[MeasurementStatus.OBSERVED],
        attribution=[source["attribution"]],
        licenses=[source["license"]],
        temporal={"minimum": cfg.start_date, "maximum": cfg.end_date},
    )
    write_manifest(cfg.raw_manifest_path, payload)
    return cfg.holiday_snapshot


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(download(args.config, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
