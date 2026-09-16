"""Spatial join helpers."""

from __future__ import annotations

from typing import Any

import geopandas as gpd


def align_crs(
    left: gpd.GeoDataFrame,
    right: gpd.GeoDataFrame,
    *,
    target_crs: Any | None = None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Return two GeoDataFrames in a common CRS."""
    if left.crs is None:
        raise ValueError("Left GeoDataFrame has no CRS.")
    if right.crs is None:
        raise ValueError("Right GeoDataFrame has no CRS.")

    crs = target_crs or left.crs
    left_out = left if left.crs == crs else left.to_crs(crs)
    right_out = right if right.crs == crs else right.to_crs(crs)
    return left_out, right_out


def sjoin_aligned(
    left: gpd.GeoDataFrame,
    right: gpd.GeoDataFrame,
    *,
    how: str = "inner",
    predicate: str = "intersects",
    target_crs: Any | None = None,
    **kwargs: Any,
) -> gpd.GeoDataFrame:
    """Run ``geopandas.sjoin`` after normalizing both inputs to one CRS."""
    left_aligned, right_aligned = align_crs(left, right, target_crs=target_crs)
    return gpd.sjoin(
        left_aligned,
        right_aligned,
        how=how,
        predicate=predicate,
        **kwargs,
    )


def sjoin_nearest_aligned(
    left: gpd.GeoDataFrame,
    right: gpd.GeoDataFrame,
    *,
    how: str = "left",
    max_distance: float | None = None,
    distance_col: str | None = None,
    target_crs: Any | None = None,
    **kwargs: Any,
) -> gpd.GeoDataFrame:
    """Run ``geopandas.sjoin_nearest`` after normalizing both inputs to one CRS."""
    left_aligned, right_aligned = align_crs(left, right, target_crs=target_crs)
    return gpd.sjoin_nearest(
        left_aligned,
        right_aligned,
        how=how,
        max_distance=max_distance,
        distance_col=distance_col,
        **kwargs,
    )
