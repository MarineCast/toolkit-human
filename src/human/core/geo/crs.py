"""Coordinate reference system helpers."""

from __future__ import annotations

from typing import Any


def pick_meter_crs_from_bbox(bbox_geom: Any, fallback_epsg: int = 3857) -> int:
    """Pick a UTM EPSG code from a WGS84 bbox-like geometry centroid.

    Returns a northern-hemisphere UTM code (EPSG:326xx) for non-negative
    latitudes and southern-hemisphere UTM code (EPSG:327xx) otherwise. If the
    centroid longitude does not resolve to a valid UTM zone, returns
    ``fallback_epsg``.
    """
    centroid = bbox_geom.centroid
    lon, lat = float(centroid.x), float(centroid.y)
    zone = int((lon + 180) // 6) + 1
    if zone < 1 or zone > 60:
        return int(fallback_epsg)
    return (32600 + zone) if lat >= 0 else (32700 + zone)
