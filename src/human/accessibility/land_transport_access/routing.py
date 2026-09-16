"""Acquire dated OSM extracts and build an immutable native road-only graph."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from human.utils.artifacts import atomic_write_json, sha256_file

OSRM_ROOT = Path("/opt/homebrew/Cellar/osrm-backend/26.8.0_1")
OSMIUM = Path("/opt/homebrew/Cellar/osmium-tool/1.19.1_1/bin/osmium")
REGIONS = ("canada/british-columbia", "canada/alberta", "us/washington", "us/oregon", "us/idaho")


def acquire(directory: Path, *, date: str = "260825") -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    records, paths = [], []
    for region in REGIONS:
        name = region.split("/")[-1] + f"-{date}.osm.pbf"
        path = directory / name
        url = f"https://download.geofabrik.de/north-america/{region.rsplit('/', 1)[0]}/{name}"
        record_path = path.with_suffix(".source.json")
        if path.exists():
            if not record_path.exists() or json.loads(record_path.read_text())[
                "sha256"
            ] != sha256_file(path):
                raise ValueError(f"Unverified pre-existing routing extract: {path}")
        else:
            print(f"Downloading {url}", flush=True)
            temporary = path.with_suffix(".download")
            with requests.get(url, stream=True, timeout=(30, 120)) as response:
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        handle.write(chunk)
            subprocess.run(
                [str(OSMIUM), "fileinfo", str(temporary), "--input-format=pbf"], check=True
            )
            temporary.replace(path)
            atomic_write_json(
                record_path,
                {
                    "url": url,
                    "date": date,
                    "sha256": sha256_file(path),
                    "license": "ODbL-1.0",
                    "attribution": "OpenStreetMap contributors; Geofabrik",
                },
            )
        records.append(json.loads(record_path.read_text()))
        paths.append(path)
    atomic_write_json(directory / "extracts.json", records, overwrite=True)
    return paths


def build_graph(extracts: list[Path], directory: Path, *, threads: int = 4) -> Path:
    timestamps = {
        subprocess.check_output(
            [str(OSMIUM), "fileinfo", "-g", "header.option.timestamp", str(path)], text=True
        ).strip()
        for path in extracts
    }
    if len(timestamps) != 1 or not next(iter(timestamps)):
        raise ValueError("Road graph extracts must share a declared OSM data timestamp")
    if directory.exists():
        raise FileExistsError(f"Graph builds are immutable: {directory}")
    directory.mkdir(parents=True)
    version = subprocess.check_output(
        [str(OSRM_ROOT / "bin/osrm-extract"), "--version"], text=True
    ).strip()
    if "26.8.0" not in version:
        raise ValueError(f"Unexpected OSRM runtime: {version}")
    profiles = directory / "profiles"
    shutil.copytree(OSRM_ROOT / "share/osrm/profiles", profiles)
    profile = profiles / "road_only.lua"
    shutil.copy2(Path(__file__).with_name("road_only.lua"), profile)
    merged = directory / "roads.osm.pbf"
    subprocess.run([str(OSMIUM), "merge", *map(str, extracts), "-o", str(merged)], check=True)
    graph = directory / "roads.osrm"
    for binary, args in (
        ("osrm-extract", ["-p", str(profile), str(merged)]),
        ("osrm-partition", [str(graph)]),
        ("osrm-customize", [str(graph)]),
    ):
        with (directory / f"{binary}.log").open("w") as log:
            subprocess.run(
                [str(OSRM_ROOT / "bin" / binary), "--threads", str(threads), *args],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        print(f"Completed {binary}", flush=True)
    payload = {
        "osm_data_timestamp": next(iter(timestamps)),
        "routing_policy": "road_only_no_ferry_no_shuttle_train",
        "engine": version,
        "engine_sha256": sha256_file(OSRM_ROOT / "bin/osrm-routed"),
        "profile_sha256": sha256_file(profile),
        "profiles": {str(p.relative_to(profiles)): sha256_file(p) for p in profiles.rglob("*.lua")},
        "inputs": [{"path": str(p.resolve()), "sha256": sha256_file(p)} for p in extracts],
        "graph_files": {p.name: sha256_file(p) for p in directory.glob("roads.osrm*")},
        "graph_path": str(graph.resolve()),
        "snap_policy": "same_land_polygon_required",
        "validation_status": "built_requires_connectivity_and_snap_acceptance",
    }
    atomic_write_json(directory / "manifest.json", payload)
    return graph


def classify_pair(
    duration_seconds: float | None,
    *,
    request_ok: bool,
    covered: bool,
    snap_valid: bool,
    disconnected_proven: bool = False,
) -> str:
    if not request_ok or not covered or not snap_valid:
        return "routing_unavailable"
    if duration_seconds is not None and np.isfinite(duration_seconds) and duration_seconds >= 0:
        return "reachable"
    # A null OSRM table alone is not proof of graph/land-component disconnection.
    return "known_disconnected" if disconnected_proven else "routing_unavailable"


def population_demand(
    pairs: pd.DataFrame, *, decay_minutes: tuple[int, ...] = (60, 120, 240)
) -> pd.DataFrame:
    required = {"source_h3", "origin_h3", "population", "duration_minutes", "routing_status"}
    if not required.issubset(pairs) or pairs.duplicated(["source_h3", "origin_h3"]).any():
        raise ValueError("OD pairs require unique source/origin keys and routing evidence")
    if not pairs.routing_status.isin(
        {"reachable", "known_disconnected", "routing_unavailable"}
    ).all():
        raise ValueError("Unknown routing status")
    if (
        pairs.population.isna().any()
        or not np.isfinite(pairs.population).all()
        or pairs.population.lt(0).any()
    ):
        raise ValueError("Invalid origin population")
    origin_sets = pairs.groupby("source_h3").origin_h3.agg(frozenset)
    if origin_sets.nunique() != 1:
        raise ValueError("OD evaluation must retain every selected origin for every destination")
    if pairs.groupby("origin_h3").population.nunique().gt(1).any():
        raise ValueError("Population differs between OD destinations")
    rows = []
    for cell, group in pairs.groupby("source_h3"):
        total = group.population.sum()
        if total <= 0:
            raise ValueError("Selected origin population must be positive")
        reachable = group.routing_status.eq("reachable")
        if (
            not np.isfinite(group.loc[reachable, "duration_minutes"]).all()
            or group.loc[reachable, "duration_minutes"].lt(0).any()
        ):
            raise ValueError("Reachable routes require nonnegative finite travel time")
        evaluated = group.routing_status.isin(["reachable", "known_disconnected"])
        row = {
            "source_h3": cell,
            "POPULATION_TRAVEL_CONTEXT_AVAILABLE": bool(evaluated.any()),
            "POPULATION_TRAVEL_ROUTED_SELECTED_POPULATION_FRACTION": group.loc[
                reachable, "population"
            ].sum()
            / total,
            "POPULATION_TRAVEL_EVALUATED_SELECTED_POPULATION_FRACTION": group.loc[
                evaluated, "population"
            ].sum()
            / total,
            "ROUTING_POLICY": "road_only_no_ferry_no_shuttle_train",
        }
        for decay in decay_minutes:
            row[f"POPULATION_TRAVEL_DEMAND_{decay}_MIN"] = (
                float(
                    (
                        group.loc[reachable, "population"]
                        * np.exp(-group.loc[reachable, "duration_minutes"] / decay)
                    ).sum()
                )
                if evaluated.any()
                else np.nan
            )
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extracts", type=Path, required=True)
    parser.add_argument("--graph", type=Path)
    parser.add_argument("--date", default="260825")
    args = parser.parse_args()
    extracts = acquire(args.extracts, date=args.date)
    if args.graph:
        print(build_graph(extracts, args.graph), flush=True)


if __name__ == "__main__":
    main()
