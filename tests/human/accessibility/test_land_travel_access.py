from __future__ import annotations

import math

import pandas as pd
import polars as pl

from human.accessibility.land_transport_access.build import (
    build_components as build_transport_components,
)
from human.accessibility.land_transport_access.config import (
    load_land_transport_config,
)
from human.accessibility.population_travel_time.build import (
    build_components as build_population_travel_components,
)
from human.accessibility.population_travel_time.config import (
    load_population_travel_config,
)
from human.activity_and_effort.land_reporting_opportunity.build import (
    build_source_components,
    build_target_components,
)


def test_repository_land_travel_configs_load() -> None:
    assert load_land_transport_config().h3_resolution == 7
    assert load_population_travel_config().primary_decay_minutes == 120


def test_land_transport_components_preserve_unavailable_rows() -> None:
    frame = pd.DataFrame(
        {
            "source_h3": ["cell-a", "cell-b"],
            "ROAD_SNAP_DISTANCE_FROM_SOURCE_CENTROID_M": [5_000.0, None],
            "MIN_CITY_TRAVEL_TIME_MIN": [120.0, 60.0],
            "ROAD_ROUTING_AVAILABLE": [True, False],
            "CITY_TRAVEL_ROUTING_AVAILABLE": [True, True],
        }
    )
    output = build_transport_components(
        frame,
        road_distance_decay_km=5.0,
        city_travel_time_decay_minutes=120.0,
    ).set_index("H3_INDEX")
    assert output.loc["cell-a", "ROAD_PROXIMITY_COMPONENT"] == math.exp(-1)
    assert output.loc["cell-a", "CITY_TRAVEL_ACCESS_COMPONENT"] == math.exp(-1)
    assert output.loc["cell-a", "LAND_TRANSPORT_ACCESS_OPPORTUNITY_INDEX"] == math.exp(-2)
    assert pd.isna(output.loc["cell-b", "ROAD_PROXIMITY_COMPONENT"])
    assert pd.isna(output.loc["cell-b", "LAND_TRANSPORT_ACCESS_OPPORTUNITY_INDEX"])
    assert output.loc["cell-b", "MEASUREMENT_STATUS"] == "unavailable"


def test_population_travel_components_gate_incomplete_routing() -> None:
    frame = pd.DataFrame(
        {
            "source_h3": ["cell-a", "cell-b", "cell-c"],
            "POPULATION_TRAVEL_CONTEXT_AVAILABLE": [True, True, True],
            "POPULATION_TRAVEL_ROUTED_SELECTED_POPULATION_FRACTION": [1.0, 0.995, 0.5],
            "POPULATION_TRAVEL_DEMAND_60_MIN": [100.0, 1_000.0, 50_000.0],
            "POPULATION_TRAVEL_DEMAND_120_MIN": [200.0, 2_000.0, 60_000.0],
            "POPULATION_TRAVEL_DEMAND_240_MIN": [300.0, 3_000.0, 70_000.0],
        }
    )
    output, caps = build_population_travel_components(
        frame,
        decay_minutes=(60, 120, 240),
        primary_decay_minutes=120,
        cap_quantile=0.99,
        minimum_routed_population_fraction=0.99,
    )
    output = output.set_index("H3_INDEX")
    assert set(caps) == {60, 120, 240}
    assert output.loc["cell-a", "POPULATION_TRAVEL_OPPORTUNITY_INDEX"] > 0
    assert output.loc["cell-b", "POPULATION_TRAVEL_OPPORTUNITY_INDEX"] <= 1
    assert pd.isna(output.loc["cell-c", "POPULATION_TRAVEL_OPPORTUNITY_INDEX"])
    assert output.loc["cell-c", "MEASUREMENT_STATUS"] == "unavailable"


def test_land_reporting_keeps_unknown_access_separate_from_challenger() -> None:
    cells = pl.DataFrame({"source_h3": ["source-a", "source-b"]})
    transport = pl.DataFrame(
        {
            "H3_INDEX": ["source-a", "source-b"],
            "ROAD_PROXIMITY_COMPONENT": [0.5, 1.0],
            "CITY_TRAVEL_ACCESS_COMPONENT": [0.8, 0.9],
            "LAND_TRANSPORT_ACCESS_OPPORTUNITY_INDEX": [0.4, 0.9],
            "LAND_TRANSPORT_ACCESS_AVAILABLE": [True, True],
        }
    )
    travel = pl.DataFrame(
        {
            "H3_INDEX": ["source-a", "source-b"],
            "POPULATION_TRAVEL_OPPORTUNITY_INDEX": [0.6, 0.7],
            "POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE": [True, True],
        }
    )
    shore = pl.DataFrame(
        {
            "H3_INDEX": ["source-a"],
            "PUBLIC_ACCESS_EVIDENCE_STATE": ["verified_public"],
            "VERIFIED_PUBLIC_ACCESS_SITE_COUNT": [1],
            "OSM_EXPLICIT_PUBLIC_ACCESS_SITE_COUNT": [0],
            "OSM_SHORE_CANDIDATE_COUNT": [0],
            "SOURCE_COVERAGE_COMPLETE": [False],
        }
    )
    source = build_source_components(cells, transport, travel, shore).sort("source_h3")
    assert source.get_column("LAND_TRANSPORT_TRAVEL_OPPORTUNITY_INDEX").to_list() == [
        0.3,
        0.7,
    ]
    assert source.get_column("VERIFIED_PUBLIC_ACCESS_SUPPORTED_INDEX").to_list() == [
        0.3,
        None,
    ]
    assert source.get_column("PUBLIC_ACCESS_EVIDENCE_STATE").to_list() == [
        "verified_public",
        "unknown",
    ]
    static = pl.DataFrame(
        {
            "source_h3": ["source-a", "source-b"],
            "target_h3": ["target-a", "target-a"],
            "weight_static_viewability": [0.5, 0.5],
        }
    )
    target, cap = build_target_components(static, source, scaling_quantile=0.99)
    assert cap > 0
    assert target.height == 1
    assert target["LAND_TRANSPORT_TRAVEL_REPORTING_OPPORTUNITY_RAW"].item() == 0.5
    assert target["VERIFIED_PUBLIC_ACCESS_SUPPORTED_RAW"].item() == 0.15
    assert target["VERIFIED_PUBLIC_ACCESS_SOURCE_COUNT"].item() == 1
