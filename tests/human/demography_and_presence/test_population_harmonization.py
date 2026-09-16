from __future__ import annotations

import pandas as pd
import pytest

from human.demography_and_presence.population.build import (
    harmonize_population_frames,
)
from human.demography_and_presence.population.context import (
    CONTEXT_SCOPE,
    build_population_context,
)


def test_cross_border_harmonization_uses_country_qualified_unique_key() -> None:
    shared = {
        "h3": ["same-cell"],
        "h3_resolution": [7],
        "distance_to_water_m": [0.0],
        "distance_to_water_miles": [0.0],
        "distance_to_water_capped_miles": [0.0],
        "water_distance_basis": ["centroid"],
        "source_dataset": ["fixture"],
        "allocation_method": ["area_weighted"],
        "crs_area": ["EPSG:5070"],
    }
    us = pd.DataFrame(
        {
            **shared,
            "state_abbr": ["WA"],
            "population_2020": [10.0],
            "population_2020_round": [10],
            "population_density_2020_per_km2": [2.0],
        }
    )
    canada = pd.DataFrame(
        {
            **shared,
            "province_abbr": ["BC"],
            "population_2021": [20.0],
            "population_2021_round": [20],
            "population_density_2021_per_km2": [3.0],
            "source_geography_level": ["DA"],
        }
    )
    output = harmonize_population_frames(us, canada)
    assert len(output) == 2
    assert not output.duplicated(["COUNTRY_CODE", "H3_INDEX"]).any()
    assert output.groupby("COUNTRY_CODE")["POPULATION"].sum().to_dict() == {
        "CA": 20.0,
        "US": 10.0,
    }

    context = build_population_context(output)
    assert len(context) == 1
    row = context.iloc[0]
    assert row["H3_INDEX"] == "same-cell"
    assert row["POPULATION"] == 30.0
    assert row["POPULATION_US_2020"] == 10.0
    assert row["POPULATION_CA_2021"] == 20.0
    assert row["COUNTRY_COUNT"] == 2
    assert row["COUNTRY_CODES"] == "CA|US"
    assert row["SUBDIVISION_CODES"] == "BC|WA"
    assert row["CENSUS_YEAR_MIN"] == 2020
    assert row["CENSUS_YEAR_MAX"] == 2021
    assert bool(row["CENSUS_VINTAGE_MIXED_QC"])
    assert bool(row["POPULATION_CONTEXT_AVAILABLE"])
    assert not bool(row["MARINE_TRANSFER_APPLIED"])
    assert row["CONTEXT_SCOPE"] == CONTEXT_SCOPE


def test_population_context_preserves_absent_country_as_null() -> None:
    frame = pd.DataFrame(
        {
            "H3_INDEX": ["us-only"],
            "H3_RESOLUTION": [7],
            "COUNTRY_CODE": ["US"],
            "SUBDIVISION_CODE": ["WA"],
            "CENSUS_YEAR": [2020],
            "POPULATION": [0.0],
        }
    )
    context = build_population_context(frame)
    row = context.iloc[0]
    assert row["POPULATION"] == 0.0
    assert row["POPULATION_LOG1P"] == 0.0
    assert row["POPULATION_US_2020"] == 0.0
    assert pd.isna(row["POPULATION_CA_2021"])
    assert not bool(row["CENSUS_VINTAGE_MIXED_QC"])


def test_population_context_rejects_duplicate_country_h3_keys() -> None:
    frame = pd.DataFrame(
        {
            "H3_INDEX": ["duplicate", "duplicate"],
            "H3_RESOLUTION": [7, 7],
            "COUNTRY_CODE": ["US", "US"],
            "SUBDIVISION_CODE": ["WA", "WA"],
            "CENSUS_YEAR": [2020, 2020],
            "POPULATION": [1.0, 2.0],
        }
    )
    with pytest.raises(ValueError, match="duplicate country/H3"):
        build_population_context(frame)
