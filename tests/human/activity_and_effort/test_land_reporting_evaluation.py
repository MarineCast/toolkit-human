from __future__ import annotations

import pandas as pd
import pytest

from human.activity_and_effort.land_reporting_opportunity.evaluate import (
    PRIMARY_COLUMN,
    build_comparison,
    build_population_baseline,
    decile_table,
)


def test_population_baseline_preserves_unavailable_source_context() -> None:
    static = pd.DataFrame(
        {
            "source_h3": ["source-a", "source-b"],
            "target_h3": ["target-a", "target-a"],
            "weight_static_viewability": [0.5, 0.5],
        }
    )
    population = pd.DataFrame(
        {
            "H3_INDEX": ["source-a"],
            "POPULATION_LOG1P": [2.0],
            "POPULATION_CONTEXT_AVAILABLE": [True],
        }
    )

    target, cap = build_population_baseline(static, population)

    assert cap == pytest.approx(2.0)
    assert target.loc[0, "POPULATION_EXACT_CELL_RAW"] == pytest.approx(0.5)
    assert target.loc[0, "POPULATION_AVAILABLE_SOURCE_COUNT"] == 1
    assert target.loc[0, "POPULATION_CONTEXT_COVERAGE"] == pytest.approx(0.5)


def test_comparison_uses_zero_only_for_absent_report_records() -> None:
    target = pd.DataFrame(
        {
            "H3_INDEX": ["target-a", "target-b"],
            "LAND_STATIC_WEIGHT_SUM": [1.0, 1.0],
            PRIMARY_COLUMN: [0.5, 0.25],
            "MAPPED_PUBLIC_ACCESS_SUPPORTED_RAW": [0.2, None],
            "VERIFIED_PUBLIC_ACCESS_SUPPORTED_RAW": [0.1, None],
            "MAPPED_ACCESS_CONTEXT_FRACTION": [1.0, 1.0],
        }
    )
    population = pd.DataFrame(
        {
            "H3_INDEX": ["target-a", "target-b"],
            "POPULATION_EXACT_CELL_RAW": [0.4, None],
            "POPULATION_AVAILABLE_SOURCE_COUNT": [1, 0],
            "POPULATION_AVAILABLE_STATIC_WEIGHT": [1.0, 0.0],
            "POPULATION_TOTAL_STATIC_WEIGHT": [1.0, 1.0],
            "POPULATION_CONTEXT_COVERAGE": [1.0, 0.0],
        }
    )
    sightings = pd.DataFrame(
        {
            "H3_INDEX": ["target-a"],
            "REPORTED_SIGHTING_COUNT": [2],
            "REPORTED_SIGHTING_DAYS": [1],
        }
    )

    comparison = build_comparison(target, population, sightings).set_index("H3_INDEX")

    assert comparison.loc["target-b", "REPORTED_SIGHTING_COUNT"] == 0
    assert not comparison.loc["target-b", "HAS_REPORTED_SIGHTING"]
    assert pd.isna(comparison.loc["target-b", "POPULATION_EXACT_CELL_RAW"])
    assert pd.isna(comparison.loc["target-b", "MAPPED_PUBLIC_ACCESS_SUPPORTED_RAW"])


def test_deciles_keep_supported_zero_opportunity_separate() -> None:
    values = [0.0, *[float(value) for value in range(1, 21)]]
    comparison = pd.DataFrame(
        {
            "H3_INDEX": [f"target-{value}" for value in range(len(values))],
            PRIMARY_COLUMN: values,
            "REPORTED_SIGHTING_COUNT": [0] * len(values),
            "REPORTED_SIGHTING_DAYS": [0] * len(values),
            "HAS_REPORTED_SIGHTING": [False] * len(values),
            "MAPPED_ACCESS_CONTEXT_FRACTION": [1.0] * len(values),
        }
    )

    summary = decile_table(comparison)

    assert summary["PRESSURE_DECILE"].tolist() == list(range(11))
    assert summary.loc[summary["PRESSURE_DECILE"].eq(0), "TARGET_CELLS"].item() == 1
