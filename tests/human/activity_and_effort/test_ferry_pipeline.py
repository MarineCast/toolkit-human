from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from human.activity_and_effort.ferry.pipeline import (
    FerryEffortError,
    RouteMapping,
    UnresolvedRoutesError,
    allocate_route_days,
    apply_wsf_duration_adjustments,
    build_wsf_duration_adjustments,
    collapse_source_cells,
    resolve_route_days,
    standardize_bc_route_days,
)


def _route_day(
    *,
    route_key: str = "test",
    geometry_id: str = "TEST_ROUTE",
    riders: float = 100.0,
    sailings: float | None = 10.0,
    jurisdiction: str = "Washington",
    estimated: bool = False,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "service_date": [pd.Timestamp("2026-01-01")],
            "jurisdiction": [jurisdiction],
            "operator": ["Test Ferries"],
            "route_key": [route_key],
            "route_name": [route_key],
            "route_geometry_id": [geometry_id],
            "daily_riders": [riders],
            "daily_sailings": [sailings],
            "route_duration_minutes": [30.0],
            "ridership_is_estimated": [estimated],
            "sailing_count_is_estimated": [False],
            "platform_effort_available": [sailings is not None],
            "source_temporal_resolution": ["route_day"],
            "full_route_traversal_assumption": [jurisdiction == "British Columbia"],
            "ridership_source": ["synthetic"],
        }
    )


def _crosswalk(
    *, geometry_id: str = "TEST_ROUTE", fractions: tuple[float, ...] = (0.6, 0.4)
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "route_geometry_id": [geometry_id] * len(fractions),
            "source_h3": [f"cell_{index}" for index in range(len(fractions))],
            "h3_resolution": [7] * len(fractions),
            "distance_in_cell_m": [1000.0 * value for value in fractions],
            "route_length_m": [1000.0] * len(fractions),
            "distance_fraction": list(fractions),
            "voyage_time_in_cell_minutes": [30.0 * value for value in fractions],
            "route_duration_minutes": [30.0] * len(fractions),
        }
    )


def test_allocates_rider_and_vessel_minutes_by_centerline_fraction() -> None:
    allocated, checks = allocate_route_days(_route_day(), _crosswalk())

    assert allocated["ferry_rider_minutes"].tolist() == pytest.approx([1800, 1200])
    assert allocated["ferry_vessel_minutes"].tolist() == pytest.approx([180, 120])
    assert allocated["ferry_rider_minutes"].sum() == pytest.approx(3000)
    assert allocated["ferry_vessel_minutes"].sum() == pytest.approx(300)
    assert all(row.get("rider_minutes_passed") for row in checks)


def test_bc_rider_effort_exists_while_platform_effort_remains_null() -> None:
    route_day = _route_day(
        jurisdiction="British Columbia", riders=100, sailings=None, estimated=True
    )
    allocated, _ = allocate_route_days(route_day, _crosswalk())

    assert allocated["ferry_rider_minutes"].sum() == pytest.approx(3000)
    assert allocated["ferry_vessel_minutes"].isna().all()
    assert allocated["ferry_platform_weight_raw"].isna().all()
    assert not allocated["platform_effort_available"].any()


def test_zero_centerline_distance_cell_is_excluded() -> None:
    crosswalk = pd.concat(
        [
            _crosswalk(),
            pd.DataFrame(
                {
                    "route_geometry_id": ["TEST_ROUTE"],
                    "source_h3": ["footprint_only"],
                    "h3_resolution": [7],
                    "distance_in_cell_m": [0.0],
                    "route_length_m": [1000.0],
                    "distance_fraction": [0.0],
                    "voyage_time_in_cell_minutes": [0.0],
                    "route_duration_minutes": [30.0],
                }
            ),
        ],
        ignore_index=True,
    )

    allocated, _ = allocate_route_days(_route_day(), crosswalk)

    assert "footprint_only" not in set(allocated["source_h3"])


def test_invalid_distance_fractions_fail_conservation() -> None:
    with pytest.raises(FerryEffortError, match="distance_fraction sums"):
        allocate_route_days(_route_day(), _crosswalk(fractions=(0.5, 0.4)))


def test_multiple_routes_preserved_then_summed_by_date_and_cell() -> None:
    first, _ = allocate_route_days(
        _route_day(route_key="route_a", geometry_id="GEOM_A"),
        _crosswalk(geometry_id="GEOM_A", fractions=(1.0,)),
    )
    second, _ = allocate_route_days(
        _route_day(route_key="route_b", geometry_id="GEOM_B", riders=50, sailings=2),
        _crosswalk(geometry_id="GEOM_B", fractions=(1.0,)),
    )
    second["source_h3"] = first["source_h3"].iloc[0]
    route_level = pd.concat([first, second], ignore_index=True)

    collapsed = collapse_source_cells(route_level)

    assert len(route_level) == 2
    assert len(collapsed) == 1
    assert collapsed.loc[0, "ferry_route_count"] == 2
    assert collapsed.loc[0, "ferry_rider_minutes"] == pytest.approx(4500)
    assert collapsed.loc[0, "ferry_vessel_minutes"] == pytest.approx(360)


def test_bc_daily_estimates_conserve_published_route_month_total() -> None:
    rows = []
    for service_date, hourly_values in [
        ("2026-01-01", (40.0, 60.0)),
        ("2026-01-02", (80.0, 120.0)),
    ]:
        for value in hourly_values:
            rows.append(
                {
                    "service_date": service_date,
                    "route_number": "01",
                    "route_name": "Tsawwassen-Swartz Bay",
                    "reported_total_riders": value,
                    "report_month": "2026-01-01",
                    "published_monthly_passengers": 300.0,
                    "is_estimated": True,
                    "source": "synthetic monthly report",
                    "source_report_url": "https://example.test/report.pdf",
                    "observation_granularity": "estimated_route_hour",
                    "estimation_method": "synthetic allocation",
                }
            )
    route_days, checks = standardize_bc_route_days(pd.DataFrame(rows))

    assert route_days["daily_riders"].tolist() == pytest.approx([100, 200])
    assert route_days["daily_sailings"].isna().all()
    assert route_days["daily_riders"].sum() == pytest.approx(300)
    assert len(checks) == 1 and checks[0]["passed"]
    assert np.isclose(checks[0]["absolute_error"], 0.0)


def test_unmapped_route_fails_by_default_even_when_exclusion_is_documented() -> None:
    route_days = _route_day(route_key="not_accepted")
    mapping = RouteMapping(
        jurisdiction="Washington",
        ridership_route_key="accepted",
        route_geometry_id="ACCEPTED",
        route_name="Accepted",
        route_duration_minutes=30,
        segment_ids=("segment",),
    )
    exclusions = [
        {
            "jurisdiction": "Washington",
            "ridership_route_key": "not_accepted",
            "reason": "duration unresolved",
        }
    ]

    with pytest.raises(UnresolvedRoutesError, match="duration unresolved"):
        resolve_route_days(route_days, [mapping], exclusions)

    resolved, unresolved, excluded = resolve_route_days(
        route_days, [mapping], exclusions, allow_incomplete_routes=True
    )
    assert resolved.empty
    assert unresolved[0]["explicitly_excluded"] is True
    assert excluded == unresolved


def test_wsdot_operational_duration_with_configured_fallback() -> None:
    mapping = RouteMapping(
        jurisdiction="Washington",
        ridership_route_key="test",
        route_geometry_id="TEST_ROUTE",
        route_name="Test",
        route_duration_minutes=30,
        segment_ids=("segment",),
    )
    sailings = pd.DataFrame(
        {
            "service_date": ["2026-01-01", "2026-01-01"],
            "departure_local": ["2026-01-01 08:00", "2026-01-01 09:00"],
            "segment_id": ["test", "test"],
            "sailing_id": ["one", "two"],
            "origin_terminal": ["Seattle", "Seattle"],
            "destination_terminal": ["Bainbridge Island", "Bainbridge Island"],
            "reported_total_riders": [60.0, 40.0],
        }
    )
    history = pd.DataFrame(
        {
            "service_date": [pd.Timestamp("2026-01-01")],
            "departing_terminal": ["Colman"],
            "arriving_terminal": ["Bainbridge"],
            "scheduled_departure_local": [pd.Timestamp("2026-01-01 08:00")],
            "operational_duration_minutes": [20.0],
            "operational_duration_is_valid": [True],
        }
    )
    adjustments = build_wsf_duration_adjustments(sailings, history, [mapping])
    fixed, _ = allocate_route_days(_route_day(riders=100, sailings=2), _crosswalk())
    adjusted = apply_wsf_duration_adjustments(fixed, adjustments)

    assert adjustments.loc[0, "route_rider_minutes_total"] == pytest.approx(2400)
    assert adjustments.loc[0, "route_vessel_minutes_total"] == pytest.approx(50)
    assert adjustments.loc[0, "voyage_duration_history_coverage_fraction"] == 0.5
    assert adjusted["ferry_rider_minutes"].sum() == pytest.approx(2400)
    assert adjusted["ferry_vessel_minutes"].sum() == pytest.approx(50)
    assert adjusted["route_duration_minutes"].iloc[0] == pytest.approx(24)
    assert adjusted["platform_route_duration_minutes"].iloc[0] == pytest.approx(25)
