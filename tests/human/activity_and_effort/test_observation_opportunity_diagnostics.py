from __future__ import annotations

import pandas as pd
import pytest

from human.activity_and_effort.water_observation_opportunity.diagnostics import (
    apply_platform_pathways,
    classify_observation_platforms,
    join_sighting_components,
    within_calipers,
)


def test_platform_classification_uses_structured_fields_and_keeps_uncertainty() -> None:
    source = pd.DataFrame(
        {
            "SOURCE_RECORD_ID": ["shore", "ferry", "free_text", "vessel"],
            "SOURCE_PAYLOAD": [
                '{"observation_platform":"shore"}',
                '{"platform_type":"ferry"}',
                '{"comments":"seen from a boat"}',
                '{"vessel_type":"research vessel"}',
            ],
        }
    )
    observations = pd.DataFrame(
        {
            "SOURCE_RECORD_IDS": [
                ["shore"],
                ["ferry"],
                ["free_text"],
                ["vessel"],
                ["shore", "vessel"],
            ]
        }
    )
    assert classify_observation_platforms(observations, source).tolist() == [
        "shore",
        "ferry",
        "unknown",
        "vessel",
        "unknown",
    ]


def test_sighting_date_h3_join_retains_all_positive_rows_and_both_pathways() -> None:
    observations = pd.DataFrame(
        {
            "OBSERVATION_ID": ["one", "two"],
            "DATE": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "H3_INDEX": ["cell", "outside"],
            "PLATFORM": ["unknown", "unknown"],
            "LAND_PATHWAY_EVALUATED": [True, True],
            "WATER_PATHWAY_EVALUATED": [True, True],
        }
    )
    water = pd.DataFrame(
        {
            "DATE": pd.to_datetime(["2024-01-01"]),
            "H3_INDEX": ["cell"],
            "WATER_COMPONENT": [0.4],
        }
    )
    land = pd.DataFrame(
        {
            "DATE": pd.to_datetime(["2024-01-01"]),
            "H3_INDEX": ["cell"],
            "LAND_COMPONENT": [0.2],
        }
    )
    result = join_sighting_components(observations, water, land)
    assert result["OBSERVATION_ID"].tolist() == ["one", "two"]
    assert result.loc[0, "WATER_COMPONENT"] == 0.4
    assert pd.isna(result.loc[1, "LAND_COMPONENT"])
    assert result[["LAND_PATHWAY_EVALUATED", "WATER_PATHWAY_EVALUATED"]].all().all()
    assert result["WATER_JOIN_COVERAGE_REASON"].tolist() == [
        "matched",
        "outside_temporal_coverage",
    ]
    assert result["LAND_JOIN_COVERAGE_REASON"].tolist() == [
        "matched",
        "outside_temporal_coverage",
    ]


def test_sighting_join_distinguishes_temporal_spatial_and_upstream_gaps() -> None:
    observations = pd.DataFrame(
        {
            "DATE": pd.to_datetime(
                ["2023-12-31", "2024-01-01", "2024-01-01", "not-a-date"],
                errors="coerce",
            ),
            "H3_INDEX": ["cell", "outside", "cell", "cell"],
        }
    )
    components = pd.DataFrame(
        {
            "DATE": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "H3_INDEX": ["other", "cell"],
            "COMPONENT": [1.0, 2.0],
        }
    )
    result = join_sighting_components(observations, components, components)
    assert result["WATER_JOIN_COVERAGE_REASON"].tolist() == [
        "outside_temporal_coverage",
        "outside_spatial_support",
        "upstream_unavailable",
        "processing_failure",
    ]


def test_sighting_join_rejects_duplicate_component_keys() -> None:
    observations = pd.DataFrame({"DATE": pd.to_datetime(["2024-01-01"]), "H3_INDEX": ["cell"]})
    duplicate = pd.DataFrame(
        {
            "DATE": pd.to_datetime(["2024-01-01", "2024-01-01"]),
            "H3_INDEX": ["cell", "cell"],
        }
    )
    with pytest.raises(ValueError, match="not unique"):
        join_sighting_components(observations, duplicate, duplicate.iloc[:1])


def test_platform_pathways_mask_nonmatching_values_and_preserve_unknown_platform() -> None:
    detail = pd.DataFrame(
        {
            "PLATFORM": ["shore", "vessel", "unknown"],
            "LAND_PATHWAY_EVALUATED": [True, False, True],
            "WATER_PATHWAY_EVALUATED": [False, True, True],
            "LAND_OBSERVATION_OPPORTUNITY_RAW": [0.2, 0.3, None],
            "LAND_OBSERVATION_OPPORTUNITY_SPATIAL_RANK": [0.4, 0.5, None],
            "LAND_OBSERVATION_OPPORTUNITY_STATE": ["positive", "positive", None],
            "WATER_ALL_VESSEL_AIS_OBSERVATION_OPPORTUNITY_RAW": [0.6, 0.7, None],
            "WATER_ALL_VESSEL_AIS_OBSERVATION_OPPORTUNITY_SPATIAL_RANK": [0.8, 0.9, None],
            "WATER_ALL_VESSEL_AIS_OBSERVATION_OPPORTUNITY_STATE": [
                "positive",
                "positive",
                None,
            ],
        }
    )
    result = apply_platform_pathways(
        detail,
        component_states={
            "LAND_OBSERVATION_OPPORTUNITY_RAW": "LAND_OBSERVATION_OPPORTUNITY_STATE",
            "WATER_ALL_VESSEL_AIS_OBSERVATION_OPPORTUNITY_RAW": (
                "WATER_ALL_VESSEL_AIS_OBSERVATION_OPPORTUNITY_STATE"
            ),
        },
    )

    assert pd.isna(result.loc[0, "WATER_ALL_VESSEL_AIS_OBSERVATION_OPPORTUNITY_RAW"])
    assert result.loc[0, "WATER_ALL_VESSEL_AIS_OBSERVATION_OPPORTUNITY_STATE"] == ("not_applicable")
    assert pd.isna(result.loc[1, "LAND_OBSERVATION_OPPORTUNITY_RAW"])
    assert result.loc[1, "LAND_OBSERVATION_OPPORTUNITY_STATE"] == "not_applicable"
    assert result.loc[2, "LAND_OBSERVATION_OPPORTUNITY_STATE"] == "unmapped"
    assert result.loc[2, "WATER_ALL_VESSEL_AIS_OBSERVATION_OPPORTUNITY_STATE"] == "unmapped"


def test_border_calipers_require_every_configured_covariate() -> None:
    left = pd.Series({"_Z_population": 0.0, "_Z_road": 0.0})
    accepted, differences = within_calipers(
        left,
        pd.Series({"_Z_population": 0.2, "_Z_road": 0.3}),
        covariates=["population", "road"],
        calipers={"population": 0.25, "road": 0.35},
    )
    rejected, _ = within_calipers(
        left,
        pd.Series({"_Z_population": 0.2, "_Z_road": 0.4}),
        covariates=["population", "road"],
        calipers={"population": 0.25, "road": 0.35},
    )
    assert accepted
    assert differences == {"population": 0.2, "road": 0.3}
    assert not rejected
