"""Corrected water acceptance cases from reliability review F08/F09."""

import h3
import numpy as np
import pandas as pd
import polars as pl
import pytest
from human.activity_and_effort.water_observation_opportunity.pipeline import (
    condition_arrays,
    propagated_condition_summaries,
    propagate_component,
    kernel_basis,
    area_weighted_parent_kernel,
    apply_coverage_semantics,
)


def conditions(daylight, wind):
    return condition_arrays(
        visibility_km=np.full_like(daylight, 10.0),
        daylight_fraction=daylight,
        wind_speed_ms=wind,
        precipitation_mm_day=np.zeros_like(daylight),
        weather_complete=np.ones_like(daylight, dtype=bool),
        wind_midpoint_ms=5.0,
        wind_slope_ms=1.0,
        precipitation_half_mm_day=10.0,
    )


def basis():
    return kernel_basis(
        pl.DataFrame(
            {
                "s": ["a", "b", "c"],
                "t": ["x", "x", "y"],
                "w": [1.0, 3.0, 0.0],
                "DISTANCE_BIN_INDEX": [1, 1, 1],
            }
        ),
        source_column="s",
        target_column="t",
        weight_column="w",
        distance_bin_km=1.0,
    )


def test_independent_conditions_do_not_mutate_and_means_have_own_support():
    daylight = np.array([[0.4, 0.8, 0.5]])
    wind = np.array([[5.0, np.nan, np.nan]])
    prior = daylight.copy()
    c = conditions(daylight, wind)
    np.testing.assert_array_equal(daylight, prior)
    assert np.isfinite(c["daylight_weight"]).all()
    summaries = propagated_condition_summaries(
        c, basis(), visibility_transition_fraction=0.2, visibility_minimum_transition_km=1.0
    )
    assert summaries["DAYLIGHT_WEIGHT"][0, 0] == pytest.approx(0.7)
    assert summaries["DAYLIGHT_WEIGHT_COVERAGE"][0, 0] == 1
    assert summaries["WIND_WEIGHT"][0, 0] == 0.5
    assert summaries["WIND_WEIGHT_COVERAGE"][0, 0] == 0.25
    assert np.isnan(summaries["WIND_WEIGHT"][0, 1])


@pytest.mark.parametrize(
    "activity,daylight,expected,coverage",
    [
        ([1, 1, 1], [1, 1, 1], None, 0),
        ([0, 0, 1], [1, 1, 1], 0, 1),
        ([1, 1, 1], [0, 0, 1], 0, 1),
    ],
)
def test_missing_relevant_weather_and_structural_zeros(activity, daylight, expected, coverage):
    c = conditions(np.array([daylight], dtype=float), np.full((1, 3), np.nan))
    result, support = propagate_component(
        np.array([activity], dtype=float),
        c,
        basis(),
        visibility_transition_fraction=0.2,
        visibility_minimum_transition_km=1.0,
        wind_exponent=1.0,
        precipitation_exponent=1.0,
        return_coverage=True,
    )
    assert support[0, 0] == coverage
    if expected is None:
        assert np.isnan(result[0, 0])
    else:
        assert result[0, 0] == expected
    assert result[0, 1] == 0  # zero kernel needs no dynamic evidence
    safe, states = apply_coverage_semantics(result, np.array([False]), derived=True)
    assert np.isnan(safe[0, 1])  # unknown AIS coverage still cannot claim a total zero


def test_fine_distance_bins_and_unequal_canonical_water_areas():
    parent = h3.latlng_to_cell(48.5, -123.0, 6)
    children = sorted(h3.cell_to_children(parent, 7))
    source = children[0]
    support = pd.DataFrame(
        {"target_h3": children, "target_water_area_m2": [1.0, 9.0, 10.0, 10.0, 10.0, 10.0, 10.0]}
    )
    static = pl.DataFrame(
        {
            "source_h3": [source, source],
            "target_h3": children[:2],
            "weight_static_viewability": [1.0, 0.5],
        }
    )
    result = area_weighted_parent_kernel(
        static, support, distance_bin_km=0.1, aggregate_sources=True
    )
    assert result["DISTANCE_BIN_INDEX"].n_unique() == 2
    assert result["weight"].sum() == pytest.approx((1 + 9 * 0.5) / 60)
    # Adding an explicit zero pair does not remove previously unmodeled water from the denominator.
    extra = pl.DataFrame(
        {"source_h3": [source], "target_h3": [children[2]], "weight_static_viewability": [0.0]}
    )
    extended = area_weighted_parent_kernel(
        pl.concat([static, extra]), support, distance_bin_km=0.1, aggregate_sources=True
    )
    assert extended["weight"].sum() == pytest.approx(result["weight"].sum())


def test_dates_outside_ais_source_support_stay_unavailable(tmp_path):
    from types import SimpleNamespace
    from datetime import date
    from human.activity_and_effort.water_observation_opportunity.build import (
        _read_ais_year,
    )
    from human.activity_and_effort.water_observation_opportunity.pipeline import (
        AIS_ACTIVITY_COLUMNS,
        COMMERCIAL_INPUT_COLUMNS,
    )

    path = tmp_path / "ais.parquet"
    row = {name: [2.0] for name in [*AIS_ACTIVITY_COLUMNS.values(), *COMMERCIAL_INPUT_COLUMNS]}
    pl.DataFrame(
        {
            **row,
            "DATE": [date(2024, 1, 1)],
            "H3_INDEX": ["a"],
            "SOURCE_TEMPORAL_COVERAGE_FRACTION": [1.0],
            "SOURCE_COVERAGE_COMPLETE": [True],
            "SOURCE_COVERAGE_STATUS": ["complete"],
        }
    ).write_parquet(path)
    values, coverage, complete, statuses = _read_ais_year(
        SimpleNamespace(ais_daily_path=path),
        source_cells=pd.Index(["a"]),
        dates=pd.date_range("2024-01-02", periods=2),
    )
    assert all(np.isnan(v).all() for v in values.values())
    assert np.isnan(coverage).all()
    assert not complete.any()
    assert statuses == ["unknown", "unknown"]
