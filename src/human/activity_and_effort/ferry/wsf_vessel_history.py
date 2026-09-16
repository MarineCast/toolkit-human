#!/usr/bin/env python3
"""Download and normalize historical WSDOT ferry vessel-history records."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

import pandas as pd
import requests
from dotenv import dotenv_values

API_ROOT = "https://www.wsdot.wa.gov/Ferries/API/Vessels/rest/vesselhistory"
WSDOT_DATE_PATTERN = re.compile(r"/Date\(([-+]?\d+)")
LOCAL_TIMEZONE = "America/Los_Angeles"


def _parse_wsdot_timestamp(value: Any) -> pd.Timestamp:
    match = WSDOT_DATE_PATTERN.match(str(value))
    if not match:
        return pd.NaT
    return (
        pd.to_datetime(int(match.group(1)), unit="ms", utc=True)
        .tz_convert(LOCAL_TIMEZONE)
        .tz_localize(None)
    )


def _fetch_vessel(
    vessel_name: str,
    start_date: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    api_key: str,
    *,
    timeout_seconds: int,
    retries: int,
) -> list[dict[str, Any]]:
    url = (
        f"{API_ROOT}/{quote(vessel_name, safe='')}/"
        f"{start_date:%Y-%m-%d}/{end_exclusive:%Y-%m-%d}"
    )
    error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url,
                params={"apiaccesscode": api_key},
                headers={"Accept": "application/json"},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError(f"Unexpected WSDOT response for {vessel_name}")
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            error = exc
            if attempt < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"WSDOT vessel-history request failed for {vessel_name}: {error}")


def normalize_vessel_history(
    records: Sequence[dict[str, Any]],
    *,
    requested_start: str | pd.Timestamp,
    requested_end: str | pd.Timestamp,
) -> pd.DataFrame:
    start = pd.Timestamp(requested_start).normalize()
    end = pd.Timestamp(requested_end).normalize()
    raw = pd.DataFrame(records)
    columns = [
        "VesselId",
        "Vessel",
        "Departing",
        "Arriving",
        "ScheduledDepart",
        "ActualDepart",
        "EstArrival",
        "Date",
    ]
    for column in columns:
        if column not in raw:
            raw[column] = pd.NA
    result = pd.DataFrame(
        {
            "vessel_id": pd.to_numeric(raw["VesselId"], errors="coerce").astype("Int64"),
            "vessel_name": raw["Vessel"].astype("string"),
            "departing_terminal": raw["Departing"].astype("string"),
            "arriving_terminal": raw["Arriving"].astype("string"),
            "scheduled_departure_local": raw["ScheduledDepart"].map(_parse_wsdot_timestamp),
            "actual_departure_local": raw["ActualDepart"].map(_parse_wsdot_timestamp),
            "estimated_arrival_local": raw["EstArrival"].map(_parse_wsdot_timestamp),
            "history_record_local": raw["Date"].map(_parse_wsdot_timestamp),
        }
    )
    result["service_date"] = result["scheduled_departure_local"].dt.normalize()
    result = result[result["service_date"].between(start, end)].copy()
    result["operational_duration_minutes"] = (
        result["estimated_arrival_local"] - result["actual_departure_local"]
    ).dt.total_seconds() / 60.0
    valid = result["operational_duration_minutes"].between(1.0, 300.0)
    result["operational_duration_is_valid"] = valid.fillna(False)
    result["voyage_duration_source"] = "wsdot_actual_departure_to_estimated_arrival"
    result["retrieved_at_utc"] = datetime.now(timezone.utc)
    result = result.sort_values(
        ["service_date", "vessel_name", "scheduled_departure_local"]
    ).drop_duplicates(
        [
            "service_date",
            "vessel_name",
            "departing_terminal",
            "arriving_terminal",
            "scheduled_departure_local",
        ],
        keep="last",
    )
    return result.reset_index(drop=True)


def download_vessel_history(
    *,
    ridership_path: str | Path,
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
    api_key: str,
    max_workers: int = 6,
    timeout_seconds: int = 30,
    retries: int = 2,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    ridership = pd.read_parquet(ridership_path, columns=["service_date", "vessel_name"])
    ridership["service_date"] = pd.to_datetime(ridership["service_date"]).dt.normalize()
    vessels = sorted(
        ridership.loc[ridership["service_date"].between(start, end), "vessel_name"]
        .dropna()
        .astype(str)
        .unique()
    )
    if not vessels:
        raise ValueError(f"No WSF vessels found between {start.date()} and {end.date()}")
    records: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    end_exclusive = end + pd.Timedelta(days=1)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _fetch_vessel,
                vessel,
                start,
                end_exclusive,
                api_key,
                timeout_seconds=timeout_seconds,
                retries=retries,
            ): vessel
            for vessel in vessels
        }
        for future in as_completed(futures):
            vessel = futures[future]
            vessel_records = future.result()
            counts[vessel] = len(vessel_records)
            records.extend(vessel_records)
    frame = normalize_vessel_history(records, requested_start=start, requested_end=end)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_endpoint": API_ROOT,
        "requested_start_date": start.date().isoformat(),
        "requested_end_date": end.date().isoformat(),
        "vessels_requested": vessels,
        "response_records_by_vessel": counts,
        "normalized_rows": len(frame),
        "valid_operational_duration_rows": int(frame["operational_duration_is_valid"].sum()),
        "duration_definition": "EstArrival minus ActualDepart",
        "duration_caveat": (
            "WSDOT provides estimated arrival, not actual arrival; this is an "
            "operational estimated crossing duration."
        ),
    }
    return frame, metadata


def _atomic_write(frame: pd.DataFrame, metadata: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    frame.to_parquet(temporary, compression="zstd", index=False)
    os.replace(temporary, path)
    metadata_path = path.with_suffix(path.suffix + ".metadata.json")
    metadata["parquet_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata_temp = metadata_path.with_name(f".{metadata_path.name}.{os.getpid()}.part")
    metadata_temp.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    os.replace(metadata_temp, metadata_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ridership", required=True, type=Path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, default=Path("config/.env"))
    parser.add_argument("--max-workers", type=int, default=6)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    api_key = dotenv_values(args.env_file).get("WSDOT_API_KEY") or os.getenv("WSDOT_API_KEY")
    if not api_key:
        raise SystemExit(f"WSDOT_API_KEY is not configured in {args.env_file} or the environment")
    frame, metadata = download_vessel_history(
        ridership_path=args.ridership,
        start_date=args.start_date,
        end_date=args.end_date,
        api_key=str(api_key),
        max_workers=args.max_workers,
    )
    _atomic_write(frame, metadata, args.output)
    print(f"Saved {len(frame):,} WSDOT vessel-history records to {args.output}")
    print(f"Valid operational durations: " f"{int(frame['operational_duration_is_valid'].sum()):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
