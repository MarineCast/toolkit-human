"""Small Overpass adapter for provenance-preserving human access inventories."""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_HEADERS = {
    "User-Agent": "OrcaCastDataPrep/1.1 (+https://github.com/orcacast)",
    "Accept": "application/json",
}


def build_overpass_query(
    bbox: tuple[float, float, float, float],
    selectors: Iterable[str],
    *,
    timeout_seconds: int = 180,
) -> str:
    """Build a bounded query returning tags and representative centers."""
    west, south, east, north = bbox
    osm_bbox = f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"
    statements = "\n".join(f"  nwr{selector}({osm_bbox});" for selector in selectors)
    return (
        f"[out:json][timeout:{int(timeout_seconds)}];\n"
        "(\n"
        f"{statements}\n"
        ");\n"
        "out tags center qt;\n"
    )


def query_overpass_points(
    endpoint_url: str,
    query: str,
    *,
    timeout_seconds: int = 240,
) -> gpd.GeoDataFrame:
    """Query Overpass and retain one WGS84 point plus tags per OSM object."""
    response = requests.post(
        endpoint_url or DEFAULT_OVERPASS_URL,
        data={"data": query},
        headers=DEFAULT_HEADERS,
        timeout=(30, timeout_seconds),
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("remark"):
        raise RuntimeError(f"Incomplete Overpass response: {payload['remark']}")
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise RuntimeError("Invalid Overpass response: missing elements list.")

    records: list[dict[str, Any]] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        center = element.get("center") if isinstance(element.get("center"), dict) else element
        latitude = center.get("lat")
        longitude = center.get("lon")
        if latitude is None or longitude is None:
            continue
        tags = dict(element.get("tags") or {})
        records.append(
            {
                "OSM_TYPE": str(element.get("type", "")),
                "OSM_ID": str(element.get("id", "")),
                **{str(key): value for key, value in tags.items()},
                "geometry": Point(float(longitude), float(latitude)),
            }
        )
    if not records:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")


def query_overpass_points_first_available(
    endpoint_urls: Sequence[str],
    query: str,
    *,
    timeout_seconds: int = 240,
) -> gpd.GeoDataFrame:
    """Return a complete response from the first healthy configured endpoint."""
    endpoints = tuple(dict.fromkeys(url for url in endpoint_urls if url))
    if not endpoints:
        endpoints = (DEFAULT_OVERPASS_URL,)
    errors: list[str] = []
    for endpoint in endpoints:
        try:
            return query_overpass_points(
                endpoint,
                query,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
    raise RuntimeError("All Overpass endpoints failed: " + "; ".join(errors))


def tiled_bboxes(
    bbox: tuple[float, float, float, float],
    *,
    maximum_span_degrees: float = 2.5,
) -> tuple[tuple[float, float, float, float], ...]:
    """Split a WGS84 bbox into deterministic bounded Overpass query tiles."""
    west, south, east, north = bbox
    if west >= east or south >= north:
        raise ValueError("Overpass bbox must have west < east and south < north.")
    if maximum_span_degrees <= 0:
        raise ValueError("maximum_span_degrees must be positive.")
    x_count = max(1, int(math.ceil((east - west) / maximum_span_degrees)))
    y_count = max(1, int(math.ceil((north - south) / maximum_span_degrees)))
    x_step = (east - west) / x_count
    y_step = (north - south) / y_count
    return tuple(
        (
            west + x_index * x_step,
            south + y_index * y_step,
            west + (x_index + 1) * x_step,
            south + (y_index + 1) * y_step,
        )
        for y_index in range(y_count)
        for x_index in range(x_count)
    )


def query_overpass_points_batched(
    endpoint_urls: Sequence[str],
    bbox: tuple[float, float, float, float],
    selectors: Iterable[str],
    *,
    maximum_span_degrees: float = 2.5,
    query_timeout_seconds: int = 180,
    request_timeout_seconds: int = 240,
) -> gpd.GeoDataFrame:
    """Query complete tiles with failover and deduplicate cross-tile objects.

    A failed tile fails the entire acquisition. This intentionally prevents a
    gateway timeout from being published as apparently complete OSM coverage.
    """
    selector_values = tuple(selectors)
    frames: list[gpd.GeoDataFrame] = []
    for tile in tiled_bboxes(bbox, maximum_span_degrees=maximum_span_degrees):
        query = build_overpass_query(
            tile,
            selector_values,
            timeout_seconds=query_timeout_seconds,
        )
        frames.append(
            query_overpass_points_first_available(
                endpoint_urls,
                query,
                timeout_seconds=request_timeout_seconds,
            )
        )
    nonempty = [frame for frame in frames if not frame.empty]
    if not nonempty:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    merged = gpd.GeoDataFrame(
        pd.concat(nonempty, ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326",
    )
    return merged.drop_duplicates(["OSM_TYPE", "OSM_ID"]).reset_index(drop=True)
