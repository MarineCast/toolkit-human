from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
import yaml
from pydantic import ValidationError

from human.activity_and_effort.land_reporting_opportunity.config import (
    load_land_reporting_config,
)
from human.activity_and_effort.observation_opportunity_contract import (
    CONTROLLED_STATES,
    STATIC_PAIR_CANONICAL_MAPPING,
    ComponentState,
    add_lineage_polars,
    component_state,
    lineage_values,
    static_geometry_arrow_schema,
    static_pair_arrow_schema,
    validate_component_state_values,
    validate_component_table,
)
from human.activity_and_effort.water_observation_opportunity.config import (
    load_water_observation_config,
)


def test_static_pair_contract_makes_distance_semantics_explicit() -> None:
    schema = static_pair_arrow_schema()
    assert STATIC_PAIR_CANONICAL_MAPPING["weight_terrain"] == "TERRAIN_LOS_DISTANCE_SUPPORT"
    assert STATIC_PAIR_CANONICAL_MAPPING["weight_distance"].endswith("DIAGNOSTIC")
    assert b"must not be multiplied again" in schema.metadata[b"distance_semantics"]
    assert static_geometry_arrow_schema().metadata[b"schema_version"] == b"3.0.0-research"


def test_water_distance_sensitivity_is_explicit_and_uncalibrated() -> None:
    cfg = load_water_observation_config(resolve_generation=False)
    assert [item["label"] for item in cfg.distance_curve_sensitivity] == [
        "steeper_near_range",
        "configured_reference",
        "broader_range",
    ]
    assert all(item["selected_model"] == "logistic" for item in cfg.distance_curve_sensitivity)


@pytest.mark.parametrize(
    ("value", "complete", "derived", "expected"),
    [
        (2.0, True, True, "positive"),
        (0.0, True, True, "derived_zero"),
        (0.0, True, False, "observed_zero"),
        (2.0, False, True, "partial"),
        (None, False, True, "unknown"),
        (-1.0, True, True, "processing_failure"),
    ],
)
def test_component_state_never_converts_missingness_to_zero(
    value: float | None, complete: bool, derived: bool, expected: str
) -> None:
    assert component_state(value, coverage_complete=complete, derived=derived) == expected


def test_component_table_validates_lineage_and_states() -> None:
    lineage = lineage_values(
        generation_id="fixture",
        config_hash="a" * 64,
        source_hashes={"ais": "b" * 64},
        source_vintages={"ais": {"minimum": "2024-01-01"}},
        knowledge_time_utc="2025-01-01T00:00:00+00:00",
    )
    frame = add_lineage_polars(
        pl.DataFrame({"DATE": ["2024-01-01"], "H3_INDEX": ["cell"], "AIS_STATE": ["partial"]}),
        lineage,
    )
    validate_component_table(
        frame, keys=["DATE", "H3_INDEX"], component_state_columns=["AIS_STATE"]
    )
    assert set(CONTROLLED_STATES) == {state.value for state in ComponentState}
    assert json.loads(frame["SOURCE_HASHES_JSON"].item())["ais"] == "b" * 64


def test_component_table_rejects_unknown_state() -> None:
    frame = pl.DataFrame({"DATE": ["2024-01-01"], "STATE": ["not-a-state"]})
    with pytest.raises(ValueError, match="invalid component states"):
        validate_component_table(
            frame,
            keys=["DATE"],
            component_state_columns=["STATE"],
            required_lineage=False,
        )


@pytest.mark.parametrize(
    ("value", "state"),
    [(None, "positive"), (1.0, "source_unavailable"), (0.0, "positive"), (1.0, "derived_zero")],
)
def test_component_value_state_contradictions_are_rejected(value: float | None, state: str) -> None:
    with pytest.raises(ValueError, match="value-state contradictions"):
        validate_component_state_values(
            pl.DataFrame({"VALUE": [value], "STATE": [state]}),
            pairs=[("VALUE", "STATE")],
        )


@pytest.mark.parametrize(
    ("source", "loader", "section"),
    [
        (
            Path("config/data/human/activity_and_effort/land_reporting_opportunity.yaml"),
            load_land_reporting_config,
            "composite",
        ),
        (
            Path("config/data/human/activity_and_effort/water_observation_opportunity.yaml"),
            load_water_observation_config,
            "coverage",
        ),
    ],
)
def test_nested_observation_config_rejects_unknown_keys(
    tmp_path: Path, source: Path, loader, section: str
) -> None:
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw[section]["misspelled_contract_key"] = True
    candidate = tmp_path / source.name
    candidate.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises((ValidationError, ValueError), match="misspelled_contract_key"):
        loader(candidate, resolve_generation=False)
