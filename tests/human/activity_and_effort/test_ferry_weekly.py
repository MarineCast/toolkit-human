from __future__ import annotations

import h3
import pandas as pd

from human.activity_and_effort.ferry.build import build_weekly_r6


def test_ferry_weekly_r6_conserves_effort_and_flags_fallback() -> None:
    cell = h3.latlng_to_cell(48.5, -123.0, 7)
    route_daily = pd.DataFrame(
        {
            "service_date": ["2026-01-05", "2026-01-06"],
            "source_h3": [cell, cell],
            "route_geometry_id": ["route", "route"],
            "operator": ["WSF", "WSF"],
            "ridership_is_estimated": [False, True],
            "platform_effort_available": [True, False],
            "voyage_duration_source": ["wsdot_history", "configured_route_duration_fallback"],
            "ferry_rider_minutes": [100.0, 50.0],
            "ferry_rider_hours": [100.0 / 60, 50.0 / 60],
            "ferry_rider_km": [10.0, 5.0],
            "ferry_vessel_minutes": [20.0, None],
            "ferry_vessel_hours": [1 / 3, None],
            "ferry_vessel_km": [2.0, None],
        }
    )
    output = build_weekly_r6(route_daily)
    assert len(output) == 1
    assert output.loc[0, "ferry_rider_minutes"] == 150.0
    assert output.loc[0, "ferry_vessel_minutes"] == 20.0
    assert output.loc[0, "ferry_route_count"] == 1
    assert output.loc[0, "has_observed_ridership"]
    assert output.loc[0, "has_estimated_ridership"]
    assert output.loc[0, "has_duration_fallback"]
