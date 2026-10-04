from __future__ import annotations

from datetime import date

import h3
import numpy as np
import pandas as pd
import polars as pl
import pytest

from human.activity_and_effort.water_observation_opportunity.build import (
    _expand_ais_to_r7_sources,
)
from human.activity_and_effort.water_observation_opportunity.inspect import (
    _available_case_dominant_component,
)
from human.activity_and_effort.water_observation_opportunity.pipeline import (
    add_distance_bins,
    aggregate_weekly,
    apply_coverage_semantics,
    condition_arrays,
    distance_visibility_weight,
    kernel_basis,
    propagate_component,
    static_distance_summaries,
)


def _basis():
    source = h3.latlng_to_cell(48.5, -123.0, 6)
    near = h3.latlng_to_cell(48.51, -123.0, 6)
    far = h3.latlng_to_cell(48.7, -123.0, 6)
    kernel = add_distance_bins(
        pl.DataFrame(
            {
                "source": [source, source],
                "target": [near, far],
                "weight": [1.0, 1.0],
            }
        ),
        source_column="source",
        target_column="target",
        distance_bin_km=0.5,
    )
    return kernel_basis(
        kernel,
        source_column="source",
        target_column="target",
        weight_column="weight",
        distance_bin_km=0.5,
    )


def test_visibility_is_distance_aware_and_conditions_remain_separate() -> None:
    basis = _basis()
    conditions = condition_arrays(
        visibility_km=np.asarray([[5.0]]),
        daylight_fraction=np.asarray([[0.5]]),
        wind_speed_ms=np.asarray([[5.5]]),
        precipitation_mm_day=np.asarray([[0.0]]),
        weather_complete=np.asarray([[True]]),
        wind_midpoint_ms=5.5,
        wind_slope_ms=1.5,
        precipitation_half_mm_day=10.0,
    )
    result = propagate_component(
        np.asarray([[10.0]]),
        conditions,
        basis,
        visibility_transition_fraction=0.2,
        visibility_minimum_transition_km=1.0,
        wind_exponent=0.7,
        precipitation_exponent=0.3,
    )

    assert conditions["daylight_weight"].item() == 0.5
    assert result[0, 0] > result[0, 1]
    assert result[0, 0] > 0.0


def test_visibility_response_handles_zero_and_missing_without_neutral_fill() -> None:
    weights = distance_visibility_weight(
        np.asarray([[0.0, np.nan, 10.0]]),
        5.0,
        transition_fraction=0.2,
        minimum_transition_km=1.0,
    )
    assert weights[0, 0] == 0.0
    assert np.isnan(weights[0, 1])
    assert weights[0, 2] > 0.5


def test_partial_coverage_keeps_positive_but_never_claims_zero() -> None:
    values, states = apply_coverage_semantics(
        np.asarray([[3.0, 0.0], [0.0, 2.0]]),
        np.asarray([False, True]),
        derived=True,
    )
    assert values[0, 0] == 3.0
    assert states[0, 0] == "partial"
    assert np.isnan(values[0, 1])
    assert states[0, 1] == "unknown"
    assert values[1, 0] == 0.0
    assert states[1, 0] == "derived_zero"
    assert states[1, 1] == "positive"


def test_weekly_aggregation_sums_activity_and_preserves_unavailable_states() -> None:
    daily = pl.DataFrame(
        {
            "DATE": [date(2024, 1, 1), date(2024, 1, 2)],
            "H3_INDEX": ["cell", "cell"],
            "H3_RESOLUTION": [6, 6],
            "COMPONENT_RAW": [1.0, None],
            "PHYSICAL_VIEWABILITY_RAW": [0.75, 0.75],
            "CONDITION_WEIGHT": [0.4, 0.6],
            "COMPONENT_STATE": ["partial", "unknown"],
            "SEA_STATE_STATE": ["source_unavailable", "source_unavailable"],
            "PRIMARY_WATER_COMPOSITE_STATE": ["not_applicable", "not_applicable"],
        }
    ).lazy()
    weekly = aggregate_weekly(
        daily,
        additive_columns=["COMPONENT_RAW"],
        mean_columns=["PHYSICAL_VIEWABILITY_RAW", "CONDITION_WEIGHT"],
        state_columns=[
            "COMPONENT_STATE",
            "SEA_STATE_STATE",
            "PRIMARY_WATER_COMPOSITE_STATE",
        ],
    )
    assert weekly["COMPONENT_RAW"].item() == pytest.approx(1.0)
    assert weekly["COMPONENT_AVAILABLE_DAY_COUNT"].item() == 1
    assert weekly["CONDITION_WEIGHT"].item() == pytest.approx(0.5)
    assert weekly["PHYSICAL_VIEWABILITY_RAW"].item() == pytest.approx(0.75)
    assert weekly["COMPONENT_STATE"].item() == "partial"
    assert weekly["SEA_STATE_STATE"].item() == "source_unavailable"
    assert weekly["PRIMARY_WATER_COMPOSITE_STATE"].item() == "not_applicable"
    assert weekly["INCOMPLETE_WEEK"].item()


def test_incomplete_week_is_explicit() -> None:
    daily = pl.DataFrame(
        {
            "DATE": [date(2024, 1, 1), date(2024, 1, 2)],
            "H3_INDEX": ["cell", "cell"],
            "H3_RESOLUTION": [6, 6],
            "COMPONENT_RAW": [1.0, 1.0],
            "COMPONENT_STATE": ["positive", "positive"],
        }
    ).lazy()
    weekly = aggregate_weekly(
        daily,
        additive_columns=["COMPONENT_RAW"],
        mean_columns=[],
        state_columns=["COMPONENT_STATE"],
    )
    assert weekly["PERIOD_DAY_COUNT"].item() == 2
    assert weekly["INCOMPLETE_WEEK"].item()


def test_distance_summary_does_not_multiply_distance_into_static_kernel() -> None:
    basis = _basis()
    result = static_distance_summaries(
        basis,
        distance_detection_weights=np.linspace(1.0, 0.0, len(basis.distance_km)),
    )
    assert np.isfinite(result["DISTANCE_KM_MEAN"]).all()
    assert np.ptp(result["DISTANCE_KM_MEAN"]) > 10.0
    assert np.all((result["DISTANCE_DETECTION_WEIGHT"] >= 0.0))
    assert np.all((result["DISTANCE_DETECTION_WEIGHT"] <= 1.0))
    assert np.array_equal(basis.static_target_support, np.ones(2))


def test_r6_ais_is_conserved_when_distributed_to_modeled_r7_sources() -> None:
    parent = h3.latlng_to_cell(48.5, -123.0, 6)
    children = pd.Index(sorted(h3.cell_to_children(parent, 7))[:3])
    expanded = _expand_ais_to_r7_sources(
        {"ACTIVITY": np.asarray([[12.0], [3.0]])},
        r6_sources=pd.Index([parent]),
        r7_sources=children,
    )["ACTIVITY"]
    assert expanded.shape == (2, 3)
    assert expanded.sum(axis=1).tolist() == pytest.approx([12.0, 3.0])


def test_dominance_excludes_all_missing_available_cases() -> None:
    result = _available_case_dominant_component(
        pd.DataFrame({"one": [None, 2.0], "two": [None, 1.0]})
    )
    assert pd.isna(result.iloc[0])
    assert result.iloc[1] == "one"


def test_weekly_builder_deduplicates_overlapping_coverage_selectors(tmp_path):
    """Dynamic coverage is both a condition field and a *_COVERAGE field."""
    from datetime import date
    from types import SimpleNamespace
    from human.activity_and_effort.water_observation_opportunity.build import _build_weekly
    cfg = SimpleNamespace(source_daily_path=tmp_path / 'source.parquet',
                          target_daily_path=tmp_path / 'target.parquet',
                          source_weekly_path=tmp_path / 'source-week.parquet',
                          target_weekly_path=tmp_path / 'target-week.parquet')
    daily = pl.DataFrame({'DATE': [date(2024, 1, 1), date(2024, 1, 2)],
                          'H3_INDEX': ['8628d1067ffffff'] * 2,
                          'H3_RESOLUTION': [6, 6],
                          'DYNAMIC_CONDITION_COVERAGE': [0.25, 0.75]})
    daily.write_parquet(cfg.source_daily_path)
    daily.write_parquet(cfg.target_daily_path)
    assert _build_weekly(cfg) == (1, 1)
    result = pl.read_parquet(cfg.target_weekly_path)
    assert result['DYNAMIC_CONDITION_COVERAGE'].to_list() == [0.5]
    assert result['PERIOD_DAY_COUNT'].to_list() == [2]


def test_diagnostics_select_pinned_land_generation_without_workspace_defaults(tmp_path, monkeypatch):
    import json
    from human.activity_and_effort.water_observation_opportunity.diagnostics import _land_daily_for_join, _land_member
    from human.utils.artifacts import sha256_file
    daily = tmp_path / 'selected-daily.parquet'
    pl.DataFrame({'PHYSICAL_VIEWABILITY_RAW': [1.0], 'PHYSICAL_VIEWABILITY_STATE': ['positive']}).write_parquet(daily)
    item = {'dataset_id': 'human.activity_and_effort.land_reporting_opportunity.daily_h3_r6',
            'path': str(daily), 'sha256': sha256_file(daily)}
    manifest = tmp_path / 'manifest.json'
    payload = dict(manifest_schema_version=1, product='human.activity_and_effort.land_reporting_opportunity',
                   stage='build', run_id='fixture', build_time_utc='2026-01-01T00:00:00Z',
                   config_path='synthetic', config_hash='fixture', resolved_config={}, sources=[],
                   inputs=[dict(item, dataset_id='input.land_static_weights')], artifacts=[item],
                   h3_resolution=6, spatial_bounds_wgs84={}, temporal_coverage={}, source_completeness='partial',
                   measurement_statuses=['derived'], attribution=[], licenses=[], known_limitations=['Synthetic fixture'])
    manifest.write_text(json.dumps(payload))
    monkeypatch.chdir(tmp_path)
    selected, mapping, states = _land_daily_for_join(manifest)
    assert selected == daily
    assert mapping['LAND_PHYSICAL_VIEWABILITY_RAW'] == 'PHYSICAL_VIEWABILITY_RAW'
    assert states['LAND_PHYSICAL_VIEWABILITY_STATE'] == 'PHYSICAL_VIEWABILITY_STATE'
    payload['inputs'][0]['sha256'] = '0' * 64
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='checksum'):
        _land_member(manifest, 'inputs', 'input.land_static_weights')
