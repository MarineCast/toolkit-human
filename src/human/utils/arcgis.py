"""ArcGIS REST acquisition helpers shared by human feature families."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import requests

DEFAULT_HEADERS = {
    "User-Agent": "OrcaCastDataPrep/1.1 (+https://github.com/orcacast)",
    "Accept": "application/json, application/geo+json, */*",
}


def arcgis_query_url(layer_url: str) -> str:
    """Return the REST query endpoint for an ArcGIS feature layer."""
    clean = str(layer_url).rstrip("/")
    return clean if clean.endswith("/query") else f"{clean}/query"


def _get(url: str, **kwargs: Any) -> requests.Response:
    headers = dict(DEFAULT_HEADERS)
    headers.update(kwargs.pop("headers", {}) or {})
    return requests.get(url, headers=headers, **kwargs)


def query_arcgis_geojson(
    layer_url: str,
    *,
    where: str = "1=1",
    out_fields: str = "*",
    bbox: tuple[float, float, float, float] | None = None,
    page_size: int | None = None,
) -> gpd.GeoDataFrame:
    """Page through an ArcGIS layer and return WGS84 features.

    Layer metadata is advisory. Some public servers reject metadata requests
    while serving queries, so acquisition falls back to a conservative page
    size and still checks ArcGIS error payloads.
    """
    if page_size is None:
        try:
            response = _get(layer_url.rstrip("/"), params={"f": "pjson"}, timeout=60)
            response.raise_for_status()
            page_size = int(response.json().get("maxRecordCount", 1000)) or 1000
        except Exception:
            page_size = 1000

    params: dict[str, Any] = {
        "f": "geojson",
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true",
        "outSR": 4326,
        "resultOffset": 0,
        "resultRecordCount": page_size,
    }
    if bbox is not None:
        west, south, east, north = bbox
        params.update(
            {
                "geometry": f"{west},{south},{east},{north}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
            }
        )

    features: list[dict[str, Any]] = []
    query_url = arcgis_query_url(layer_url)
    while True:
        response = _get(query_url, params=params, timeout=120)
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            error = payload["error"]
            message = error.get("message", error) if isinstance(error, dict) else error
            raise RuntimeError(f"ArcGIS query failed for {query_url}: {message}")
        batch = payload.get("features", []) or []
        features.extend(batch)
        if len(batch) < page_size:
            break
        params["resultOffset"] += page_size

    if not features:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    frame = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    if frame.crs is None:
        frame = frame.set_crs("EPSG:4326")
    return frame.to_crs("EPSG:4326")
