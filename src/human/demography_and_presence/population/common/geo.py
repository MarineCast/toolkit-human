"""Shared GeoPandas and Shapely compatibility helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import geopandas as gpd


def require_crs(gdf: gpd.GeoDataFrame, label: str) -> None:
    """Require a GeoDataFrame to have a declared CRS."""
    if gdf.crs is None:
        raise ValueError(f"{label} has no CRS.")


def repair_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Repair invalid geometries and drop null or empty rows."""
    require_crs(gdf, "GeoDataFrame")
    repaired = gdf.copy()
    try:
        repaired["geometry"] = repaired.geometry.make_valid()
    except Exception:
        repaired["geometry"] = repaired.geometry.buffer(0)
    return repaired[repaired.geometry.notna() & ~repaired.geometry.is_empty].copy()


def geometry_union(gdf: gpd.GeoDataFrame):
    """Return a unioned geometry across supported GeoPandas versions."""
    if gdf.empty:
        raise ValueError("Cannot union an empty GeoDataFrame.")
    try:
        return gdf.geometry.union_all()
    except AttributeError:
        return gdf.unary_union


def query_intersection_indices(gdf: gpd.GeoDataFrame, geometry: Any) -> list[int]:
    """Query a spatial index for candidate rows intersecting a geometry."""
    if gdf.empty:
        return []
    try:
        return list(gdf.sindex.query(geometry, predicate="intersects"))
    except TypeError:
        return list(gdf.sindex.query(geometry))


def select_intersecting(gdf: gpd.GeoDataFrame, geometry: Any) -> gpd.GeoDataFrame:
    """Return rows that exactly intersect a geometry after spatial-index prefiltering."""
    candidate_indices = query_intersection_indices(gdf, geometry)
    if not candidate_indices:
        return gdf.iloc[0:0].copy()
    candidates = gdf.iloc[candidate_indices].copy()
    return candidates[candidates.geometry.intersects(geometry)].copy()


def normalized_values(values: Iterable[object]) -> set[str]:
    """Normalize values for case-insensitive attribute filtering."""
    return {str(value).strip().upper() for value in values}
