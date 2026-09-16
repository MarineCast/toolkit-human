"""Statistics Canada 2021 Census geography and population helpers.

Dissemination Areas are used as the source geography for area-weighted H3
allocation. Census Profile population counts may be subject to Statistics
Canada random rounding, so small-area outputs should not be interpreted as
unrounded micro-counts.
"""

from __future__ import annotations

import logging
import time
import zipfile
from io import StringIO
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from ..common.exceptions import PopulationDataError, PopulationInputError
from ..common.geo import repair_geometries
from ..common.io import atomic_write_parquet, download_file, read_vector
from .config import CanadaPopulationConfig

LOGGER = logging.getLogger(__name__)

STATCAN_PROFILE_REQUIRED_COLUMNS = (
    "DGUID",
    "GEO_LEVEL",
    "CHARACTERISTIC_ID",
    "C1_COUNT_TOTAL",
)
STATCAN_UNAVAILABLE_FILENAME = "bc_da_population_2021_unavailable.parquet"


class MissingCanadaInputError(FileNotFoundError, PopulationInputError):
    """Raised when Canada source inputs are not configured or unavailable."""


class CanadaApiError(PopulationDataError):
    """Raised when the official Statistics Canada Census Profile API fails."""


def read_population_table(path: str | Path) -> pd.DataFrame:
    """Read a configured Canada population table from CSV or Parquet."""
    resolved = Path(path).expanduser().resolve()
    suffix = resolved.suffix.lower()
    if suffix == ".zip":
        return _read_statcan_profile_zip(resolved)
    if suffix == ".csv":
        return pd.read_csv(resolved)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(resolved)
    raise ValueError(f"Unsupported population table format: {resolved}")


def _read_statcan_profile_zip(path: Path) -> pd.DataFrame:
    """Stream DA population counts from an official comprehensive Profile ZIP."""
    with zipfile.ZipFile(path) as archive:
        members = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".csv") and "_csv_data_" in name.lower()
        ]
        if len(members) != 1:
            raise PopulationDataError(
                "Statistics Canada Census Profile ZIP must contain exactly one data CSV; "
                f"found {members}."
            )
        frames: list[pd.DataFrame] = []
        with archive.open(members[0]) as source:
            chunks = pd.read_csv(
                source,
                usecols=list(STATCAN_PROFILE_REQUIRED_COLUMNS),
                dtype="string",
                encoding="latin-1",
                chunksize=250_000,
            )
            for chunk in chunks:
                characteristic_ids = pd.to_numeric(chunk["CHARACTERISTIC_ID"], errors="coerce")
                selected = chunk[
                    chunk["GEO_LEVEL"].eq("Dissemination area") & characteristic_ids.eq(1)
                ][["DGUID", "C1_COUNT_TOTAL"]]
                if not selected.empty:
                    frames.append(selected)
    if not frames:
        raise PopulationDataError(
            f"Statistics Canada Census Profile ZIP contains no DA population rows: {path}"
        )
    return pd.concat(frames, ignore_index=True).rename(
        columns={"C1_COUNT_TOTAL": "Population, 2021"}
    )


def first_existing_column(columns: list[str], candidates: list[str], label: str) -> str:
    """Return the first candidate column found, case-insensitively."""
    lower_to_original = {column.lower(): column for column in columns}
    for candidate in candidates:
        if candidate in columns:
            return candidate
        match = lower_to_original.get(candidate.lower())
        if match:
            return match
    raise ValueError(f"Could not find {label} column. Tried {candidates}. Available: {columns}")


def local_or_download_path(
    configured_path: str | Path | None,
    download_url: str | None,
    raw_dir: Path,
    filename: str,
    overwrite: bool = False,
) -> Path:
    """Resolve a local file or download a configured official file URL."""
    if configured_path:
        path = Path(configured_path).expanduser().resolve()
        if not path.exists():
            raise MissingCanadaInputError(f"Configured Canada input does not exist: {path}")
        return path
    if download_url:
        return download_file(download_url, raw_dir / filename, overwrite=overwrite)
    raise MissingCanadaInputError(
        "Canada StatsCan boundary geometry is not configured. Provide an official "
        "Statistics Canada 2021 DA boundary file via canada.geography_path, or set "
        "canada.geography_download_url."
    )


def load_canada_geography(
    path: Path,
    province_code: str,
    join_column_candidates: list[str],
    area_crs: str,
    dguid_prefix: str = "2021S0512",
) -> tuple[gpd.GeoDataFrame, str]:
    """Load official census geography and filter it to British Columbia."""
    geography = read_vector(path)
    if geography.crs is None:
        raise ValueError(f"Canada geography has no CRS: {path}")
    geography = repair_geometries(geography.to_crs(area_crs))
    join_column = first_existing_column(
        list(geography.columns),
        join_column_candidates,
        "geography join",
    )
    geography[join_column] = geography[join_column].astype(str).str.strip()

    province_columns = [
        column for column in ("PRUID", "PRCODE", "province_code") if column in geography.columns
    ]
    if province_columns:
        province_column = province_columns[0]
        geography = geography[
            geography[province_column].astype(str).str.zfill(2) == province_code
        ].copy()
    elif join_column.upper() == "DAUID":
        geography = geography[
            geography[join_column].astype(str).str.startswith(province_code, na=False)
        ].copy()
    elif join_column.upper() == "DGUID":
        expected_prefix = f"{dguid_prefix}{province_code}"
        geography = geography[
            geography[join_column].astype(str).str.startswith(expected_prefix, na=False)
        ].copy()
    else:
        raise PopulationDataError(
            "Canada geography has no province-code column and its join column is not "
            "DAUID or DGUID, so the source cannot be safely scoped to British Columbia."
        )

    if geography.empty:
        raise ValueError(f"No Canada geography rows remain for province code {province_code}.")
    if geography[join_column].duplicated().any():
        duplicate_count = int(geography[join_column].duplicated().sum())
        raise PopulationDataError(
            f"Canada geography contains {duplicate_count} duplicate join IDs in {join_column}."
        )
    return geography, join_column


def _coerce_population_values(values: pd.Series, *, context: str) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    invalid = numeric.isna()
    if invalid.any():
        examples = values.loc[invalid].astype(str).head(10).tolist()
        raise PopulationDataError(
            f"{context} contains {int(invalid.sum())} invalid population values. "
            f"Examples: {examples}"
        )
    numeric_array = numeric.to_numpy(dtype="float64")
    if not np.isfinite(numeric_array).all():
        raise PopulationDataError(f"{context} contains non-finite population values.")
    if (numeric < 0).any():
        raise PopulationDataError(f"{context} contains negative population values.")
    fractional = ~np.isclose(numeric_array % 1, 0.0, atol=1e-9)
    if fractional.any():
        examples = values.iloc[np.flatnonzero(fractional)[:10]].astype(str).tolist()
        raise PopulationDataError(
            f"{context} contains non-integer population counts. Examples: {examples}"
        )
    return numeric.astype("int64")


def _consolidate_population(frame: pd.DataFrame, *, context: str) -> pd.DataFrame:
    required = {"geo_id", "population_2021"}
    missing = required - set(frame.columns)
    if missing:
        raise PopulationDataError(f"{context} is missing columns: {sorted(missing)}")
    normalized = frame[["geo_id", "population_2021"]].copy()
    normalized["geo_id"] = normalized["geo_id"].astype(str).str.strip()
    if normalized["geo_id"].eq("").any():
        raise PopulationDataError(f"{context} contains blank geography IDs.")
    normalized["population_2021"] = _coerce_population_values(
        normalized["population_2021"],
        context=context,
    )
    conflicts = (
        normalized.groupby("geo_id")["population_2021"].nunique().loc[lambda values: values > 1]
    )
    if not conflicts.empty:
        raise PopulationDataError(
            f"{context} contains conflicting values for {len(conflicts)} geography IDs. "
            f"Examples: {conflicts.index[:10].tolist()}"
        )
    return normalized.drop_duplicates("geo_id", keep="first").reset_index(drop=True)


def load_canada_population(
    path: Path,
    join_column_candidates: list[str],
    population_column_candidates: list[str],
    *,
    allow_missing_population: bool = False,
) -> tuple[pd.DataFrame, str, str]:
    """Load a local population table and identify strict join/population columns."""
    population = read_population_table(path)
    join_column = first_existing_column(
        list(population.columns),
        join_column_candidates,
        "population join",
    )
    population_column = first_existing_column(
        list(population.columns),
        population_column_candidates,
        "population",
    )
    population[join_column] = population[join_column].astype(str).str.strip()
    numeric_population = pd.to_numeric(population[population_column], errors="coerce")
    unavailable_ids: list[str] = []
    values_as_text = population[population_column].astype("string").str.strip()
    unavailable_mask = numeric_population.isna() & (
        population[population_column].isna() | values_as_text.isin({"", "..", "...", "x", "X"})
    )
    if unavailable_mask.any() and allow_missing_population:
        unavailable_ids = population.loc[unavailable_mask, join_column].astype(str).tolist()
        population = population.loc[~unavailable_mask].copy()
    population[population_column] = _coerce_population_values(
        population[population_column],
        context=f"Canada population table {path}",
    )
    duplicate_values = (
        population.groupby(join_column)[population_column].nunique().loc[lambda values: values > 1]
    )
    if not duplicate_values.empty:
        raise PopulationDataError(
            f"Canada population table has conflicting duplicate IDs. "
            f"Examples: {duplicate_values.index[:10].tolist()}"
        )
    population = population.drop_duplicates(join_column, keep="first").copy()
    population.attrs["unavailable_geo_ids"] = unavailable_ids
    return population, join_column, population_column


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _first_matching_column(
    columns: list[str],
    candidates: tuple[str, ...],
    label: str,
) -> str:
    lower_to_original = {column.lower(): column for column in columns}
    for candidate in candidates:
        if candidate in columns:
            return candidate
        match = lower_to_original.get(candidate.lower())
        if match:
            return match
    raise CanadaApiError(
        f"StatsCan API response did not include a {label} column. Columns: {columns}"
    )


def _parse_statcan_population_csv(text: str) -> pd.DataFrame:
    """Parse SDMX CSV from the Statistics Canada Census Profile API."""
    frame = pd.read_csv(StringIO(text))
    if frame.empty:
        raise CanadaApiError("StatsCan API returned an empty CSV response.")
    geography_column = _first_matching_column(
        list(frame.columns),
        ("DGUID", "REF_AREA", "Reference area", "GEO", "Geography"),
        "geography",
    )
    value_column = _first_matching_column(
        list(frame.columns),
        (
            "OBS_VALUE",
            "VALUE",
            "Value",
            "C1_COUNT_TOTAL",
            "Population, 2021",
            "Population",
        ),
        "population value",
    )
    result = frame[[geography_column, value_column]].copy()
    result.columns = ["geo_id", "population_2021"]
    return _consolidate_population(result, context="StatsCan API response")


def fetch_statcan_da_population_api(
    cfg: CanadaPopulationConfig,
    dguids: list[str],
    cache_path: Path | None = None,
    existing: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Fetch candidate DA population from the official Census Profile SDMX API."""
    unique_dguids = sorted({str(value) for value in dguids if pd.notna(value)})
    if not unique_dguids:
        if existing is not None and not existing.empty:
            return _consolidate_population(existing, context="Existing StatsCan population")
        raise CanadaApiError("No DA DGUIDs were supplied for the StatsCan API request.")

    frames: list[pd.DataFrame] = []
    if existing is not None and not existing.empty:
        frames.append(_consolidate_population(existing, context="Existing StatsCan population"))
    endpoint = f"{cfg.source.statcan_sdmx_base_url}/data/STC_CP,{cfg.source.statcan_dataflow}"
    chunks = _chunks(unique_dguids, cfg.source.statcan_request_chunk_size)
    LOGGER.info(
        "Fetching StatsCan DA population for %s geographies in %s chunk(s) of up to %s",
        len(unique_dguids),
        len(chunks),
        cfg.source.statcan_request_chunk_size,
    )

    for chunk_number, chunk in enumerate(chunks, start=1):
        geography_key = "+".join(chunk)
        key = ".".join(
            [
                cfg.source.statcan_frequency,
                geography_key,
                cfg.source.statcan_gender_code,
                cfg.source.statcan_population_characteristic_code,
                cfg.source.statcan_statistic_code,
            ]
        )
        url = f"{endpoint}/{key}"
        LOGGER.info(
            "Fetching StatsCan DA population chunk %s/%s (%s geographies)",
            chunk_number,
            len(chunks),
            len(chunk),
        )
        response: requests.Response | None = None
        for attempt in range(1, 4):
            try:
                response = requests.get(
                    url,
                    params={"format": "csv", "detail": "dataonly"},
                    headers={"Accept": "text/csv"},
                    timeout=cfg.source.statcan_timeout_seconds,
                )
            except requests.RequestException as exc:
                if attempt == 3:
                    raise CanadaApiError(
                        "StatsCan Census Profile API request failed after retries. "
                        f"Endpoint={url}; error={exc}"
                    ) from exc
                sleep_seconds = 2 * attempt
                LOGGER.warning(
                    "StatsCan request error on attempt %s/3; retrying in %ss: %s",
                    attempt,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                sleep_seconds = 5 * attempt
                LOGGER.warning(
                    "StatsCan API returned HTTP %s on attempt %s/3; retrying in %ss",
                    response.status_code,
                    attempt,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)
                continue
            break

        if response is None:
            raise CanadaApiError("StatsCan request failed before a response was created.")
        if response.status_code != 200:
            raise CanadaApiError(
                "StatsCan Census Profile API request failed. "
                f"Endpoint={url}; HTTP={response.status_code}; "
                f"response preview={response.text[:1000]}"
            )
        try:
            frames.append(_parse_statcan_population_csv(response.text))
        except CanadaApiError:
            raise
        except Exception as exc:
            raise CanadaApiError(
                "StatsCan response could not be parsed for DA population. "
                f"Endpoint={url}; response preview={response.text[:1000]}"
            ) from exc

        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            partial = _consolidate_population(
                pd.concat(frames, ignore_index=True),
                context="Partial StatsCan population cache",
            )
            atomic_write_parquet(partial, cache_path, overwrite=True)
            LOGGER.info(
                "Updated partial StatsCan population cache with %s rows: %s",
                len(partial),
                cache_path,
            )

    population = _consolidate_population(
        pd.concat(frames, ignore_index=True),
        context="Fetched StatsCan population",
    )
    missing = sorted(set(unique_dguids) - set(population["geo_id"]))
    if missing:
        raise CanadaApiError(
            "StatsCan did not return all requested DA rows. "
            f"Missing {len(missing)} of {len(unique_dguids)}; examples: {missing[:10]}"
        )
    return population


def load_or_fetch_candidate_population(
    cfg: CanadaPopulationConfig,
    candidate_dguids: list[str],
    overwrite: bool = False,
) -> pd.DataFrame:
    """Load cached candidate population or fetch it from the official API."""
    cache_path = cfg.paths.population_api_cache
    requested = sorted({str(value) for value in candidate_dguids if pd.notna(value)})
    if not requested:
        raise CanadaApiError("No candidate DA geography IDs were supplied.")

    cached = pd.DataFrame(columns=["geo_id", "population_2021"])
    if cache_path.exists() and not overwrite:
        cached = _consolidate_population(
            pd.read_parquet(cache_path),
            context=f"StatsCan population cache {cache_path}",
        )
        cached_ids = set(cached["geo_id"])
        if set(requested).issubset(cached_ids):
            LOGGER.info("Using cached StatsCan population API results: %s", cache_path)
            return cached[cached["geo_id"].isin(requested)].copy()
        LOGGER.info(
            "StatsCan cache has %s requested rows and is missing %s",
            len(set(requested) & cached_ids),
            len(set(requested) - cached_ids),
        )

    if cfg.source.api_or_download_mode in {"auto", "api"}:
        cached_requested = cached[cached["geo_id"].isin(requested)].copy()
        missing = sorted(set(requested) - set(cached_requested["geo_id"]))
        population = fetch_statcan_da_population_api(
            cfg,
            missing,
            cache_path=cache_path,
            existing=cached_requested,
        )
        population = population[population["geo_id"].isin(requested)].copy()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(population, cache_path, overwrite=True)
        LOGGER.info("Cached StatsCan population API results: %s", cache_path)
        return population

    raise MissingCanadaInputError(
        "Canada API mode is disabled and no usable cached population file exists."
    )


def load_local_fallback_population(cfg: CanadaPopulationConfig) -> pd.DataFrame:
    """Load a locally configured population fallback table."""
    population_path = cfg.source.population_table_path
    if population_path is None and cfg.source.population_download_url is not None:
        population_path = download_file(
            cfg.source.population_download_url,
            cfg.paths.raw_dir / cfg.source.population_download_filename,
        )
    if population_path is None:
        raise MissingCanadaInputError(
            "Canada local population fallback requested, but "
            "neither canada.population_table_path nor canada.population_download_url "
            "is configured."
        )
    join_candidates = [cfg.source.population_geoid_col] if cfg.source.population_geoid_col else []
    join_candidates.extend(cfg.source.population_join_column_candidates)
    value_candidates = [cfg.source.population_value_col] if cfg.source.population_value_col else []
    value_candidates.extend(cfg.source.population_variable_candidates)
    population, join_column, population_column = load_canada_population(
        population_path,
        [candidate for candidate in join_candidates if candidate],
        [candidate for candidate in value_candidates if candidate],
        allow_missing_population=True,
    )
    unavailable_ids = [str(value).strip() for value in population.attrs["unavailable_geo_ids"]]
    unavailable = pd.DataFrame({"geo_id": unavailable_ids})
    if not unavailable.empty:
        numeric_ids = unavailable["geo_id"].str.match(r"^\d+$", na=False)
        unavailable.loc[numeric_ids, "geo_id"] = (
            cfg.source.statcan_dguid_prefix + unavailable.loc[numeric_ids, "geo_id"]
        )
    unavailable["census_year"] = cfg.source.census_year
    unavailable["measurement_status"] = "unavailable"
    unavailable["qc_reason"] = "STATCAN_POPULATION_VALUE_UNAVAILABLE"
    unavailable["source_dataset"] = "Statistics Canada 2021 Census Profile"
    unavailable_path = cfg.paths.raw_dir / STATCAN_UNAVAILABLE_FILENAME
    atomic_write_parquet(unavailable, unavailable_path, overwrite=True)
    if not unavailable.empty:
        LOGGER.warning(
            "Statistics Canada reports %s DA population values as unavailable; "
            "wrote explicit QC rows to %s",
            len(unavailable),
            unavailable_path,
        )
    result = population[[join_column, population_column]].rename(
        columns={join_column: "geo_id", population_column: "population_2021"}
    )
    result["geo_id"] = result["geo_id"].astype(str).str.strip()
    da_mask = result["geo_id"].str.match(r"^\d+$", na=False)
    result.loc[da_mask, "geo_id"] = cfg.source.statcan_dguid_prefix + result.loc[da_mask, "geo_id"]
    return _consolidate_population(result, context="Local Canada population fallback")


def join_candidate_population(
    cfg: CanadaPopulationConfig,
    candidate_geographies: gpd.GeoDataFrame,
    geography_join_column: str,
    overwrite: bool = False,
) -> gpd.GeoDataFrame:
    """Join API-first 2021 population counts to candidate DA geometries."""
    candidates = candidate_geographies.copy()
    if "DGUID" in candidates.columns:
        candidates["source_geo_id"] = candidates["DGUID"].astype(str).str.strip()
    else:
        values = candidates[geography_join_column].astype(str).str.strip()
        if geography_join_column.upper() == "DAUID":
            candidates["source_geo_id"] = cfg.source.statcan_dguid_prefix + values
        else:
            candidates["source_geo_id"] = values
    if candidates["source_geo_id"].duplicated().any():
        raise PopulationDataError("Candidate Canada geography contains duplicate source IDs.")

    if cfg.source.api_or_download_mode == "local":
        requested = set(candidates["source_geo_id"])
        cache_path = cfg.paths.population_api_cache
        population = pd.DataFrame(columns=["geo_id", "population_2021"])
        cache_covers_requested = False
        if cache_path.exists() and not overwrite:
            cached = _consolidate_population(
                pd.read_parquet(cache_path),
                context=f"Statistics Canada population cache {cache_path}",
            )
            unavailable_ids: set[str] = set()
            unavailable_path = cfg.paths.raw_dir / STATCAN_UNAVAILABLE_FILENAME
            if unavailable_path.exists():
                unavailable_ids = set(
                    pd.read_parquet(unavailable_path, columns=["geo_id"])["geo_id"].astype(str)
                )
            if requested.issubset(set(cached["geo_id"]) | unavailable_ids):
                population = cached[cached["geo_id"].isin(requested)].copy()
                cache_covers_requested = True
                LOGGER.info("Using cached Statistics Canada population: %s", cache_path)
        if not cache_covers_requested:
            population = load_local_fallback_population(cfg)
            population = population[population["geo_id"].isin(requested)].copy()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_parquet(population, cache_path, overwrite=True)
            LOGGER.info("Cached Statistics Canada population: %s", cache_path)
    else:
        try:
            population = load_or_fetch_candidate_population(
                cfg,
                candidates["source_geo_id"].tolist(),
                overwrite=overwrite or cfg.runtime.overwrite_raw_cache,
            )
        except CanadaApiError:
            if (
                cfg.source.api_or_download_mode == "auto"
                and cfg.source.population_table_path is not None
            ):
                LOGGER.warning(
                    "StatsCan API failed in auto mode; using configured local fallback table."
                )
                population = load_local_fallback_population(cfg)
            else:
                raise

    joined = candidates.merge(
        population[["geo_id", "population_2021"]],
        left_on="source_geo_id",
        right_on="geo_id",
        how="left",
        validate="one_to_one",
    )
    missing_mask = joined["population_2021"].isna()
    if missing_mask.any():
        examples = joined.loc[missing_mask, "source_geo_id"].head(10).tolist()
        LOGGER.warning(
            "Population is unavailable for %s Canada DA rows; retaining nulls for explicit "
            "downstream exclusion. Examples: %s",
            int(missing_mask.sum()),
            examples,
        )
    joined["population_available"] = ~missing_mask
    joined["population_2021"] = pd.to_numeric(joined["population_2021"], errors="coerce").astype(
        "Int64"
    )
    return repair_geometries(joined.to_crs(cfg.area_crs))
