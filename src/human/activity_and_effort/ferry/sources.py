#!/usr/bin/env python3
"""Download Washington or BC Ferries ridership to Parquet.

The pipeline downloads the public Tableau workbook used by WSF's ridership
by-sailing dashboard, extracts its embedded Hyper database, queries a
configurable date range, normalizes route/terminal/time fields, validates the
result, deduplicates repeated Tableau marks, and atomically writes Parquet.

Example
-------
python wsf_ridership_to_parquet.py \
    --start-date 2020-01-01 \
    --end-date 2026-07-21

python wsf_ridership_to_parquet.py \
    --source bc \
    --start-date 2026-01-01 \
    --end-date 2026-06-30 \
    --wsf-reference wsf_ridership.parquet

By default, source snapshots and caches are written beneath the canonical
``data/raw/human/activity_and_effort/ferry`` directory.
Use ``--output /path/to/file.parquet`` to choose another destination.

BC Ferries publishes monthly route totals rather than sailing-level records.
``--source bc`` downloads those official reports and allocates each monthly
total to date/hour buckets using a temporal profile learned from an existing
WSF sailing-level Parquet file. BC rows are estimates and are labeled as such.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import logging
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from human.core.config.paths import project_root

LOGGER = logging.getLogger("wsf-ridership")

DEFAULT_WORKBOOK_URL = (
    "https://public.tableau.com/workbooks/" "RidershipBySailing_Averages.twb?showVizHome=no"
)
DEFAULT_BC_RESOURCES_URL = "https://www.bcferries.com/in-the-community/resources"
PIPELINE_VERSION = "3.0.0-bc-monthly-estimates"
USER_AGENT = f"wsf-ridership-pipeline/{PIPELINE_VERSION} (public-data research pipeline)"
FERRY_UTILS_DIR = Path(__file__).resolve().parent
FERRY_RAW_DIR = project_root() / "data/raw/human/activity_and_effort/ferry"
DEFAULT_WSF_OUTPUT = FERRY_RAW_DIR / "wsf_ridership.parquet"
DEFAULT_BC_OUTPUT = FERRY_RAW_DIR / "bc_ferries_estimated_ridership.parquet"
DEFAULT_WSF_CACHE_DIR = FERRY_RAW_DIR / "wsf_tableau"
DEFAULT_BC_CACHE_DIR = FERRY_RAW_DIR / "bc_ferries_traffic"
DEFAULT_BC_PREVIEW_DIR = DEFAULT_BC_CACHE_DIR / "previews"


@dataclass(frozen=True)
class Config:
    start_date: date
    end_date: date
    output: Path
    cache_dir: Path
    workbook_url: str = DEFAULT_WORKBOOK_URL
    overwrite_cache: bool = False
    duplicate_aggregation: str = "max"
    request_timeout_seconds: int = 180
    minimum_rows: int = 1

    def validate(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.end_date > datetime.now(timezone.utc).date():
            LOGGER.warning(
                "End date %s is in the future; output will stop at available data", self.end_date
            )
        if self.duplicate_aggregation not in {"max", "sum"}:
            raise ValueError("duplicate_aggregation must be 'max' or 'sum'")
        if self.minimum_rows < 1:
            raise ValueError("minimum_rows must be at least 1")


@dataclass(frozen=True)
class BCConfig:
    start_date: date
    end_date: date
    output: Path
    cache_dir: Path
    wsf_reference: Path = DEFAULT_WSF_OUTPUT
    resources_url: str = DEFAULT_BC_RESOURCES_URL
    overwrite_cache: bool = False
    request_timeout_seconds: int = 180
    minimum_rows: int = 1

    def validate(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if not self.wsf_reference.is_file():
            raise FileNotFoundError(
                f"WSF reference Parquet not found: {self.wsf_reference}. "
                "Run this script with --source wsf first, or pass --wsf-reference."
            )
        if self.minimum_rows < 1:
            raise ValueError("minimum_rows must be at least 1")


BC_ROUTE_NAMES = {
    "01": "Tsawwassen-Swartz Bay",
    "02": "Horseshoe Bay-Departure Bay",
    "03": "Horseshoe Bay-Langdale",
    "04": "Swartz Bay-Fulford Harbour",
    "05": "Swartz Bay-Southern Gulf Islands",
    "06": "Crofton-Vesuvius Bay",
    "07": "Earls Cove-Saltery Bay",
    "08": "Horseshoe Bay-Snug Cove",
    "09": "Tsawwassen-Southern Gulf Islands",
    "10": "Port Hardy-Mid Coast-Prince Rupert",
    "11": "Prince Rupert-Skidegate",
    "12": "Brentwood Bay-Mill Bay",
    "13": "Langdale-Gambier/Keats",
    "17": "Powell River-Little River (Comox)",
    "18": "Powell River-Texada Island",
    "19": "Nanaimo Harbour-Gabriola Island",
    "20": "Chemainus-Thetis-Penelakut",
    "21": "Buckley Bay-Denman Island",
    "22": "Denman Island-Hornby Island",
    "23": "Campbell River-Quadra Island",
    "24": "Quadra Island-Cortes Island",
    "25": "Port McNeill-Alert Bay-Sointula",
    "26": "Skidegate-Alliford Bay",
    "28": "Port Hardy-Mid Coast",
    "30": "Tsawwassen-Duke Point",
}


TERMINAL_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("Friday Harbor", (r"\bfriday\s*harbor\b",)),
    ("Bainbridge Island", (r"\bbainbridge(?:\s+island)?\b",)),
    ("Point Defiance", (r"\b(?:point|pt\.?)\s*defiance\b",)),
    ("Port Townsend", (r"\bport\s*townsend\b",)),
    ("Southworth", (r"\bsouthworth\b",)),
    ("Fauntleroy", (r"\bfauntleroy\b",)),
    ("Tahlequah", (r"\btahlequah\b",)),
    ("Mukilteo", (r"\bmukilteo\b",)),
    ("Clinton", (r"\bclinton\b",)),
    ("Edmonds", (r"\bedmonds?\b",)),
    ("Kingston", (r"\bkingston\b",)),
    ("Bremerton", (r"\bbremerton\b",)),
    ("Seattle", (r"\bseattle\b", r"\bcolman\s*dock\b")),
    ("Vashon", (r"\bvashon(?:\s+island)?\b",)),
    ("Coupeville", (r"\bcoupeville\b", r"\bkeystone\b")),
    ("Anacortes", (r"\banacortes\b",)),
    ("Lopez", (r"\blopez(?:\s+island)?\b",)),
    ("Shaw", (r"\bshaw(?:\s+island)?\b",)),
    ("Orcas", (r"\borcas(?:\s+island)?\b",)),
    ("Sidney", (r"\bsidney(?:\s*,?\s*b\.?c\.?)?\b",)),
]
SAN_JUAN_TERMINALS = {"Anacortes", "Lopez", "Shaw", "Orcas", "Friday Harbor", "Sidney"}
TRIANGLE_TERMINALS = {"Fauntleroy", "Vashon", "Southworth"}


def make_session() -> requests.Session:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return session


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(
    session: requests.Session,
    url: str,
    destination: Path,
    *,
    overwrite: bool,
    timeout_seconds: int,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0 and not overwrite:
        LOGGER.info("Using cached download: %s", destination)
        return destination

    temporary = destination.with_suffix(destination.suffix + ".part")
    LOGGER.info("Downloading %s", url)
    with session.get(url, stream=True, timeout=(20, timeout_seconds)) as response:
        response.raise_for_status()
        with temporary.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
    if temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Downloaded workbook is empty")
    temporary.replace(destination)
    return destination


def extract_hyper(workbook_path: Path, hyper_path: Path) -> Path:
    hyper_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = hyper_path.with_suffix(".hyper.part")
    try:
        with zipfile.ZipFile(workbook_path) as workbook:
            members = [name for name in workbook.namelist() if name.lower().endswith(".hyper")]
            if len(members) != 1:
                raise RuntimeError(f"Expected exactly one Hyper extract; found {len(members)}")
            with workbook.open(members[0]) as source, temporary.open("wb") as destination:
                shutil.copyfileobj(source, destination)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            "The downloaded Tableau workbook is not a valid packaged workbook. "
            "The public endpoint may have changed."
        ) from exc
    temporary.replace(hyper_path)
    return hyper_path


def _list_hyper_tables(connection: Any) -> list[str]:
    """List physical Hyper tables for a useful failure diagnostic."""
    rows = connection.execute_list_query("""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
        ORDER BY table_schema, table_name
        """)
    return [f'"{schema}"."{table}"' for schema, table in rows]


def read_hyper(config: Config, hyper_path: Path) -> pd.DataFrame:
    """Run the exact sailing-ridership query used in 07_FERRY_ROUTES.ipynb."""
    try:
        from tableauhyperapi import Connection, HyperProcess, Telemetry
    except ImportError as exc:
        raise RuntimeError(
            "Missing tableauhyperapi. Install with: pip install tableauhyperapi"
        ) from exc

    vehicle = 'COALESCE("Veh Traf per Sailing", "Vehicle Traffic Per Sailing", "Veh on Board")'
    total = (
        'COALESCE("Total Ridership per Sailing", "Total Riders", '
        'CASE WHEN "Vehicle Traffic Per Sailing" IS NOT NULL THEN '
        'COALESCE("Vehicle Traffic Per Sailing", 0) + '
        'COALESCE("Vehicle Passenger Traffic Per Sailing", 0) + COALESCE("Walk-ons", 0) END, '
        'CASE WHEN "Veh Traf per Sailing" IS NOT NULL THEN COALESCE("Veh Traf per Sailing", 0) + '
        'COALESCE("Veh Pax per Sailing", 0) + COALESCE("Walk-ons", 0) END)'
    )
    query = f"""
        SELECT
            CAST("Operating Date" AS TEXT) AS "service_date",
            "Route" AS "route_name",
            "Departure Terminal" AS "origin_terminal",
            "Arrival Terminal" AS "destination_terminal",
            CAST("Scheduled Departure Time" AS TEXT) AS "scheduled_departure",
            CAST(NULL AS TEXT) AS "actual_departure",
            CAST("Actual Vessel" AS TEXT) AS "vessel_name",
            {vehicle} AS "vehicles",
            CASE WHEN {total} IS NOT NULL AND {vehicle} IS NOT NULL
                 THEN GREATEST({total} - {vehicle}, 0)
                 ELSE NULL END AS "passengers",
            {total} AS "reported_total_riders"
        FROM "Extract"."Extract"
        WHERE "Operating Date" BETWEEN DATE '{config.start_date.isoformat()}'
                                   AND DATE '{config.end_date.isoformat()}'
          AND ("Total Riders" IS NOT NULL
               OR "Total Ridership per Sailing" IS NOT NULL
               OR "Vehicle Traffic Per Sailing" IS NOT NULL
               OR "Veh Traf per Sailing" IS NOT NULL)
    """

    with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as process:
        with Connection(process.endpoint, str(hyper_path)) as connection:
            try:
                rows = connection.execute_list_query(query)
            except Exception as exc:
                tables = _list_hyper_tables(connection)
                raise RuntimeError(
                    "The extracted workbook does not contain the notebook table "
                    '\'"Extract"."Extract"\'. Discovered Hyper tables: '
                    f"{tables or ['<none>']}. The default mode performs a fresh download; "
                    "do not use --reuse-workbook unless the cached TWBX is known-good."
                ) from exc

    columns = [
        "service_date",
        "route_name",
        "origin_terminal",
        "destination_terminal",
        "scheduled_departure",
        "actual_departure",
        "vessel_name",
        "vehicles",
        "passengers",
        "reported_total_riders",
    ]
    return pd.DataFrame(rows, columns=columns)


def canonical_terminal(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    low = text.lower().replace("–", "-").replace("—", "-")
    for name, patterns in TERMINAL_PATTERNS:
        if any(re.search(pattern, low, flags=re.IGNORECASE) for pattern in patterns):
            return name
    cleaned = re.sub(r"\s+terminal\b", "", text, flags=re.IGNORECASE).strip()
    return cleaned.title() if cleaned else None


def optional_text(value: Any) -> str | None:
    """Return a non-empty string, converting pandas missing values to None."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def terminals_in_text(value: Any) -> list[str]:
    if value is None or pd.isna(value):
        return []
    text = str(value).lower().replace("–", "-").replace("—", "-")
    found: list[tuple[int, str]] = []
    for name, patterns in TERMINAL_PATTERNS:
        positions = [
            m.start() for pattern in patterns for m in re.finditer(pattern, text, re.IGNORECASE)
        ]
        if positions:
            found.append((min(positions), name))
    return [name for _, name in sorted(found)]


def route_group(origin: str | None, destination: str | None, route_name: Any) -> str | None:
    origin = optional_text(origin)
    destination = optional_text(destination)
    pair = {item for item in (origin, destination) if item}
    label_terminals = set(terminals_in_text(route_name))
    if (pair and pair.issubset(TRIANGLE_TERMINALS)) or len(
        label_terminals & TRIANGLE_TERMINALS
    ) >= 2:
        return "Fauntleroy-Vashon-Southworth"
    if (pair and pair.issubset(SAN_JUAN_TERMINALS)) or len(
        label_terminals & SAN_JUAN_TERMINALS
    ) >= 2:
        return "Anacortes-San Juan Islands"
    if origin and destination:
        return "-".join(sorted((origin, destination)))
    value = str(route_name).strip() if route_name is not None and not pd.isna(route_name) else ""
    return value or None


def parse_clock_seconds(value: Any) -> float:
    if value is None or pd.isna(value):
        return np.nan
    if isinstance(value, (int, float, np.number)):
        numeric = float(value)
        if 0 <= numeric < 1:
            return numeric * 86_400
        if 0 <= numeric <= 172_800:
            return numeric
    text = str(value).strip()
    if not text:
        return np.nan
    match = re.fullmatch(r"(\d{1,3}):(\d{2})(?::(\d{2}(?:\.\d+)?))?", text)
    if match:
        hour, minute, second = int(match.group(1)), int(match.group(2)), float(match.group(3) or 0)
        return hour * 3600 + minute * 60 + second
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.notna(parsed):
        return parsed.hour * 3600 + parsed.minute * 60 + parsed.second
    return np.nan


def normalize(frame: pd.DataFrame, config: Config) -> pd.DataFrame:
    out = frame.copy()
    out["service_date"] = pd.to_datetime(out["service_date"], errors="coerce").dt.normalize()
    for column in ("vehicles", "passengers", "reported_total_riders"):
        out[column] = pd.to_numeric(out[column], errors="coerce").astype("Float64")

    out["origin_terminal"] = out["origin_terminal"].map(canonical_terminal)
    out["destination_terminal"] = out["destination_terminal"].map(canonical_terminal)

    missing_pair = out["origin_terminal"].isna() | out["destination_terminal"].isna()
    for index in out.index[missing_pair]:
        terminals = terminals_in_text(out.at[index, "route_name"])
        if len(terminals) >= 2:
            if pd.isna(out.at[index, "origin_terminal"]):
                out.at[index, "origin_terminal"] = terminals[0]
            if pd.isna(out.at[index, "destination_terminal"]):
                out.at[index, "destination_terminal"] = terminals[-1]

    # Series.map may coerce None to floating-point NaN. Keep the terminal
    # columns as object values so downstream identity construction never
    # mistakes NaN for a valid, truthy terminal name.
    out["origin_terminal"] = out["origin_terminal"].map(optional_text)
    out["destination_terminal"] = out["destination_terminal"].map(optional_text)

    out["route_group"] = [
        route_group(origin, destination, route)
        for origin, destination, route in zip(
            out["origin_terminal"], out["destination_terminal"], out["route_name"]
        )
    ]
    out["segment_id"] = [
        (
            "__".join(sorted((origin, destination)))
            if optional_text(origin) and optional_text(destination)
            else group
        )
        for origin, destination, group in zip(
            out["origin_terminal"], out["destination_terminal"], out["route_group"]
        )
    ]
    out["direction_id"] = [
        (
            f"{origin}__to__{destination}"
            if optional_text(origin) and optional_text(destination)
            else group
        )
        for origin, destination, group in zip(
            out["origin_terminal"], out["destination_terminal"], out["route_group"]
        )
    ]

    out["scheduled_departure_seconds"] = out["scheduled_departure"].map(parse_clock_seconds)
    out["actual_departure_seconds"] = out["actual_departure"].map(parse_clock_seconds)
    out["departure_seconds"] = out["actual_departure_seconds"].fillna(
        out["scheduled_departure_seconds"]
    )
    out["departure_time_source"] = np.where(
        out["actual_departure_seconds"].notna(), "actual", "scheduled"
    )
    out["departure_local"] = out["service_date"] + pd.to_timedelta(
        out["departure_seconds"], unit="s"
    )
    out["calendar_date"] = out["departure_local"].dt.normalize()
    out["departure_hour_local"] = out["departure_local"].dt.hour.astype("Int8")
    out["departure_minute_local"] = out["departure_local"].dt.minute.astype("Int8")

    out = out[
        out["service_date"].between(pd.Timestamp(config.start_date), pd.Timestamp(config.end_date))
        & out["reported_total_riders"].notna()
        & out["route_group"].notna()
        & out["departure_seconds"].notna()
    ].copy()

    out["departure_key_seconds"] = out["departure_seconds"].round().astype("Int64")
    out["sailing_id"] = (
        out["service_date"].dt.strftime("%Y-%m-%d")
        + "__"
        + out["direction_id"].astype("string")
        + "__"
        + out["departure_key_seconds"].astype("string")
    )
    out["source"] = "WSF public Tableau Ridership by Sailing workbook"
    out["retrieved_at_utc"] = pd.Timestamp.now(tz="UTC")
    return out


def deduplicate(frame: pd.DataFrame, method: str) -> pd.DataFrame:
    key = ["service_date", "direction_id", "departure_key_seconds"]
    metrics = ["vehicles", "passengers", "reported_total_riders"]
    aggregation = "sum" if method == "sum" else "max"
    aggregations: dict[str, Any] = {column: aggregation for column in metrics}
    for column in frame.columns:
        if column not in key and column not in metrics:
            aggregations[column] = "first"
    grouped = frame.groupby(key, dropna=False, as_index=False).agg(aggregations)
    counts = frame.groupby(key, dropna=False).size().rename("source_rows_collapsed").reset_index()
    grouped = grouped.merge(counts, on=key, how="left")
    grouped["sailing_id"] = (
        grouped["service_date"].dt.strftime("%Y-%m-%d")
        + "__"
        + grouped["direction_id"].astype("string")
        + "__"
        + grouped["departure_key_seconds"].astype("string")
    )
    return grouped


def validate_output(frame: pd.DataFrame, config: Config) -> None:
    required = {
        "service_date",
        "departure_local",
        "departure_hour_local",
        "sailing_id",
        "route_group",
        "origin_terminal",
        "destination_terminal",
        "reported_total_riders",
    }
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"Output is missing required columns: {sorted(missing)}")
    if len(frame) < config.minimum_rows:
        raise RuntimeError(f"Output has {len(frame)} rows; expected at least {config.minimum_rows}")
    if frame["sailing_id"].duplicated().any():
        examples = frame.loc[frame["sailing_id"].duplicated(False), "sailing_id"].head().tolist()
        raise RuntimeError(f"sailing_id is not unique after deduplication; examples: {examples}")
    if (
        not frame["service_date"]
        .between(pd.Timestamp(config.start_date), pd.Timestamp(config.end_date))
        .all()
    ):
        raise RuntimeError("Output contains dates outside the requested range")
    if (frame["reported_total_riders"] < 0).any():
        raise RuntimeError("Output contains negative reported_total_riders")
    if frame["departure_local"].isna().any():
        raise RuntimeError("Output contains null departure timestamps")


def atomic_write_parquet(frame: pd.DataFrame, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    frame.to_parquet(temporary, index=False, engine="pyarrow", compression="zstd")
    temporary.replace(output)


def write_metadata(config: Config, output: Path, workbook: Path, frame: pd.DataFrame) -> Path:
    metadata_path = output.with_suffix(output.suffix + ".metadata.json")
    metadata = {
        "pipeline_version": PIPELINE_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_start_date": config.start_date.isoformat(),
        "requested_end_date": config.end_date.isoformat(),
        "actual_min_date": frame["service_date"].min().date().isoformat(),
        "actual_max_date": frame["service_date"].max().date().isoformat(),
        "rows": len(frame),
        "unique_sailings": frame["sailing_id"].nunique(),
        "route_groups": int(frame["route_group"].nunique()),
        "workbook_url": config.workbook_url,
        "workbook_sha256": sha256_file(workbook),
        "output_sha256": sha256_file(output),
        "duplicate_aggregation": config.duplicate_aggregation,
        "columns": {column: str(dtype) for column, dtype in frame.dtypes.items()},
    }
    temporary = metadata_path.with_suffix(metadata_path.suffix + ".part")
    temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temporary.replace(metadata_path)
    return metadata_path


class _LinkParser(HTMLParser):
    """Collect links and visible anchor text without adding an HTML dependency."""

    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text)))
            self._href = None
            self._text = []


MONTH_NUMBERS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
MONTH_WORD = "|".join(sorted(MONTH_NUMBERS, key=len, reverse=True))


def _month_from_link_text(text: str) -> pd.Timestamp | None:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    patterns = (
        rf"traffic statistics(?: for)?\s+({MONTH_WORD})\s+(20\d{{2}})",
        rf"({MONTH_WORD})\s+(20\d{{2}})\s+traffic statistics",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return pd.Timestamp(int(match.group(2)), MONTH_NUMBERS[match.group(1).lower()], 1)
    return None


def discover_bc_reports(session: requests.Session, config: BCConfig) -> list[dict[str, Any]]:
    response = session.get(
        config.resources_url,
        timeout=(20, config.request_timeout_seconds),
    )
    response.raise_for_status()
    parser = _LinkParser()
    parser.feed(response.text)
    requested_start = pd.Timestamp(config.start_date).to_period("M")
    requested_end = pd.Timestamp(config.end_date).to_period("M")
    reports: dict[pd.Period, dict[str, Any]] = {}
    for href, label in parser.links:
        report_month = _month_from_link_text(label)
        if report_month is None:
            continue
        period = report_month.to_period("M")
        if requested_start <= period <= requested_end:
            reports[period] = {
                "report_month": report_month,
                "url": urljoin(config.resources_url, href),
                "label": re.sub(r"\s+", " ", label).strip(),
            }
    if not reports:
        raise RuntimeError(
            "No BC Ferries monthly traffic reports were found for the requested months "
            f"on {config.resources_url}"
        )
    return [reports[period] for period in sorted(reports)]


def parse_bc_report(pdf_path: Path, report_url: str) -> pd.DataFrame:
    """Extract current-month route totals from an official BC Ferries PDF."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Missing pypdf. Install with: pip install pypdf") from exc

    reader = PdfReader(pdf_path)
    page_texts = [page.extract_text(extraction_mode="layout") for page in reader.pages]
    full_text = "\n".join(page_texts)
    title_match = re.search(
        r"Total Vehicle and Passenger Counts by Route for\s+([A-Za-z]+)\s+(20\d{2})",
        full_text,
        flags=re.IGNORECASE,
    )
    if not title_match:
        raise RuntimeError(f"Could not identify report month in {pdf_path}")
    month_name, year_text = title_match.groups()
    month_number = MONTH_NUMBERS.get(month_name.lower())
    if month_number is None:
        raise RuntimeError(f"Unrecognized report month {month_name!r} in {pdf_path}")
    report_month = pd.Timestamp(int(year_text), month_number, 1)

    number_pattern = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
    rows: list[dict[str, Any]] = []
    for text in page_texts:
        current_route: str | None = None
        for line in text.splitlines():
            route_match = re.match(r"^\s*(\d{2})\s+[A-Z]", line)
            if route_match:
                current_route = route_match.group(1)
                continue
            if current_route is None or not line.strip() or re.search(r"[A-Za-z]", line):
                continue
            values = number_pattern.findall(line)
            if len(values) not in {7, 14}:
                continue
            numeric = [float(value.replace(",", "")) for value in values]
            vehicles = numeric[0] if len(numeric) == 14 else np.nan
            passengers = numeric[7] if len(numeric) == 14 else numeric[0]
            rows.append(
                {
                    "report_month": report_month,
                    "route_number": current_route,
                    "route_name": BC_ROUTE_NAMES.get(
                        current_route, f"BC Ferries Route {current_route}"
                    ),
                    "published_monthly_vehicles": vehicles,
                    "published_monthly_passengers": passengers,
                    "source_report_url": report_url,
                    "source_report_sha256": sha256_file(pdf_path),
                }
            )
            current_route = None

    result = pd.DataFrame(rows)
    if result.empty:
        raise RuntimeError(f"No route totals could be parsed from {pdf_path}")
    if result["route_number"].duplicated().any():
        duplicates = result.loc[result["route_number"].duplicated(False), "route_number"].tolist()
        raise RuntimeError(f"Duplicate route totals parsed from {pdf_path}: {duplicates}")
    return result


def build_wsf_temporal_profile(reference_path: Path) -> dict[str, Any]:
    """Learn systemwide month x weekday x hour patterns from observed WSF data."""
    columns = ["service_date", "departure_hour_local", "passengers", "vehicles"]
    reference = pd.read_parquet(reference_path, columns=columns)
    reference["service_date"] = pd.to_datetime(
        reference["service_date"], errors="coerce"
    ).dt.normalize()
    reference["departure_hour_local"] = pd.to_numeric(
        reference["departure_hour_local"], errors="coerce"
    )
    reference = reference.dropna(subset=["service_date", "departure_hour_local"])
    reference = reference[reference["departure_hour_local"].between(0, 23)].copy()
    if reference.empty:
        raise RuntimeError(f"WSF reference contains no usable date/hour rows: {reference_path}")

    reference["hour"] = reference["departure_hour_local"].astype(int)
    for metric in ("passengers", "vehicles"):
        reference[metric] = pd.to_numeric(reference[metric], errors="coerce").fillna(0.0)
    hourly = reference.groupby(["service_date", "hour"], as_index=False)[
        ["passengers", "vehicles"]
    ].sum()
    dates = pd.date_range(hourly["service_date"].min(), hourly["service_date"].max(), freq="D")
    complete = pd.MultiIndex.from_product(
        [dates, range(24)], names=["service_date", "hour"]
    ).to_frame(index=False)
    complete = complete.merge(hourly, on=["service_date", "hour"], how="left")
    complete[["passengers", "vehicles"]] = complete[["passengers", "vehicles"]].fillna(0.0)
    complete["month"] = complete["service_date"].dt.month
    complete["day_of_week"] = complete["service_date"].dt.dayofweek

    profile: dict[str, Any] = {
        "reference_min_date": complete["service_date"].min(),
        "reference_max_date": complete["service_date"].max(),
    }
    for metric in ("passengers", "vehicles"):
        profile[metric] = {
            "month_dow_hour": complete.groupby(["month", "day_of_week", "hour"])[metric].mean(),
            "dow_hour": complete.groupby(["day_of_week", "hour"])[metric].mean(),
            "hour": complete.groupby("hour")[metric].mean(),
        }
    return profile


def _month_hour_weights(report_month: pd.Timestamp, profile: dict[str, Any]) -> pd.DataFrame:
    last_day = calendar.monthrange(report_month.year, report_month.month)[1]
    dates = pd.date_range(report_month, report_month.replace(day=last_day), freq="D")
    grid = pd.MultiIndex.from_product(
        [dates, range(24)], names=["service_date", "departure_hour_local"]
    ).to_frame(index=False)
    grid["month"] = report_month.month
    grid["day_of_week"] = grid["service_date"].dt.dayofweek
    for metric in ("passengers", "vehicles"):
        tables = profile[metric]
        keys = pd.MultiIndex.from_frame(grid[["month", "day_of_week", "departure_hour_local"]])
        intensity = tables["month_dow_hour"].reindex(keys).to_numpy(dtype=float)
        missing = ~np.isfinite(intensity)
        if missing.any():
            fallback_keys = pd.MultiIndex.from_frame(
                grid.loc[missing, ["day_of_week", "departure_hour_local"]]
            )
            intensity[missing] = tables["dow_hour"].reindex(fallback_keys).to_numpy(dtype=float)
        missing = ~np.isfinite(intensity)
        if missing.any():
            intensity[missing] = (
                tables["hour"]
                .reindex(grid.loc[missing, "departure_hour_local"])
                .to_numpy(dtype=float)
            )
        intensity = np.nan_to_num(intensity, nan=1.0, posinf=0.0, neginf=0.0)
        if intensity.sum() <= 0:
            intensity[:] = 1.0
        grid[f"{metric[:-1] if metric.endswith('s') else metric}_allocation_weight"] = (
            intensity / intensity.sum()
        )
    return grid.drop(columns=["month", "day_of_week"])


def allocate_bc_monthly_totals(
    monthly: pd.DataFrame, profile: dict[str, Any], retrieved_at: pd.Timestamp
) -> pd.DataFrame:
    chunks: list[pd.DataFrame] = []
    for report_month, month_routes in monthly.groupby("report_month", sort=True):
        weights = _month_hour_weights(pd.Timestamp(report_month), profile)
        for route in month_routes.to_dict("records"):
            chunk = weights.copy()
            for column, value in route.items():
                chunk[column] = value
            chunk["passengers"] = (
                chunk["published_monthly_passengers"] * chunk["passenger_allocation_weight"]
            )
            chunk["vehicles"] = (
                chunk["published_monthly_vehicles"] * chunk["vehicle_allocation_weight"]
            )
            chunk["reported_total_riders"] = chunk["passengers"]
            chunks.append(chunk)
    result = pd.concat(chunks, ignore_index=True)
    result["calendar_date"] = result["service_date"]
    result["departure_minute_local"] = pd.Series(0, index=result.index, dtype="Int8")
    result["departure_hour_local"] = result["departure_hour_local"].astype("Int8")
    result["departure_local"] = result["service_date"] + pd.to_timedelta(
        result["departure_hour_local"].astype(int), unit="h"
    )
    result["departure_time_source"] = "estimated_hour_bucket"
    result["route_group"] = result["route_name"]
    result["segment_id"] = "bc_route_" + result["route_number"]
    result["direction_id"] = pd.NA
    result["origin_terminal"] = pd.NA
    result["destination_terminal"] = pd.NA
    result["vessel_name"] = pd.NA
    result["sailing_id"] = (
        "estimated_bc__"
        + result["service_date"].dt.strftime("%Y-%m-%d")
        + "__route_"
        + result["route_number"]
        + "__hour_"
        + result["departure_hour_local"].astype(str).str.zfill(2)
    )
    result["observation_granularity"] = "estimated_route_hour"
    result["estimation_method"] = "WSF systemwide month-by-weekday-by-hour temporal profile"
    result["is_estimated"] = True
    result["source"] = "BC Ferries monthly Traffic Statistics PDF"
    result["retrieved_at_utc"] = retrieved_at
    return result


def validate_bc_output(frame: pd.DataFrame, monthly: pd.DataFrame, config: BCConfig) -> None:
    if len(frame) < config.minimum_rows:
        raise RuntimeError(f"Output has {len(frame)} rows; expected at least {config.minimum_rows}")
    if frame["sailing_id"].duplicated().any():
        raise RuntimeError("BC estimate IDs are not unique")
    if not frame["is_estimated"].all():
        raise RuntimeError("BC allocated rows must all be explicitly marked as estimated")
    if (frame[["passengers", "vehicles"]].dropna() < 0).any().any():
        raise RuntimeError("BC output contains negative allocated values")

    expected = monthly.set_index(["report_month", "route_number"])
    actual = frame.groupby(["report_month", "route_number"])[["passengers", "vehicles"]].sum(
        min_count=1
    )
    for metric in ("passengers", "vehicles"):
        published = expected[f"published_monthly_{metric}"].astype(float)
        allocated = actual[metric].reindex(expected.index).astype(float)
        mask = published.notna()
        if not np.allclose(allocated[mask], published[mask], rtol=0, atol=1e-6):
            raise RuntimeError(f"Allocated BC {metric} do not conserve published monthly totals")


def write_bc_metadata(
    config: BCConfig,
    output: Path,
    reports: pd.DataFrame,
    frame: pd.DataFrame,
    profile: dict[str, Any],
) -> Path:
    metadata_path = output.with_suffix(output.suffix + ".metadata.json")
    metadata = {
        "pipeline_version": PIPELINE_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_mode": "bc_monthly_temporal_estimate",
        "requested_start_date": config.start_date.isoformat(),
        "requested_end_date": config.end_date.isoformat(),
        "actual_min_date": frame["service_date"].min().date().isoformat(),
        "actual_max_date": frame["service_date"].max().date().isoformat(),
        "rows": len(frame),
        "routes": int(frame["route_number"].nunique()),
        "report_months": sorted(frame["report_month"].dt.strftime("%Y-%m").unique().tolist()),
        "resources_url": config.resources_url,
        "source_reports": reports[["report_month", "source_report_url", "source_report_sha256"]]
        .drop_duplicates()
        .assign(report_month=lambda value: value["report_month"].dt.strftime("%Y-%m"))
        .to_dict("records"),
        "wsf_reference_path": str(config.wsf_reference.resolve()),
        "wsf_reference_sha256": sha256_file(config.wsf_reference),
        "wsf_reference_min_date": profile["reference_min_date"].date().isoformat(),
        "wsf_reference_max_date": profile["reference_max_date"].date().isoformat(),
        "estimation_method": "WSF systemwide month-by-weekday-by-hour temporal profile",
        "monthly_total_conservation_validated": True,
        "output_sha256": sha256_file(output),
        "columns": {column: str(dtype) for column, dtype in frame.dtypes.items()},
    }
    temporary = metadata_path.with_suffix(metadata_path.suffix + ".part")
    temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temporary.replace(metadata_path)
    return metadata_path


def run_bc(config: BCConfig) -> pd.DataFrame:
    config.validate()
    requested_last_day = calendar.monthrange(config.end_date.year, config.end_date.month)[1]
    if config.start_date.day != 1 or config.end_date.day != requested_last_day:
        LOGGER.warning(
            "BC reports contain calendar-month totals; output will include each selected "
            "report's complete month rather than truncate published totals to %s through %s",
            config.start_date,
            config.end_date,
        )
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    session = make_session()
    links = discover_bc_reports(session, config)
    parsed_reports: list[pd.DataFrame] = []
    for report in links:
        report_month = pd.Timestamp(report["report_month"])
        pdf_path = config.cache_dir / f"bc_ferries_{report_month:%Y_%m}.pdf"
        download_file(
            session,
            report["url"],
            pdf_path,
            overwrite=config.overwrite_cache,
            timeout_seconds=config.request_timeout_seconds,
        )
        parsed = parse_bc_report(pdf_path, report["url"])
        if parsed["report_month"].iloc[0].to_period("M") != report_month.to_period("M"):
            raise RuntimeError(
                f"Report link labeled {report_month:%Y-%m} contains "
                f"{parsed['report_month'].iloc[0]:%Y-%m}"
            )
        parsed_reports.append(parsed)
    monthly = pd.concat(parsed_reports, ignore_index=True)
    profile = build_wsf_temporal_profile(config.wsf_reference)
    result = allocate_bc_monthly_totals(monthly, profile, pd.Timestamp.now(tz="UTC"))
    result = result.sort_values(
        ["service_date", "departure_hour_local", "route_number"], kind="stable"
    ).reset_index(drop=True)
    validate_bc_output(result, monthly, config)
    preferred_columns = [
        "service_date",
        "calendar_date",
        "departure_local",
        "departure_hour_local",
        "departure_minute_local",
        "departure_time_source",
        "sailing_id",
        "route_number",
        "route_name",
        "route_group",
        "segment_id",
        "direction_id",
        "origin_terminal",
        "destination_terminal",
        "vessel_name",
        "vehicles",
        "passengers",
        "reported_total_riders",
        "report_month",
        "published_monthly_vehicles",
        "published_monthly_passengers",
        "vehicle_allocation_weight",
        "passenger_allocation_weight",
        "observation_granularity",
        "estimation_method",
        "is_estimated",
        "source",
        "source_report_url",
        "source_report_sha256",
        "retrieved_at_utc",
    ]
    result = result[preferred_columns]
    atomic_write_parquet(result, config.output)
    metadata_path = write_bc_metadata(config, config.output, monthly, result, profile)
    LOGGER.info(
        "Wrote %s estimated BC route-hour rows (%s reports) to %s",
        f"{len(result):,}",
        monthly["report_month"].nunique(),
        config.output,
    )
    LOGGER.info("Wrote provenance metadata to %s", metadata_path)
    return result


def run_wsf(config: Config) -> pd.DataFrame:
    config.validate()
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = config.cache_dir / "RidershipBySailing_Averages.twbx"
    hyper_path = config.cache_dir / "wsf_ridership_by_sailing.hyper"

    session = make_session()
    workbook = download_file(
        session,
        config.workbook_url,
        workbook_path,
        overwrite=config.overwrite_cache,
        timeout_seconds=config.request_timeout_seconds,
    )
    extract_hyper(workbook, hyper_path)
    raw = read_hyper(config, hyper_path)
    LOGGER.info("Queried %s raw sailing records", f"{len(raw):,}")
    normalized = normalize(raw, config)
    result = deduplicate(normalized, config.duplicate_aggregation)
    result = result.sort_values(
        ["service_date", "departure_key_seconds", "direction_id"], kind="stable"
    ).reset_index(drop=True)
    validate_output(result, config)

    preferred_columns = [
        "service_date",
        "calendar_date",
        "departure_local",
        "departure_hour_local",
        "departure_minute_local",
        "scheduled_departure",
        "actual_departure",
        "departure_time_source",
        "sailing_id",
        "route_name",
        "route_group",
        "segment_id",
        "direction_id",
        "origin_terminal",
        "destination_terminal",
        "vessel_name",
        "vehicles",
        "passengers",
        "reported_total_riders",
        "source_rows_collapsed",
        "source",
        "retrieved_at_utc",
    ]
    result = result[[column for column in preferred_columns if column in result.columns]]
    atomic_write_parquet(result, config.output)
    metadata_path = write_metadata(config, config.output, workbook, result)
    LOGGER.info(
        "Wrote %s sailings (%s to %s) to %s",
        f"{len(result):,}",
        result["service_date"].min().date(),
        result["service_date"].max().date(),
        config.output,
    )
    LOGGER.info("Wrote provenance metadata to %s", metadata_path)
    return result


# Backward-compatible programmatic entrypoint.
run = run_wsf


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Date must use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("wsf", "bc"),
        default="wsf",
        help="Download observed WSF sailings or estimated BC route-hour ridership (default: wsf).",
    )
    parser.add_argument("--start-date", type=parse_date, required=True)
    parser.add_argument("--end-date", type=parse_date, default=datetime.now(timezone.utc).date())
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Parquet destination (default depends on --source and is under data/raw).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help=(
            "Download cache (default: data/raw/human/activity_and_effort/ferry/"
            "<wsf_tableau|bc_ferries_traffic>)."
        ),
    )
    parser.add_argument("--workbook-url", default=DEFAULT_WORKBOOK_URL)
    parser.add_argument("--bc-resources-url", default=DEFAULT_BC_RESOURCES_URL)
    parser.add_argument(
        "--wsf-reference",
        type=Path,
        default=DEFAULT_WSF_OUTPUT,
        help=(
            "Observed WSF Parquet used to estimate BC temporal allocation "
            f"(default: {DEFAULT_WSF_OUTPUT})"
        ),
    )
    parser.add_argument(
        "--reuse-downloads",
        "--reuse-workbook",
        dest="reuse_downloads",
        action="store_true",
        help=(
            "Reuse cached WSF workbook or BC report PDFs. The resources index is still "
            "checked for available BC report links."
        ),
    )
    parser.add_argument(
        "--duplicate-aggregation",
        choices=("max", "sum"),
        default="max",
        help="How to combine duplicate Tableau marks. 'max' is the conservative default.",
    )
    parser.add_argument("--minimum-rows", type=int, default=1)
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    LOGGER.info("Starting ferry ridership downloader %s (source=%s)", PIPELINE_VERSION, args.source)
    try:
        if args.source == "wsf":
            config = Config(
                start_date=args.start_date,
                end_date=args.end_date,
                output=args.output or DEFAULT_WSF_OUTPUT,
                cache_dir=args.cache_dir or DEFAULT_WSF_CACHE_DIR,
                workbook_url=args.workbook_url,
                overwrite_cache=not args.reuse_downloads,
                duplicate_aggregation=args.duplicate_aggregation,
                minimum_rows=args.minimum_rows,
            )
            LOGGER.info(
                "Workbook mode: %s",
                "REUSE EXISTING WORKBOOK" if args.reuse_downloads else "FRESH DOWNLOAD",
            )
            run_wsf(config)
        else:
            config = BCConfig(
                start_date=args.start_date,
                end_date=args.end_date,
                output=args.output or DEFAULT_BC_OUTPUT,
                cache_dir=args.cache_dir or DEFAULT_BC_CACHE_DIR,
                wsf_reference=args.wsf_reference,
                resources_url=args.bc_resources_url,
                overwrite_cache=not args.reuse_downloads,
                minimum_rows=args.minimum_rows,
            )
            LOGGER.info(
                "BC report mode: %s",
                "REUSE EXISTING REPORTS" if args.reuse_downloads else "FRESH DOWNLOAD",
            )
            run_bc(config)
    except Exception:
        LOGGER.exception("Ferry ridership pipeline failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
