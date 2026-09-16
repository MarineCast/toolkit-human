"""US Census API access for 2020 PL block population."""

from __future__ import annotations

import logging
import os
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from tqdm.auto import tqdm

from ..common.errors import PopulationDataError, PopulationExternalServiceError
from ..common.io import atomic_write_parquet
from .config import UsPopulationConfig

LOGGER = logging.getLogger(__name__)


class CensusInvalidKeyError(PopulationExternalServiceError):
    """Raised when Census rejects the configured API key."""


def _clean_api_key(key: str | None) -> str:
    cleaned = str(key or "").strip().strip("'").strip('"')
    if cleaned and ("http" in cleaned.lower() or "key=" in cleaned.lower()):
        raise ValueError("Census API key looks like a URL or query string, not a raw key.")
    return cleaned


def _redact_url(url: str, key: str) -> str:
    return url.replace(key, "KEY_REDACTED") if key else url


def _api_key(cfg: UsPopulationConfig) -> str:
    if not cfg.census.use_api_key:
        return ""
    key = _clean_api_key(os.getenv(cfg.census.api_key_env_var))
    if not key:
        raise RuntimeError(
            "census.use_api_key=true but environment variable "
            f"{cfg.census.api_key_env_var!r} is missing or empty."
        )
    return key


def _coerce_population(series: pd.Series, *, label: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    invalid = numeric.isna() | ~np.isfinite(numeric)
    if invalid.any():
        examples = series.loc[invalid].astype(str).head(10).tolist()
        raise PopulationDataError(
            f"{label} contains {int(invalid.sum())} invalid population values. "
            f"Examples: {examples}"
        )
    if (numeric < 0).any():
        examples = numeric.loc[numeric < 0].head(10).tolist()
        raise PopulationDataError(f"{label} contains negative population values: {examples}")
    if not np.allclose(numeric, np.round(numeric), rtol=0, atol=1e-12):
        examples = numeric.loc[~np.isclose(numeric, np.round(numeric))].head(10).tolist()
        raise PopulationDataError(f"{label} contains non-integer population counts: {examples}")
    return numeric.astype("int64")


def census_get(cfg: UsPopulationConfig, params: dict[str, str]) -> list[list[str]]:
    """Call the Census API with retry and explicit invalid-key detection."""
    request_params = dict(params)
    key = _api_key(cfg)
    if key:
        request_params["key"] = key

    last_error: PopulationExternalServiceError | None = None
    for attempt in range(1, cfg.census.max_retries + 1):
        try:
            response = requests.get(
                cfg.census.api_base_url,
                params=request_params,
                timeout=cfg.census.timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            last_error = PopulationExternalServiceError(
                f"Census API request failed on attempt {attempt}: {exc}"
            )
            if attempt < cfg.census.max_retries:
                time.sleep(2 * attempt)
                continue
            raise last_error from exc

        safe_url = _redact_url(response.url, key)
        content_type = (response.headers.get("content-type") or "").lower()
        location = response.headers.get("location") or ""
        preview = response.text[:1500]

        if "invalid_key.html" in location.lower() or "invalid_key.html" in response.url.lower():
            raise CensusInvalidKeyError(f"Census rejected the API key. URL: {safe_url}")
        if "html" in content_type or "<title>invalid key</title>" in preview.lower():
            if "invalid" in preview.lower() and "key" in preview.lower():
                raise CensusInvalidKeyError(
                    f"Census returned an invalid-key HTML response. URL: {safe_url}\n"
                    f"Response preview:\n{preview}"
                )
            raise PopulationExternalServiceError(
                f"Census API returned HTML instead of JSON. URL: {safe_url}\n"
                f"Response preview:\n{preview}"
            )

        if response.status_code in {429, 500, 502, 503, 504}:
            last_error = PopulationExternalServiceError(
                f"Census API temporary HTTP {response.status_code} on attempt {attempt}. "
                f"URL: {safe_url}\nResponse preview:\n{preview}"
            )
            if attempt < cfg.census.max_retries:
                time.sleep(2 * attempt)
                continue
            raise last_error

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise PopulationExternalServiceError(
                f"Census API HTTP {response.status_code}. URL: {safe_url}\n"
                f"Response preview:\n{preview}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise PopulationExternalServiceError(
                f"Census API did not return valid JSON. URL: {safe_url}\n"
                f"Content-Type: {response.headers.get('content-type')}\n"
                f"Response preview:\n{preview}"
            ) from exc
        if not isinstance(payload, list):
            raise PopulationExternalServiceError(
                f"Census API returned an unexpected JSON object. URL: {safe_url}; "
                f"payload preview={str(payload)[:1000]}"
            )

        time.sleep(cfg.census.sleep_seconds)
        return payload

    raise last_error or PopulationExternalServiceError("Census API failed after retries.")


def _payload_to_population(
    cfg: UsPopulationConfig,
    payload: list[list[str]],
) -> pd.DataFrame:
    if not payload or not isinstance(payload[0], list):
        raise PopulationDataError("Census API returned an empty or malformed payload.")
    df = pd.DataFrame(payload[1:], columns=payload[0])
    pop_var = cfg.census.total_population_variable
    required = {"state", "county", "tract", "block", pop_var}
    missing = required - set(df.columns)
    if missing:
        raise PopulationDataError(f"Census payload missing expected columns: {sorted(missing)}")
    df["geoid20"] = (
        df["state"].astype(str).str.zfill(2)
        + df["county"].astype(str).str.zfill(3)
        + df["tract"].astype(str).str.zfill(6)
        + df["block"].astype(str).str.zfill(4)
    )
    df["population_2020"] = _coerce_population(df[pop_var], label="Census API response")
    return df[["geoid20", "population_2020"]]


def fetch_block_population_for_county(
    cfg: UsPopulationConfig,
    state_fips: str,
    county_fips: str,
) -> pd.DataFrame:
    """Fetch all Census block populations for one county."""
    payload = census_get(
        cfg,
        {
            "get": cfg.census.total_population_variable,
            "for": "block:*",
            "in": f"state:{state_fips} county:{county_fips} tract:*",
        },
    )
    return _payload_to_population(cfg, payload)


def fetch_block_population_for_tract(
    cfg: UsPopulationConfig,
    state_fips: str,
    county_fips: str,
    tract_fips: str,
) -> pd.DataFrame:
    """Fetch Census block populations for one tract as county-query fallback."""
    payload = census_get(
        cfg,
        {
            "get": cfg.census.total_population_variable,
            "for": "block:*",
            "in": f"state:{state_fips} county:{county_fips} tract:{tract_fips}",
        },
    )
    return _payload_to_population(cfg, payload)


def _normalize_population_frame(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    required = {"geoid20", "population_2020"}
    missing = required - set(frame.columns)
    if missing:
        raise PopulationDataError(f"{label} missing columns: {sorted(missing)}")
    normalized = frame[["geoid20", "population_2020"]].copy()
    normalized["geoid20"] = normalized["geoid20"].astype(str)
    normalized["population_2020"] = _coerce_population(normalized["population_2020"], label=label)
    conflicts = normalized.groupby("geoid20")["population_2020"].nunique()
    conflicting_geoids = conflicts[conflicts > 1]
    if not conflicting_geoids.empty:
        raise PopulationDataError(
            f"{label} contains conflicting population values for "
            f"{len(conflicting_geoids)} block GEOIDs. Examples: "
            f"{conflicting_geoids.index[:10].tolist()}"
        )
    return normalized.drop_duplicates("geoid20", keep="first")


def fetch_block_population(
    cfg: UsPopulationConfig,
    blocks: gpd.GeoDataFrame,
    overwrite: bool = False,
    tract_only: bool = False,
) -> pd.DataFrame:
    """Fetch and cache 2020 block population using block-file geographies."""
    requested_geoids = set(blocks["geoid20"].astype(str))
    cache_path = cfg.paths.block_population_cache
    if cache_path.exists() and not overwrite:
        cached = _normalize_population_frame(
            pd.read_parquet(cache_path),
            label=f"Cached block population {cache_path}",
        )
        cached_geoids = set(cached["geoid20"])
        missing_geoids = requested_geoids - cached_geoids
        if not missing_geoids:
            LOGGER.info("Using cached block population: %s", cache_path)
            return cached[cached["geoid20"].isin(requested_geoids)].copy()
        LOGGER.warning(
            "Cached block population is missing %s requested block(s); refetching.",
            len(missing_geoids),
        )

    required = {"STATEFP20", "COUNTYFP20", "TRACTCE20"}
    missing = required - set(blocks.columns)
    if missing:
        raise ValueError(f"Block geometries missing required columns: {sorted(missing)}")

    blocks_meta = blocks[["STATEFP20", "COUNTYFP20", "TRACTCE20"]].copy()
    blocks_meta["STATEFP20"] = blocks_meta["STATEFP20"].astype(str).str.zfill(2)
    blocks_meta["COUNTYFP20"] = blocks_meta["COUNTYFP20"].astype(str).str.zfill(3)
    blocks_meta["TRACTCE20"] = blocks_meta["TRACTCE20"].astype(str).str.zfill(6)
    pieces: list[pd.DataFrame] = []
    if tract_only:
        records = list(
            blocks_meta[["STATEFP20", "COUNTYFP20", "TRACTCE20"]]
            .drop_duplicates()
            .itertuples(index=False, name=None)
        )
        for state_fips, county_fips, tract_fips in tqdm(
            records, desc="Fetching tract block populations"
        ):
            pieces.append(
                fetch_block_population_for_tract(cfg, state_fips, county_fips, tract_fips)
            )
    else:
        counties = (
            blocks_meta[["STATEFP20", "COUNTYFP20"]]
            .drop_duplicates()
            .sort_values(["STATEFP20", "COUNTYFP20"])
        )
        for state_fips, county_fips in tqdm(
            list(counties.itertuples(index=False, name=None)),
            desc="Fetching county block populations",
        ):
            try:
                pieces.append(fetch_block_population_for_county(cfg, state_fips, county_fips))
            except CensusInvalidKeyError:
                raise
            except PopulationExternalServiceError as exc:
                LOGGER.warning(
                    "State %s county %s failed; falling back to tract calls: %s",
                    state_fips,
                    county_fips,
                    str(exc)[:1000],
                )
                tracts = sorted(
                    blocks_meta.loc[
                        (blocks_meta["STATEFP20"] == state_fips)
                        & (blocks_meta["COUNTYFP20"] == county_fips),
                        "TRACTCE20",
                    ].unique()
                )
                for tract_fips in tqdm(
                    tracts,
                    desc=f"County {county_fips} tracts",
                    leave=False,
                ):
                    pieces.append(
                        fetch_block_population_for_tract(cfg, state_fips, county_fips, tract_fips)
                    )

    if not pieces:
        raise PopulationDataError("No Census block population records were fetched.")
    population = _normalize_population_frame(
        pd.concat(pieces, ignore_index=True),
        label="Fetched Census block population",
    )
    missing_geoids = sorted(requested_geoids - set(population["geoid20"]))
    if missing_geoids:
        raise PopulationDataError(
            f"Census population fetch is missing {len(missing_geoids)} requested blocks. "
            f"Examples: {missing_geoids[:10]}"
        )
    population = population[population["geoid20"].isin(requested_geoids)].copy()
    atomic_write_parquet(population, cache_path, overwrite=True)
    LOGGER.info("Wrote block population cache: %s", cache_path)
    return population
