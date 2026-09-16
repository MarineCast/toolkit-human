from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import polars as pl
from scipy import sparse

from human.activity_and_effort.land_reporting_opportunity.config import (
    load_land_reporting_config,
)
from human.activity_and_effort.land_reporting_opportunity.dynamic import (
    STREAM_COMPONENTS,
    DynamicBasis,
    _add_daily_v2_columns,
    _daily_status,
    aggregate_weekly,
    compute_dynamic_chunk,
)
from human.activity_and_effort.observation_opportunity_contract import (
    LINEAGE_COLUMNS,
    lineage_values,
)


def _basis() -> DynamicBasis:
    dates = pd.date_range("2024-01-01", periods=2, freq="D")
    stream_weights = [1.0, 0.5, 0.8, 0.7, 0.56, 0.4, 0.3, 0.2]
    matrix = sparse.csr_matrix(np.asarray(stream_weights, dtype="float64")[:, None])
    static = {"H3_INDEX": ["target"]}
    for (stream, _component), weight in zip(STREAM_COMPONENTS, stream_weights):
        static[f"{stream}_STATIC_RAW"] = [weight]
        static[f"{stream}_CONTEXT_STATIC_SUPPORT"] = [weight]
        static[f"{stream}_STATIC_CONTEXT_FRACTION"] = [1.0]
    static["TARGET_R7_CHILD_COUNT"] = [1]
    static["LAND_SOURCE_COUNT"] = [1]
    weather = pd.DataFrame(
        {
            "VISIBILITY_KM_MEAN": [100.0, np.nan],
            "WIND_SPEED_10M_MS_MEAN": [5.5, 5.5],
            "PRECIP_MM_DAY_ESTIMATE": [0.0, 0.0],
            "SAMPLE_COVERAGE_FRAC": [1.0, 0.0],
            "QC_STATE": ["COMPLETE", "PARTIAL"],
        },
        index=dates,
    )
    daylight = pd.DataFrame({"DAYLIGHT_FRACTION": [0.5, 0.5]}, index=dates)
    calendar = pd.DataFrame({"calendar_effort_weight": [2.0, 2.0]}, index=dates)
    return DynamicBasis(
        target_order=pd.Index(["target"], name="H3_INDEX"),
        target_static=pd.DataFrame(static),
        distance_km=np.asarray([0.0]),
        weather_bases=[
            {
                "weather_h3_r5": "weather",
                "daylight_h3_r4": "daylight",
                "matrix_transpose": matrix,
                "context_totals": np.asarray(stream_weights),
            }
        ],
        weather_daily={"weather": weather},
        daylight_daily={"daylight": daylight},
        calendar_daily=calendar,
        dates=dates,
        native_pairs=1,
        grouped_rows=1,
    )


def test_dynamic_streams_share_physical_kernel_and_apply_calendar() -> None:
    cfg = replace(load_land_reporting_config(), wind_support_midpoint_ms=5.5)
    basis = _basis()
    result = compute_dynamic_chunk(cfg, basis, basis.dates)

    physical = result["PHYSICAL_VIEWABILITY_RAW"][0, 0]
    assert physical > 0.0
    assert np.isclose(result["POPULATION_TRAVEL_OPPORTUNITY_RAW"][0, 0], physical)
    assert np.isclose(result["LAND_EFFORT_PROXY_RAW"][0, 0], physical * 0.6)
    assert np.isnan(result["LAND_EFFORT_PROXY_RAW"][1, 0])
    assert result["LAND_EFFORT_PROXY_DYNAMIC_CONTEXT_COVERAGE"][1, 0] == 0.0


def test_daily_status_preserves_unknown_access_as_unavailable() -> None:
    status = _daily_status(
        raw=np.asarray([[1.0, 1.0]]),
        dynamic_coverage=np.asarray([[1.0, 1.0]]),
        access_fraction=np.asarray([[np.nan, 0.0]]),
        zero_static=np.asarray([[False, False]]),
    )
    assert status.tolist() == [
        [
            "unavailable_no_mapped_public_access_context",
            "unavailable_no_mapped_public_access_context",
        ]
    ]


def test_daily_status_distinguishes_access_from_dynamic_missingness() -> None:
    status = _daily_status(
        raw=np.asarray([[np.nan, np.nan]]),
        dynamic_coverage=np.asarray([[0.0, 0.0]]),
        access_fraction=np.asarray([[0.0, 0.5]]),
        zero_static=np.asarray([[False, False]]),
    )
    assert status.tolist() == [
        [
            "unavailable_no_mapped_public_access_context",
            "unavailable_required_dynamic_context",
        ]
    ]


def test_weekly_aggregation_keeps_all_null_raw_values_null() -> None:
    daily = pl.DataFrame(
        {
            "DATE": [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")],
            "H3_INDEX": ["cell", "cell"],
            "H3_RESOLUTION": [6, 6],
            "TARGET_R7_CHILD_COUNT": [2, 2],
            "LAND_SOURCE_COUNT": [3, 3],
            "CALENDAR_EFFORT_WEIGHT": [1.0, 1.0],
            "LAND_EFFORT_PROXY_RAW": [1.5, None],
            "VERIFIED_LAND_EFFORT_PROXY_RAW": [None, None],
            "LAND_EFFORT_PROXY_STATIC_INDEX": [0.4, 0.4],
            "LAND_EFFORT_PROXY_STATIC_CONTEXT_FRACTION": [0.5, 0.5],
            "LAND_EFFORT_PROXY_DYNAMIC_CONTEXT_COVERAGE": [1.0, 0.0],
        }
    ).lazy()

    weekly = aggregate_weekly(daily)

    assert weekly.height == 1
    assert weekly["WEEK_START"].item() == pd.Timestamp("2024-01-01")
    assert weekly["PERIOD_DAY_COUNT"].item() == 2
    assert weekly["LAND_EFFORT_PROXY_RAW"].item() == 1.5
    assert weekly["LAND_EFFORT_PROXY_AVAILABLE_DAY_COUNT"].item() == 1
    assert weekly["VERIFIED_LAND_EFFORT_PROXY_RAW"].item() is None
    assert weekly["VERIFIED_LAND_EFFORT_PROXY_AVAILABLE_DAY_COUNT"].item() == 0


def test_schema_v2_land_names_are_exact_aliases_and_capture_is_unavailable() -> None:
    columns: dict[str, list[object]] = {
        "DATE": [pd.Timestamp("2024-01-01")],
        "CALENDAR_EFFORT_WEIGHT": [0.75],
    }
    for stream, _component in STREAM_COMPONENTS:
        columns[f"{stream}_RAW"] = [2.0]
        columns[f"{stream}_INDEX"] = [0.5]
        columns[f"{stream}_STATIC_RAW"] = [3.0]
        columns[f"{stream}_STATIC_INDEX"] = [0.6]
        columns[f"{stream}_STATIC_CONTEXT_FRACTION"] = [1.0]
        columns[f"{stream}_DYNAMIC_CONTEXT_COVERAGE"] = [1.0]
    lineage = lineage_values(
        generation_id="fixture",
        config_hash="a" * 64,
        source_hashes={"fixture": "b" * 64},
        source_vintages={},
        knowledge_time_utc="2025-01-01T00:00:00+00:00",
    )

    result = _add_daily_v2_columns(pd.DataFrame(columns), lineage=lineage)

    assert (
        result["LAND_OBSERVATION_OPPORTUNITY_RAW"].item() == result["LAND_EFFORT_PROXY_RAW"].item()
    )
    assert (
        result["VERIFIED_LAND_OBSERVATION_OPPORTUNITY_INDEX"].item()
        == result["VERIFIED_LAND_EFFORT_PROXY_INDEX"].item()
    )
    assert result["CALENDAR_CONTEXT_WEIGHT"].item() == 0.75
    assert result["REPORTING_CAPTURE_WEIGHT"].isna().all()
    assert result["REPORTING_CAPTURE_STATE"].item() == "source_unavailable"
    assert set(LINEAGE_COLUMNS).issubset(result.columns)


def test_schema_v3_canonical_access_component_does_not_publish_legacy_structural_zero() -> None:
    columns: dict[str, list[object]] = {
        "DATE": [pd.Timestamp("2024-01-01")],
        "CALENDAR_EFFORT_WEIGHT": [1.0],
    }
    for stream, _component in STREAM_COMPONENTS:
        columns[f"{stream}_RAW"] = [0.0]
        columns[f"{stream}_INDEX"] = [0.0]
        columns[f"{stream}_STATIC_RAW"] = [0.0]
        columns[f"{stream}_STATIC_INDEX"] = [0.0]
        columns[f"{stream}_STATIC_CONTEXT_FRACTION"] = [
            0.0 if stream in {"LAND_EFFORT_PROXY", "VERIFIED_LAND_EFFORT_PROXY"} else 1.0
        ]
        columns[f"{stream}_DYNAMIC_CONTEXT_COVERAGE"] = [1.0]
    lineage = lineage_values(
        generation_id="fixture",
        config_hash="a" * 64,
        source_hashes={"fixture": "b" * 64},
        source_vintages={},
        knowledge_time_utc="2025-01-01T00:00:00+00:00",
    )
    result = _add_daily_v2_columns(pd.DataFrame(columns), lineage=lineage)
    assert result["LAND_EFFORT_PROXY_RAW"].item() == 0.0
    assert pd.isna(result["LAND_OBSERVATION_OPPORTUNITY_RAW"].item())
    assert result["LAND_OBSERVATION_OPPORTUNITY_STATE"].item() == "unmapped"
    assert pd.isna(result["PUBLIC_SHORE_ACCESS_MAPPED_STATIC_CONTEXT_FRACTION"].item())
    assert result["PUBLIC_SHORE_ACCESS_MAPPED_STATE"].item() == "unmapped"


def test_daylight_survives_missing_weather_and_has_independent_coverage() -> None:
    basis = _basis()
    basis.weather_bases.append({**basis.weather_bases[0], "weather_h3_r5": "missing"})
    basis.target_static["PHYSICAL_VIEWABILITY_STATIC_RAW"] *= 2
    basis.target_static["PHYSICAL_VIEWABILITY_CONTEXT_STATIC_SUPPORT"] *= 2
    result = compute_dynamic_chunk(load_land_reporting_config(), basis, basis.dates)
    np.testing.assert_allclose(result["DAYLIGHT_WEIGHT"], 0.5, rtol=1e-6, atol=1e-8)
    np.testing.assert_allclose(result["DAYLIGHT_COVERAGE"], 1.0)
    assert result["ATMOSPHERIC_VISIBILITY_COVERAGE"][0, 0] == 0.5
    assert result["ATMOSPHERIC_VISIBILITY_COVERAGE"][1, 0] == 0.0
    assert np.isnan(result["ATMOSPHERIC_VISIBILITY_WEIGHT"][1, 0])


def test_dynamic_chunks_and_region_order_are_invariant() -> None:
    cfg, basis = load_land_reporting_config(), _basis()
    whole = compute_dynamic_chunk(cfg, basis, basis.dates)
    for index, date in enumerate(basis.dates):
        single = compute_dynamic_chunk(cfg, basis, pd.DatetimeIndex([date]))
        for key in whole:
            if key != "DATE":
                np.testing.assert_allclose(
                    single[key][0], whole[key][index], rtol=1e-6, atol=1e-8, equal_nan=True
                )
