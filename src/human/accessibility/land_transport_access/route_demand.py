"""Evaluate selected population and city origins against a pinned local graph.

NoRoute means disconnected *in this graph*, not proof of real-world absence of
roads. Snaps must remain on the same Natural Earth land polygon. Ambiguous
coastal centroids remain unavailable rather than moving population over water.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import requests
import shapely
from shapely.geometry import LineString, Point

from human.utils.artifacts import (
    atomic_write_json,
    atomic_write_parquet,
    sha256_file,
)

from .routing import OSRM_ROOT, population_demand


def same_land_polygon(point: Point, snapped: Point, tree: shapely.STRtree) -> bool:
    candidates = tree.query(point, predicate="intersects")
    segment = LineString([point, snapped])
    return any(tree.geometries[index].covers(segment) for index in candidates)


def run(
    root: Path,
    graph_dir: Path,
    output: Path,
    *,
    origin_path: Path | None = None,
    city_path: Path | None = None,
) -> Path:
    manifest = json.loads((graph_dir / "manifest.json").read_text())
    if manifest["routing_policy"] != "road_only_no_ferry_no_shuttle_train":
        raise ValueError("Not a road-only graph")
    for name, digest in manifest["graph_files"].items():
        if sha256_file(graph_dir / name) != digest:
            raise ValueError(f"Stale road graph file: {name}")
    if sha256_file(OSRM_ROOT / "bin/osrm-routed") != manifest["engine_sha256"]:
        raise ValueError("Road runtime changed")
    output.mkdir(parents=True, exist_ok=False)
    origin_path = origin_path or (
        root
        / "outputs/effort/land_source_context/population_travel_time/population_travel_origins_h3_r4_prototype.parquet"
    )
    origins = pd.read_parquet(origin_path)
    origins = origins.loc[origins.ORIGIN_SELECTED].copy()
    city_path = city_path or (
        root
        / "outputs/effort/land_source_context/transport_access/land_source_transport_access_h3_r7_prototype.metadata.json"
    )
    cities = json.loads(city_path.read_text())["city_origins"]
    land_path = root / "data/raw/gis/land/ne_10m_land.shp"
    land = gpd.read_file(land_path).to_crs(4326).explode(index_parts=False)
    tree = shapely.STRtree(land.geometry.to_numpy())
    weights = pd.read_parquet(
        root / "data/processed/domain/human/viewshed/RES7/LAND_STATIC_WEIGHTS_R7.parquet",
        columns=["source_h3"],
    )
    cells = sorted(weights.source_h3.unique())
    destinations = [(cell, *h3.cell_to_latlng(cell)) for cell in cells]
    origin_points = [
        (str(r.origin_h3), float(r.ORIGIN_LATITUDE), float(r.ORIGIN_LONGITUDE))
        for r in origins.itertuples()
    ]
    city_points = [("city:" + c["CITY"], c["LATITUDE"], c["LONGITUDE"]) for c in cities]
    all_points = origin_points + city_points + destinations
    endpoint = "http://127.0.0.1:5066"
    log = (output / "router.log").open("w")
    process = subprocess.Popen(
        [
            str(OSRM_ROOT / "bin/osrm-routed"),
            "--algorithm",
            "mld",
            "--ip",
            "127.0.0.1",
            "--port",
            "5066",
            "--threads",
            "4",
            "--max-table-size",
            "1000",
            manifest["graph_path"],
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    session = requests.Session()
    try:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError("Local router exited; inspect router.log")
            try:
                session.get(
                    endpoint + "/nearest/v1/driving/-122.3321,47.6062", timeout=2
                ).raise_for_status()
                break
            except requests.ConnectionError:
                time.sleep(0.5)
        else:
            raise RuntimeError("Local router did not become ready")
        # Acceptance routes: mainland and same-island roads exist; ferry-only
        # mainland-to-Victoria and San Juan connections must have no road path.
        acceptance = []
        for name, coordinates, expected in (
            ("seattle_tacoma", "-122.3321,47.6062;-122.4443,47.2529", "Ok"),
            ("victoria_nanaimo", "-123.3656,48.4284;-123.9401,49.1659", "Ok"),
            ("vancouver_victoria", "-123.1207,49.2827;-123.3656,48.4284", "NoRoute"),
            ("seattle_friday_harbor", "-122.3321,47.6062;-123.0171,48.5343", "NoRoute"),
        ):
            response = session.get(
                endpoint + "/route/v1/driving/" + coordinates,
                params={"overview": "false", "steps": "true"},
                timeout=120,
            ).json()
            modes = {
                s["mode"]
                for route in response.get("routes", [])
                for leg in route["legs"]
                for s in leg["steps"]
            }
            if response.get("code") != expected or modes & {"ferry", "train"}:
                raise ValueError(
                    f"Road-only acceptance failed: {name}: {response.get('code')} {modes}"
                )
            acceptance.append({"case": name, "result": response["code"], "modes": sorted(modes)})
        atomic_write_json(output / "acceptance.json", acceptance)
        snaps = {}
        for index, (key, lat, lon) in enumerate(all_points):
            response = session.get(
                endpoint + f"/nearest/v1/driving/{lon},{lat}", params={"number": 1}, timeout=30
            ).json()
            nearest = response.get("waypoints", [{}])[0]
            location = nearest.get("location")
            valid = bool(
                location
                and nearest.get("distance", np.inf) <= 30000
                and same_land_polygon(Point(lon, lat), Point(*location), tree)
            )
            snaps[key] = {
                "key": key,
                "latitude": lat,
                "longitude": lon,
                "snap_valid": valid,
                "snap_distance_m": nearest.get("distance"),
                "snap_location": location,
                "snap_nodes": nearest.get("nodes"),
                "snap_status": "same_land_polygon" if valid else "ambiguous_or_unavailable",
            }
            if index % 500 == 0:
                print(f"Validated snaps {index}/{len(all_points)}", flush=True)
        atomic_write_json(output / "snaps.json", list(snaps.values()))
        valid_origins = [p for p in origin_points + city_points if snaps[p[0]]["snap_valid"]]
        od_parts, transport_rows = [], []
        population = origins.set_index("origin_h3").POPULATION.to_dict()
        for start in range(0, len(destinations), 50):
            batch = destinations[start : start + 50]
            valid_destinations = [p for p in batch if snaps[p[0]]["snap_valid"]]
            durations = {}
            if valid_destinations and valid_origins:
                points = valid_origins + valid_destinations
                coords = ";".join(",".join(map(str, snaps[p[0]]["snap_location"])) for p in points)
                n = len(valid_origins)
                response = session.get(
                    endpoint + "/table/v1/driving/" + coords,
                    params={
                        "sources": ";".join(map(str, range(n))),
                        "destinations": ";".join(map(str, range(n, len(points)))),
                        "annotations": "duration",
                    },
                    timeout=120,
                ).json()
                if response.get("code") != "Ok":
                    raise ValueError(f"Local routing batch failed: {response.get('code')}")
                atomic_write_json(output / f"table_{start:05d}.json", response)
                durations = {
                    (p[0], d[0]): response["durations"][i][j]
                    for i, p in enumerate(valid_origins)
                    for j, d in enumerate(valid_destinations)
                }
            records = []
            for cell, lat, lon in batch:
                for origin, _, _ in origin_points:
                    evaluated = (origin, cell) in durations
                    seconds = durations.get((origin, cell))
                    records.append(
                        {
                            "source_h3": cell,
                            "origin_h3": origin,
                            "population": population[origin],
                            "duration_minutes": seconds / 60 if seconds is not None else np.nan,
                            "routing_status": (
                                "reachable"
                                if seconds is not None
                                else "known_disconnected" if evaluated else "routing_unavailable"
                            ),
                            "disconnection_basis": (
                                "no_directed_path_in_validated_pinned_graph"
                                if evaluated and seconds is None
                                else None
                            ),
                        }
                    )
                times = [durations.get((city[0], cell)) for city in city_points]
                finite = [t / 60 for t in times if t is not None]
                evaluated_cities = all((city[0], cell) in durations for city in city_points)
                transport_rows.append(
                    {
                        "source_h3": cell,
                        "SOURCE_CENTROID_LATITUDE": lat,
                        "SOURCE_CENTROID_LONGITUDE": lon,
                        "ROAD_SNAP_DISTANCE_FROM_SOURCE_CENTROID_M": snaps[cell]["snap_distance_m"],
                        "ROAD_ROUTING_AVAILABLE": snaps[cell]["snap_valid"],
                        "MIN_CITY_TRAVEL_TIME_MIN": min(finite) if finite else np.nan,
                        "CITY_TRAVEL_ROUTING_AVAILABLE": bool(finite),
                        "CITY_EVALUATED_ORIGIN_FRACTION": sum(
                            (city[0], cell) in durations for city in city_points
                        )
                        / len(city_points),
                        "CITY_TRAVEL_CONTEXT_STATUS": (
                            "complete_city_evaluation"
                            if evaluated_cities
                            else "partial_evaluated_city_lower_bound"
                        ),
                        "CITY_KNOWN_DISCONNECTED": evaluated_cities and not finite,
                        "ROUTING_POLICY": manifest["routing_policy"],
                    }
                )
            part = pd.DataFrame(records)
            atomic_write_parquet(part, output / f"od_{start:05d}.parquet")
            od_parts.append(part)
            print(
                f"Routed destinations {min(start + 50, len(destinations))}/{len(destinations)}",
                flush=True,
            )
        pairs = pd.concat(od_parts, ignore_index=True)
        demand = population_demand(pairs)
        transport = pd.DataFrame(transport_rows)
        for name, frame in (("population_demand", demand), ("transport", transport)):
            path = output / f"{name}.parquet"
            atomic_write_parquet(frame, path)
            atomic_write_json(
                output / f"{name}.metadata.json",
                {
                    "h3_resolution": 7,
                    "rows": len(frame),
                    "routing_policy": manifest["routing_policy"],
                    "graph_manifest": str((graph_dir / "manifest.json").resolve()),
                    "graph_manifest_sha256": sha256_file(graph_dir / "manifest.json"),
                    "acceptance_path": str((output / "acceptance.json").resolve()),
                    "acceptance_sha256": sha256_file(output / "acceptance.json"),
                    "origin_sha256": sha256_file(origin_path),
                    "city_origins_sha256": sha256_file(city_path),
                    "land_mask_sha256": sha256_file(land_path),
                    "source_artifact": {"sha256": sha256_file(path)},
                    "limitations": [
                        "Natural Earth centroid snap ambiguity remains unavailable.",
                        "NoRoute is disconnection in the regional pinned OSM graph, not proven real-world inaccessibility.",
                        "Road inventory and population snapshots are contemporary, not historical routes.",
                    ],
                },
            )
        return output
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        session.close()
        log.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origins", type=Path)
    parser.add_argument("--cities", type=Path)
    args = parser.parse_args()
    print(
        run(args.root, args.graph, args.output, origin_path=args.origins, city_path=args.cities),
        flush=True,
    )


if __name__ == "__main__":
    main()
