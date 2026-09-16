"""Tests for official Statistics Canada population inputs."""

from __future__ import annotations

import zipfile

import pandas as pd
import pytest

from human.demography_and_presence.population.canada.statcan import (
    load_canada_population,
    read_population_table,
)
from human.demography_and_presence.population.common.exceptions import (
    PopulationDataError,
)


def test_read_population_table_streams_da_population_from_profile_zip(tmp_path) -> None:
    archive_path = tmp_path / "profile.zip"
    source = pd.DataFrame(
        {
            "DGUID": [
                "2021A000259",
                "2021S051259010124",
                "2021S051259010124",
                "2021S051259010125",
            ],
            "GEO_LEVEL": [
                "Province",
                "Dissemination area",
                "Dissemination area",
                "Dissemination area",
            ],
            "CHARACTERISTIC_ID": [1, 1, 2, 1],
            "C1_COUNT_TOTAL": [5_000_879, 712, 700, None],
        }
    )
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "98-401-X2021006_English_CSV_data_BritishColumbia.csv",
            source.to_csv(index=False),
        )
        archive.writestr("98-401-X2021006_Geo_starting_row.CSV", "ignored")

    result = read_population_table(archive_path)

    assert result["DGUID"].tolist() == ["2021S051259010124", "2021S051259010125"]
    assert float(result["Population, 2021"].tolist()[0]) == 712
    assert pd.isna(result["Population, 2021"].tolist()[1])

    available, join_column, population_column = load_canada_population(
        archive_path,
        ["DGUID"],
        ["Population, 2021"],
        allow_missing_population=True,
    )
    assert join_column == "DGUID"
    assert population_column == "Population, 2021"
    assert available["DGUID"].tolist() == ["2021S051259010124"]
    assert available.attrs["unavailable_geo_ids"] == ["2021S051259010125"]


def test_local_population_does_not_reclassify_malformed_count_as_unavailable(
    tmp_path,
) -> None:
    path = tmp_path / "population.csv"
    pd.DataFrame({"DGUID": ["2021S051259010124"], "Population, 2021": ["not-a-count"]}).to_csv(
        path, index=False
    )

    with pytest.raises(PopulationDataError, match="invalid population"):
        load_canada_population(
            path,
            ["DGUID"],
            ["Population, 2021"],
            allow_missing_population=True,
        )
