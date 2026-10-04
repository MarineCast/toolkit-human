import json
import shutil
import socket
import time
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests

from human.accessibility.land_transport_access.routing import (
    OSRM_ROOT,
    classify_pair,
    population_demand,
)
from human.accessibility.population_travel_time.build import build_components


def test_disconnected_is_not_failed_routing():
    assert (
        classify_pair(None, request_ok=True, covered=True, snap_valid=True) == "routing_unavailable"
    )
    assert (
        classify_pair(
            None, request_ok=True, covered=True, snap_valid=True, disconnected_proven=True
        )
        == "known_disconnected"
    )
    assert (
        classify_pair(
            None, request_ok=True, covered=False, snap_valid=True, disconnected_proven=True
        )
        == "routing_unavailable"
    )


def test_island_residents_and_disconnected_mainland_population():
    pairs = pd.DataFrame(
        {
            "source_h3": ["island"] * 2,
            "origin_h3": ["mainland", "island"],
            "population": [1000.0, 10.0],
            "duration_minutes": [np.nan, 0.0],
            "routing_status": ["known_disconnected", "reachable"],
        }
    )
    demand = population_demand(pairs)
    assert demand.loc[0, "POPULATION_TRAVEL_DEMAND_120_MIN"] == 10
    assert demand.loc[0, "POPULATION_TRAVEL_EVALUATED_SELECTED_POPULATION_FRACTION"] == 1
    result, _ = build_components(
        demand,
        decay_minutes=(60, 120, 240),
        primary_decay_minutes=120,
        cap_quantile=0.99,
        minimum_routed_population_fraction=0.99,
    )
    assert result.POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE.all()
    pairs["routing_status"] = "routing_unavailable"
    assert population_demand(pairs).POPULATION_TRAVEL_DEMAND_120_MIN.isna().all()


def test_missing_origin_cannot_inflate_evaluated_fraction():
    pairs = pd.DataFrame(
        {
            "source_h3": ["a", "a", "b"],
            "origin_h3": ["x", "y", "x"],
            "population": 10.0,
            "duration_minutes": 2.0,
            "routing_status": "reachable",
        }
    )
    with pytest.raises(ValueError, match="every selected origin"):
        population_demand(pairs)


def test_origin_snapshot_rejects_changed_context_before_publication(tmp_path):
    from human.accessibility.land_transport_access.context_snapshot import snapshot
    from human.utils.artifacts import sha256_file

    population = (
        tmp_path
        / "outputs/effort/land_source_context/population_travel_time/population_travel_origins_h3_r4_prototype.parquet"
    )
    cities = (
        tmp_path
        / "outputs/effort/land_source_context/transport_access/land_source_transport_access_h3_r7_prototype.metadata.json"
    )
    population.parent.mkdir(parents=True)
    cities.parent.mkdir(parents=True)
    pd.DataFrame({"population": [1]}).to_parquet(population)
    cities.write_text('{"city_origins": []}')
    metadata = tmp_path / "od.json"
    metadata.write_text(
        json.dumps(
            {"origin_sha256": sha256_file(population), "city_origins_sha256": sha256_file(cities)}
        )
    )
    result = snapshot(tmp_path, tmp_path / "pinned", od_metadata=metadata)
    assert len(json.loads(result.read_text())["artifacts"]) == 2
    cities.write_text('{"city_origins": ["changed"]}')
    with pytest.raises(ValueError, match="changed"):
        snapshot(tmp_path, tmp_path / "invalid", od_metadata=metadata)
    assert not (tmp_path / "invalid").exists()


def test_partial_population_lower_bound_is_explicit_and_never_complete():
    pairs = pd.DataFrame(
        {
            "source_h3": ["a", "a"],
            "origin_h3": ["x", "y"],
            "population": [90.0, 10.0],
            "duration_minutes": [5.0, np.nan],
            "routing_status": ["reachable", "routing_unavailable"],
        }
    )
    demand = population_demand(pairs)
    with pytest.raises(ValueError, match="cap"):
        build_components(
            demand,
            decay_minutes=(120,),
            primary_decay_minutes=120,
            cap_quantile=0.99,
            minimum_routed_population_fraction=0.99,
        )
    result, _ = build_components(
        demand,
        decay_minutes=(120,),
        primary_decay_minutes=120,
        cap_quantile=0.99,
        minimum_routed_population_fraction=0.99,
        allow_partial_evaluated_lower_bound=True,
    )
    assert result.POPULATION_TRAVEL_OPPORTUNITY_STATUS.tolist() == [
        "partial_evaluated_population_lower_bound"
    ]
    assert result.POPULATION_TRAVEL_EVALUATED_SELECTED_POPULATION_FRACTION.iloc[0] == 0.9


def test_partial_city_context_is_not_labeled_complete():
    from human.accessibility.land_transport_access.build import (
        build_components as transport_components,
    )

    frame = pd.DataFrame(
        {
            "source_h3": ["a"],
            "ROAD_ROUTING_AVAILABLE": [True],
            "ROAD_SNAP_DISTANCE_FROM_SOURCE_CENTROID_M": [100.0],
            "CITY_TRAVEL_ROUTING_AVAILABLE": [True],
            "MIN_CITY_TRAVEL_TIME_MIN": [20.0],
            "CITY_EVALUATED_ORIGIN_FRACTION": [0.875],
        }
    )
    result = transport_components(
        frame, road_distance_decay_km=2, city_travel_time_decay_minutes=120
    )
    assert result.LAND_TRANSPORT_ACCESS_AVAILABLE.all()
    assert result.LAND_TRANSPORT_ACCESS_STATUS.tolist() == ["partial_evaluated_city_lower_bound"]


def test_snap_cannot_cross_water_even_within_same_land_polygon():
    import shapely
    from shapely.geometry import Point, Polygon

    from human.accessibility.land_transport_access.route_demand import (
        same_land_polygon,
    )

    land = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], holes=[[(1, 1), (3, 1), (3, 3), (1, 3)]])
    tree = shapely.STRtree([land])
    assert not same_land_polygon(Point(0.5, 2), Point(3.5, 2), tree)
    assert same_land_polygon(Point(0.1, 0.1), Point(0.9, 0.9), tree)


@pytest.mark.skipif(
    not (OSRM_ROOT / "bin/osrm-extract").exists(), reason="native OSRM not installed"
)
@pytest.mark.parametrize("kind", ("road", "bridge", "tunnel", "ferry", "shuttle_train"))
def test_native_profile_excludes_ferry_and_shuttle_train(tmp_path: Path, kind: str):
    """Route between road segments; prohibited connectors must leave them disconnected."""
    profiles = tmp_path / "profiles"
    shutil.copytree(OSRM_ROOT / "share/osrm/profiles", profiles)
    from human.accessibility.land_transport_access import routing

    shutil.copy2(Path(routing.__file__).with_name("road_only.lua"), profiles / "road_only.lua")
    tags = (
        '<tag k="highway" v="residential"/>'
        if kind == "road"
        else f'<tag k="route" v="{kind}"/>'
    )
    if kind in {"bridge", "tunnel"}:
        tags = f'<tag k="highway" v="residential"/><tag k="{kind}" v="yes"/>'
    osm = tmp_path / "test.osm"
    osm.write_text(
        '<?xml version="1.0"?><osm version="0.6">'
        '<node id="1" lat="48.000" lon="-123.000" version="1"/>'
        '<node id="2" lat="48.001" lon="-123.001" version="1"/>'
        '<node id="3" lat="48.002" lon="-123.002" version="1"/>'
        '<node id="4" lat="48.003" lon="-123.003" version="1"/>'
        '<way id="1" version="1"><nd ref="1"/><nd ref="2"/>'
        '<tag k="highway" v="residential"/></way>'
        '<way id="2" version="1"><nd ref="2"/><nd ref="3"/>' + tags + '</way>'
        '<way id="3" version="1"><nd ref="3"/><nd ref="4"/>'
        '<tag k="highway" v="residential"/></way></osm>'
    )
    graph = tmp_path / "test.osrm"
    for binary, args in (
        ("extract", ["-p", str(profiles / "road_only.lua"), str(osm)]),
        ("partition", [str(graph)]),
        ("customize", [str(graph)]),
    ):
        result = subprocess.run(
            [str(OSRM_ROOT / f"bin/osrm-{binary}"), "--threads", "1", *args],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with (tmp_path / "routed.log").open("w+") as log:
        server = subprocess.Popen(
            [str(OSRM_ROOT / "bin/osrm-routed"), "--algorithm", "mld",
             "--ip", "127.0.0.1", "--port", str(port), "--threads", "1", str(graph)],
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 15
            url = f"http://127.0.0.1:{port}/route/v1/driving/-123.000,48.000;-123.003,48.003"
            while True:
                assert server.poll() is None, "osrm-routed exited during startup"
                try:
                    response = requests.get(
                        url, params={"overview": "false", "radiuses": "20;20"}, timeout=2
                    )
                    break
                except requests.ConnectionError:
                    if time.monotonic() >= deadline:
                        pytest.fail("osrm-routed did not become ready")
                    time.sleep(0.05)
            payload = response.json()
            if kind in {"road", "bridge", "tunnel"}:
                assert response.status_code == 200, payload
                assert payload["code"] == "Ok", payload
                assert payload["routes"][0]["distance"] > 300
            else:
                assert payload["code"] == "NoRoute", payload
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def test_native_runtime_prefix_can_be_configured(tmp_path, monkeypatch):
    from human.accessibility.land_transport_access.routing import resolve_osrm_root

    monkeypatch.setenv("HUMAN_OSRM_ROOT", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda _: pytest.fail("explicit prefix must win"))
    assert resolve_osrm_root() == tmp_path.resolve()


def test_native_runtime_prefix_follows_executable_symlink(tmp_path, monkeypatch):
    from human.accessibility.land_transport_access.routing import resolve_osrm_root

    prefix = tmp_path / "versioned-install"
    executable = prefix / "bin/osrm-extract"
    executable.parent.mkdir(parents=True)
    executable.touch()
    link = tmp_path / "osrm-extract"
    link.symlink_to(executable)
    monkeypatch.delenv("HUMAN_OSRM_ROOT", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: str(link))
    assert resolve_osrm_root() == prefix
