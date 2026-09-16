"""Bounded WFS acquisition for provenance-preserving human source snapshots."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd
import requests

DEFAULT_HEADERS = {
    "User-Agent": "OrcaCastDataPrep/1.1 (+https://github.com/orcacast)",
    "Accept": "application/json",
}


def query_wfs_geojson(
    endpoint_url: str,
    feature_type: str,
    bbox: tuple[float, float, float, float],
    *,
    page_size: int = 10_000,
    timeout_seconds: int = 180,
    sort_by: str | None = None,
) -> gpd.GeoDataFrame:
    """Download every WFS feature intersecting a WGS84 bounding box.

    Pagination is mandatory: DataBC and many GeoServer deployments cap a
    single response even when the caller does not specify ``count``.
    """
    if not endpoint_url or not feature_type:
        raise ValueError("WFS endpoint_url and feature_type are required.")
    if page_size <= 0 or timeout_seconds <= 0:
        raise ValueError("WFS page_size and timeout_seconds must be positive.")
    west, south, east, north = bbox
    if west >= east or south >= north:
        raise ValueError("WFS bbox must have west < east and south < north.")

    frames: list[gpd.GeoDataFrame] = []
    start_index = 0
    matched: int | None = None
    while matched is None or start_index < matched:
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": feature_type,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "bbox": f"{west},{south},{east},{north},EPSG:4326",
            "count": page_size,
            "startIndex": start_index,
        }
        if sort_by:
            params["sortBy"] = sort_by
        response = requests.get(
            endpoint_url,
            params=params,
            headers=DEFAULT_HEADERS,
            timeout=(30, timeout_seconds),
        )
        response.raise_for_status()
        payload: Any = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
            raise RuntimeError("Invalid WFS response: missing GeoJSON features list.")
        if matched is None:
            raw_matched = payload.get("numberMatched")
            if isinstance(raw_matched, int) or (
                isinstance(raw_matched, str) and raw_matched.isdigit()
            ):
                matched = int(raw_matched)
        features = payload["features"]
        if not features:
            break
        page = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        frames.append(page)
        returned = len(features)
        start_index += returned
        if returned < page_size and matched is None:
            break

    if not frames:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    merged = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326",
    )
    feature_id = next(
        (column for column in ("OBJECTID", "objectid", "FID", "fid") if column in merged.columns),
        None,
    )
    if feature_id is not None:
        merged = merged.drop_duplicates(feature_id)
    else:
        merged = merged.drop_duplicates()
    return merged.reset_index(drop=True)
