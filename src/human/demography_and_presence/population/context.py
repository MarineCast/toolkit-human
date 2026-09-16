"""Build H3-unique population context without implying observer access."""

from __future__ import annotations

import numpy as np
import pandas as pd

from human.utils.artifacts import MeasurementStatus

CONTEXT_SCOPE = "land_h3_population_no_access_or_marine_transfer"
COUNTRY_POPULATION_COLUMNS = {
    "US": "POPULATION_US_2020",
    "CA": "POPULATION_CA_2021",
}
EXPECTED_CENSUS_YEARS = {"US": 2020, "CA": 2021}
POPULATION_CONTEXT_COLUMNS = (
    "H3_INDEX",
    "H3_RESOLUTION",
    "POPULATION",
    "POPULATION_LOG1P",
    "POPULATION_US_2020",
    "POPULATION_CA_2021",
    "COUNTRY_COUNT",
    "COUNTRY_CODES",
    "SUBDIVISION_CODES",
    "CENSUS_YEAR_MIN",
    "CENSUS_YEAR_MAX",
    "CENSUS_VINTAGE_MIXED_QC",
    "POPULATION_CONTEXT_AVAILABLE",
    "MARINE_TRANSFER_APPLIED",
    "CONTEXT_SCOPE",
    "MEASUREMENT_STATUS",
)


def _joined_unique(values: pd.Series) -> str:
    return "|".join(sorted({str(value).strip() for value in values.dropna() if str(value).strip()}))


def _validate_cross_border_input(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "H3_INDEX",
        "H3_RESOLUTION",
        "COUNTRY_CODE",
        "SUBDIVISION_CODE",
        "CENSUS_YEAR",
        "POPULATION",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Cross-border population is missing context columns: {missing}")
    if frame.empty:
        raise ValueError("Cross-border population is empty.")

    output = frame.copy()
    output["H3_INDEX"] = output["H3_INDEX"].astype("string").str.strip()
    output["COUNTRY_CODE"] = output["COUNTRY_CODE"].astype("string").str.upper().str.strip()
    output["H3_RESOLUTION"] = pd.to_numeric(output["H3_RESOLUTION"], errors="coerce")
    output["CENSUS_YEAR"] = pd.to_numeric(output["CENSUS_YEAR"], errors="coerce")
    output["POPULATION"] = pd.to_numeric(output["POPULATION"], errors="coerce")

    if output["H3_INDEX"].isna().any() or output["H3_INDEX"].eq("").any():
        raise ValueError("Cross-border population contains null or empty H3 indices.")
    if output.duplicated(["COUNTRY_CODE", "H3_INDEX"]).any():
        raise ValueError("Cross-border population contains duplicate country/H3 keys.")
    if output["H3_RESOLUTION"].isna().any() or set(output["H3_RESOLUTION"].unique()) != {7}:
        raise ValueError("Population context requires H3 resolution 7.")
    population = output["POPULATION"].to_numpy(dtype=float)
    if not np.isfinite(population).all() or (population < 0).any():
        raise ValueError("Cross-border POPULATION must be finite, non-null, and non-negative.")

    countries = set(output["COUNTRY_CODE"].dropna().astype(str))
    unsupported = sorted(countries - set(COUNTRY_POPULATION_COLUMNS))
    if unsupported:
        raise ValueError(f"Population context contains unsupported country codes: {unsupported}")
    if output["COUNTRY_CODE"].isna().any():
        raise ValueError("Cross-border population contains null country codes.")

    expected_year = output["COUNTRY_CODE"].map(EXPECTED_CENSUS_YEARS)
    invalid_year = output["CENSUS_YEAR"].isna() | output["CENSUS_YEAR"].ne(expected_year)
    if invalid_year.any():
        values = (
            output.loc[invalid_year, ["COUNTRY_CODE", "CENSUS_YEAR"]]
            .drop_duplicates()
            .to_dict("records")
        )
        raise ValueError(f"Population context census vintages do not match the schema: {values}")
    return output


def build_population_context(cross_border: pd.DataFrame) -> pd.DataFrame:
    """Collapse country-qualified population rows to one context row per H3 cell.

    Country-specific population remains nullable outside that country's published
    geography. Only the total across rows that are present is additive. No absent
    country row is converted to zero, and no access, travel, viewshed, or marine
    transfer weighting is applied here.
    """

    frame = _validate_cross_border_input(cross_border)
    grouped = frame.groupby("H3_INDEX", sort=True, observed=True)
    context = grouped.agg(
        H3_RESOLUTION=("H3_RESOLUTION", "first"),
        POPULATION=("POPULATION", "sum"),
        COUNTRY_COUNT=("COUNTRY_CODE", "nunique"),
        CENSUS_YEAR_MIN=("CENSUS_YEAR", "min"),
        CENSUS_YEAR_MAX=("CENSUS_YEAR", "max"),
    ).reset_index()

    country_codes = grouped["COUNTRY_CODE"].agg(_joined_unique).rename("COUNTRY_CODES")
    subdivision_codes = grouped["SUBDIVISION_CODE"].agg(_joined_unique).rename("SUBDIVISION_CODES")
    context = context.merge(country_codes, on="H3_INDEX", validate="one_to_one")
    context = context.merge(subdivision_codes, on="H3_INDEX", validate="one_to_one")

    for country, column in COUNTRY_POPULATION_COLUMNS.items():
        values = (
            frame.loc[frame["COUNTRY_CODE"].eq(country), ["H3_INDEX", "POPULATION"]]
            .rename(columns={"POPULATION": column})
            .copy()
        )
        context = context.merge(values, on="H3_INDEX", how="left", validate="one_to_one")

    context["POPULATION_LOG1P"] = np.log1p(context["POPULATION"].astype(float))
    context["CENSUS_VINTAGE_MIXED_QC"] = context["CENSUS_YEAR_MIN"].ne(context["CENSUS_YEAR_MAX"])
    context["POPULATION_CONTEXT_AVAILABLE"] = True
    context["MARINE_TRANSFER_APPLIED"] = False
    context["CONTEXT_SCOPE"] = CONTEXT_SCOPE
    context["MEASUREMENT_STATUS"] = MeasurementStatus.DERIVED.value

    context["H3_RESOLUTION"] = context["H3_RESOLUTION"].astype("int64")
    context["COUNTRY_COUNT"] = context["COUNTRY_COUNT"].astype("int64")
    context["CENSUS_YEAR_MIN"] = context["CENSUS_YEAR_MIN"].astype("int64")
    context["CENSUS_YEAR_MAX"] = context["CENSUS_YEAR_MAX"].astype("int64")
    context = (
        context[list(POPULATION_CONTEXT_COLUMNS)].sort_values("H3_INDEX").reset_index(drop=True)
    )

    if context["H3_INDEX"].duplicated().any():
        raise ValueError("Population context contains duplicate H3 keys.")
    if not np.isclose(
        float(context["POPULATION"].sum()),
        float(frame["POPULATION"].sum()),
        rtol=1e-12,
        atol=1e-8,
    ):
        raise ValueError("Population context does not conserve cross-border population.")
    return context
