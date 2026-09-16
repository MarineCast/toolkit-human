from __future__ import annotations

from datetime import date, timedelta

import h3
import polars as pl
import pytest
import yaml

from human.activity_and_effort.observer_effort.build import (
    _build_dynamic,
    build_parent_kernel,
)
from human.activity_and_effort.observer_effort.config import (
    load_observer_effort_config,
)


def test_parent_kernel_averages_over_modeled_r7_children(tmp_path) -> None:
    source_r6 = h3.latlng_to_cell(48.5, -123.0, 6)
    target_r6 = h3.latlng_to_cell(48.9, -123.0, 6)
    source_children = sorted(h3.cell_to_children(source_r6, 7))[:2]
    target_children = sorted(h3.cell_to_children(target_r6, 7))[:2]
    static_path = tmp_path / "static.parquet"
    pl.DataFrame(
        {
            "source_h3": [
                source_children[0],
                source_children[0],
                source_children[1],
                source_children[1],
            ],
            "target_h3": [
                target_children[0],
                target_children[1],
                target_children[0],
                target_children[1],
            ],
            "weight_terrain": [1.0] * 4,
            "weight_distance": [1.0] * 4,
            "weight_vegetation": [1.0] * 4,
            "weight_static_viewability": [1.0, 0.5, 0.5, 0.0],
        }
    ).write_parquet(static_path)

    kernel, metadata = build_parent_kernel(static_path)

    assert kernel.height == 1
    assert kernel.item(0, "WATER_STATIC_KERNEL_R6") == pytest.approx(0.5)
    assert kernel.item(0, "MODELED_SOURCE_R7_CHILD_COUNT") == 2
    assert kernel.item(0, "MODELED_TARGET_R7_CHILD_COUNT") == 2
    assert metadata["uniform_within_parent_assumption"]


def test_dynamic_product_applies_observer_activity_without_claiming_true_zero(
    tmp_path,
) -> None:
    source_r6 = h3.latlng_to_cell(48.5, -123.0, 6)
    target_r6 = h3.latlng_to_cell(48.9, -123.0, 6)
    unavailable_target_r6 = h3.latlng_to_cell(49.1, -123.0, 6)
    weekly_path = tmp_path / "ais_weekly.parquet"
    weeks = [date(2020, 1, 6) + timedelta(weeks=index) for index in range(104)]
    pl.DataFrame(
        {
            "WEEK_START": weeks,
            "H3_INDEX": [source_r6] * len(weeks),
            "H3_RESOLUTION": [6] * len(weeks),
            "OBSERVER_CAPABLE_VESSEL_HOURS_PROXY": [10] * len(weeks),
            "RECREATIONAL_VESSEL_HOURS_PROXY": [6] * len(weeks),
            "PASSENGER_VESSEL_HOURS_PROXY": [4] * len(weeks),
            "ACTIVE_VESSEL_HOURS_PROXY": [20] * len(weeks),
            "SOURCE_OBSERVED_HOURS": [168] * len(weeks),
            "SOURCE_EXPECTED_HOURS": [168] * len(weeks),
            "SOURCE_TEMPORAL_COVERAGE_FRACTION": [1.0] * len(weeks),
            "SOURCE_COVERAGE_COMPLETE": [False] * len(weeks),
            "SOURCE_COVERAGE_STATUS": ["partial_unknown_acquisition_coverage"] * len(weeks),
        }
    ).write_parquet(weekly_path)
    config_path = tmp_path / "observer_effort.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "product": "observer_effort",
                "category": "activity_and_effort",
                "source": {"source_completeness": "partial"},
                "parameters": {
                    "source_h3_resolution": 6,
                    "viewshed_h3_resolution": 7,
                    "output_h3_resolution": 6,
                    "primary_activity_column": "OBSERVER_CAPABLE_VESSEL_HOURS_PROXY",
                    "component_activity_columns": [
                        "RECREATIONAL_VESSEL_HOURS_PROXY",
                        "PASSENGER_VESSEL_HOURS_PROXY",
                        "ACTIVE_VESSEL_HOURS_PROXY",
                    ],
                    "scaling_quantile": 0.99,
                    "scaling_reference_weeks": 104,
                },
                "pipeline": {
                    "ais_weekly_path": str(weekly_path),
                    "ais_manifest_path": str(tmp_path / "ais_manifest.json"),
                    "water_static_weights_path": str(tmp_path / "static.parquet"),
                    "water_static_map_manifest_path": str(tmp_path / "static_manifest.json"),
                    "viewshed_config_path": str(tmp_path / "viewshed.yaml"),
                },
                "output": {
                    "weekly_path": str(tmp_path / "output.parquet"),
                    "metadata_path": str(tmp_path / "output.metadata.json"),
                    "manifest_path": str(tmp_path / "manifest.json"),
                },
                "inspection": {"report_path": str(tmp_path / "report.html")},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    cfg = load_observer_effort_config(config_path)
    kernel = pl.DataFrame(
        {
            "source_h3_r6": [source_r6, source_r6],
            "target_h3_r6": [target_r6, unavailable_target_r6],
            "WATER_STATIC_KERNEL_R6": [0.5, 0.0],
        }
    )

    frame, scaling = _build_dynamic(cfg, kernel)
    observed = frame.filter(pl.col("H3_INDEX") == target_r6)
    unavailable = frame.filter(pl.col("H3_INDEX") == unavailable_target_r6)

    assert frame.height == 208
    assert observed.item(0, "WATER_AIS_REPORTING_OPPORTUNITY_RAW") == pytest.approx(5.0)
    assert observed.item(0, "WATER_RECREATIONAL_VIEWABILITY_RAW") == pytest.approx(3.0)
    assert observed.item(0, "WATER_PASSENGER_VIEWABILITY_RAW") == pytest.approx(2.0)
    assert observed.item(0, "WATER_ALL_VESSEL_VIEWABILITY_RAW") == pytest.approx(10.0)
    assert observed.item(0, "WATER_AIS_REPORTING_OPPORTUNITY_INDEX") is None
    assert (
        observed.item(0, "WATER_VIEWABILITY_STATUS") == "derived_observed_activity_partial_coverage"
    )
    assert unavailable.item(0, "WATER_AIS_REPORTING_OPPORTUNITY_RAW") is None
    assert (
        unavailable.item(0, "WATER_VIEWABILITY_STATUS")
        == "unavailable_partial_spatial_or_acquisition_coverage"
    )
    assert unavailable.item(0, "MEASUREMENT_STATUS") == "unavailable"
    assert not observed.item(0, "SOURCE_COVERAGE_COMPLETE")
    assert scaling["complete_reference_weeks"] == 0
    assert scaling["index_available"] is False
