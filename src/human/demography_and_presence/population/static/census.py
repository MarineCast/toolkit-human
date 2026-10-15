"""Bounded original-census adapter, independent of coastal/water feature construction."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import box

from ..canada.statcan import read_population_table
from .sources import PopulationSources, checksum, write_sources, bundle_inventory

LOGGER = logging.getLogger(__name__)


def _resolve(workspace, value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else workspace / path


def _obtain(path, url, mode, budget):
    if path.exists():
        return path
    if mode != "live":
        raise FileNotFoundError(f"Offline population source missing: {path}")
    if not url:
        raise ValueError(f"Explicit source URL required for live acquisition: {path}")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "www2.census.gov", "www12.statcan.gc.ca", "www.statcan.gc.ca"}:
        raise ValueError("Census downloads require an explicitly configured official HTTPS source URL")
    if budget["downloads"] <= 0:
        raise ValueError("Live acquisition exceeds max_downloads; review source scope")
    budget["downloads"] -= 1
    import requests
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        # No retries/redirects that could escape the caller's bounded request scope.
        with requests.get(url, stream=True, timeout=(15, 120), allow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"Expected HTTP 200 source response, got {response.status_code}")
            with temporary.open("xb") as handle:
                for block in response.iter_content(1024 * 1024):
                    budget["bytes"] -= len(block)
                    if budget["bytes"] < 0:
                        raise ValueError("Live acquisition exceeds max_download_bytes")
                    handle.write(block)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _geography(path, extent):
    if path.suffix == ".parquet":
        frame = gpd.read_parquet(path)
    else:
        # GeoSeries mask carries its CRS, so pyogrio transforms the filter correctly.
        mask = gpd.GeoSeries([box(*extent)], crs=4326)
        frame = gpd.read_file("zip://" + str(path) if path.suffix == ".zip" else path, mask=mask)
    if frame.crs is None:
        raise ValueError(f"Census geography has no CRS: {path}")
    frame = frame.to_crs(4326)
    frame.geometry = shapely.make_valid(frame.geometry.values)
    frame = frame.loc[frame.intersects(box(*extent))].copy()
    if not frame.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("Census geometry repair produced non-polygon support; resolve source geometry")
    return frame


def prepare_census_sources(*, workspace, source_extent, specification, mode="offline"):
    """Normalize intersecting full original polygons without coastal filtering.

    Each `us`/`canada` entry specifies geography path, optional population table,
    jurisdiction and state_fips/province_code. US TIGER2020 POP20 may be selected
    explicitly instead of a population table. Missing joins stay null. Full original
    geometry is retained, including known zero polygons; no coastal cache is reused.
    """
    if mode not in {"offline", "live"}:
        raise ValueError("mode must be offline or live")
    workspace = Path(workspace).resolve()
    entries = [("US", row) for row in specification.get("us", [])] + [("CA", row) for row in specification.get("canada", [])]
    if not entries:
        raise ValueError("Configure bounded US/Canada census entries before source preparation")
    budget = {"downloads": int(specification.get("max_downloads", 0)),
              "bytes": int(specification.get("max_download_bytes", 0))}
    if budget["downloads"] < 0 or budget["bytes"] < 0:
        raise ValueError("Download limits cannot be negative")
    inputs = []
    for country, entry in entries:
        for label in ("geography", "population"):
            if not entry.get(label):
                continue
            path = _resolve(workspace, entry[label])
            _obtain(path, entry.get(label + "_url"), mode, budget)
            inputs.append({"path": str(path), "sha256": checksum(path), "role": label,
                           "jurisdiction": entry["jurisdiction"]})
    # Optional complete administrative inventory diagnoses required unsupported jurisdictions.
    inventories = []
    for record in specification.get("jurisdiction_inventories", []):
        path = _resolve(workspace, record["path"])
        if not path.exists():
            raise FileNotFoundError(path)
        inputs.append({"path": str(path), "sha256": checksum(path), "role": "jurisdiction_inventory"})
        inventories.append((record, path))
    identity = {"adapter": "original_census.v1", "inputs": inputs, "extent": source_extent,
                "specification": specification}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    pointer = workspace / "cache/population/normalization" / key / "sources.json"
    if pointer.exists():
        bundle_inventory(pointer)
        return pointer
    pieces, required, metadata = [], set(), []
    for country, entry in entries:
        path = _resolve(workspace, entry["geography"])
        LOGGER.info("Loading intersecting original census polygons: %s", entry["jurisdiction"])
        geography = _geography(path, source_extent)
        code_column = "STATEFP20" if country == "US" else "PRUID"
        code = str(entry["state_fips" if country == "US" else "province_code"])
        if code_column in geography:
            if country == "CA":
                required.update("CA-" + c for c in geography[code_column].astype(str).unique())
            geography = geography.loc[geography[code_column].astype(str).eq(code)].copy()
        identifier = entry.get("geography_id", "GEOID20" if country == "US" else "DGUID")
        if geography[identifier].duplicated().any():
            raise ValueError(f"Duplicate original geography IDs in {path}")
        geography["join_id"] = geography[identifier].astype(str)
        if entry.get("population"):
            population_path = _resolve(workspace, entry["population"])
            population = read_population_table(population_path)
            join_id = entry.get("population_id", "geoid20" if country == "US" else "geo_id")
            pop_column = entry.get("population_column", "population_2020" if country == "US" else "population_2021")
            if population[join_id].isna().any() or population[join_id].astype(str).duplicated().any():
                raise ValueError(f"Null/duplicate population source IDs in {population_path}")
            values = population[[join_id, pop_column]].copy()
            values.columns = ["join_id", "population"]
            values["join_id"] = values.join_id.astype(str)
            # Only declared suppression tokens become missing; corrupt text still fails.
            values["population"] = pd.to_numeric(values.population.replace({"x": None, "X": None, "..": None, "...": None, "": None}), errors="raise")
            geography = geography.merge(values, on="join_id", how="left", validate="one_to_one")
        else:
            column = entry.get("population_column")
            if country != "US" or column != "POP20":
                raise ValueError("Supply a population table or explicitly select US TIGER POP20")
            geography["population"] = pd.to_numeric(geography[column], errors="raise")
        geography["source_id"] = ("US2020:" if country == "US" else "CA2021:") + geography.join_id
        geography["jurisdiction"] = entry["jurisdiction"]
        geography["country"] = country
        geography["vintage"] = 2020 if country == "US" else 2021
        pieces.append(geography[["source_id", "jurisdiction", "country", "vintage", "population", "geometry"]])
        metadata.append({"jurisdiction": entry["jurisdiction"], "country": country,
                         "vintage": 2020 if country == "US" else 2021,
                         "attribution": "United States Census Bureau" if country == "US" else "Statistics Canada",
                         "license": "United States public domain" if country == "US" else "Statistics Canada Open Licence",
                         "geography_path": str(path), "selected_sources": len(geography),
                         "missing_population_sources": int(geography.population.isna().sum())})
    for record, path in inventories:
        geography = _geography(path, source_extent)
        required.update(str(record.get("prefix", "")) + v for v in geography[record["id_column"]].astype(str))
    sources = gpd.GeoDataFrame(pd.concat(pieces, ignore_index=True), crs=4326)
    if len(sources) > int(specification.get("max_sources", 1000000)):
        raise ValueError("Source normalization exceeds configured max_sources")
    # Census support is retained as individual polygons. The engine dissolves in its
    # declared equal-area CRS. Do not claim that oceans/bbox gaps are known empty.
    coverage = sources[["geometry"]].copy()
    available = {"US-" + str(row["state_fips"]) for row in specification.get("us", [])}
    available.update("CA-" + str(row["province_code"]) for row in specification.get("canada", []))
    bundle = PopulationSources(sources, coverage, {"adapter": "original_census.v1", "inputs": inputs,
        "census_sources": metadata, "required_jurisdictions": sorted(required),
        "missing_jurisdictions": sorted(required - available), "normalization_extent_wgs84": source_extent,
        "coverage_limitations": ["Coverage asserts only retained original census geography support; unverified ocean and other gaps remain unknown.",
            "Suppressed/missing Canadian DA values and missing population joins retain their original geometries.",
            "No complete global jurisdiction inventory is assumed; unknown areas outside supplied inventories remain gaps."]})
    policy = specification.get("cross_border_overlap", "reject")
    if policy not in {"reject", "quarantine"}:
        raise ValueError("cross_border_overlap must be reject or quarantine")
    if policy == "quarantine":
        from .sources import quarantine_cross_border_overlaps
        bundle = quarantine_cross_border_overlaps(bundle)
    output = write_sources(bundle, pointer.parent)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    original = json.loads(output.read_text())
    for record in original["files"].values():
        record["path"] = str((output.parent / record["path"]).relative_to(pointer.parent))
    pointer.write_text(json.dumps(original, indent=2, sort_keys=True))
    return pointer
