"""H3 grid construction shared by country pipelines."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Callable

import geopandas as gpd
from shapely.geometry import Polygon
from tqdm.auto import tqdm

from .geo import geometry_union, repair_geometries

LOGGER = logging.getLogger(__name__)
H3_GEOGRAPHIC_CRS = "EPSG:4326"


@lru_cache(maxsize=1)
def _h3_backend() -> tuple[Callable[..., Any], ...]:
    """Load the project's H3 compatibility layer only when H3 work is requested."""
    try:
        from human.core.geo.h3 import (  # type: ignore[import-not-found]
            cell_to_parent,
            cell_to_polygon,
            grid_disk_set,
            polygon_to_cells,
        )
    except ImportError as exc:
        raise RuntimeError(
            "H3 grid operations require human.core.geo.h3 in the project environment."
        ) from exc
    return cell_to_parent, cell_to_polygon, grid_disk_set, polygon_to_cells


def geo_to_cells_compat(geometry: Any, resolution: int) -> set[str]:
    """Return H3 cells covering a WGS84 geometry."""
    *_, polygon_to_cells = _h3_backend()
    return set(polygon_to_cells(geometry, resolution))


def grid_disk_compat(cell: str, k: int) -> set[str]:
    """Return neighboring H3 cells across h3-py API versions."""
    _, _, grid_disk_set, _ = _h3_backend()
    return set(grid_disk_set(cell, k))


def cell_to_parent_compat(cell: str, resolution: int) -> str:
    """Return an H3 parent cell across h3-py API versions."""
    cell_to_parent, *_ = _h3_backend()
    return str(cell_to_parent(cell, resolution))


def h3_cell_to_polygon(cell: str) -> Polygon:
    """Convert an H3 cell ID to a WGS84 Shapely polygon."""
    _, cell_to_polygon, _, _ = _h3_backend()
    return cell_to_polygon(cell)


def h3_cells_to_polygons(cells: list[str]) -> list[Polygon]:
    """Convert H3 cells to polygons, using threads for larger grids."""
    if len(cells) < 10_000:
        return [h3_cell_to_polygon(cell) for cell in cells]
    max_workers = min(8, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(h3_cell_to_polygon, cells))


def build_h3_grid_for_boundary(
    boundary_gdf: gpd.GeoDataFrame,
    resolution: int,
    area_crs: str,
    output_crs: str,
    clip_to_boundary: bool = True,
) -> gpd.GeoDataFrame:
    """Build H3 cells intersecting a boundary and return them in ``output_crs``.

    H3 polygonization always occurs in WGS84. This avoids assigning a projected
    output CRS to longitude/latitude coordinates.
    """
    if resolution < 0:
        raise ValueError("H3 resolution must be non-negative.")
    if boundary_gdf.crs is None:
        raise ValueError("Boundary GeoDataFrame has no CRS.")
    boundary_wgs84 = repair_geometries(boundary_gdf.to_crs(H3_GEOGRAPHIC_CRS))
    boundary_geometry = geometry_union(boundary_wgs84)

    LOGGER.info("Generating H3 base cells at resolution %s", resolution)
    base_cells = geo_to_cells_compat(boundary_geometry, resolution)
    if not base_cells:
        raise RuntimeError("H3 polygon fill returned no base cells for the boundary.")

    expanded_cells = set(base_cells)
    for cell in tqdm(base_cells, desc="Expanding H3 edge cells"):
        expanded_cells.update(grid_disk_compat(cell, 1))

    sorted_cells = sorted(expanded_cells)
    h3_gdf = gpd.GeoDataFrame(
        {"h3": sorted_cells},
        geometry=h3_cells_to_polygons(sorted_cells),
        crs=H3_GEOGRAPHIC_CRS,
    )

    if clip_to_boundary:
        LOGGER.info("Clipping %s H3 cells to boundary", len(h3_gdf))
        clipped = gpd.overlay(
            h3_gdf,
            boundary_wgs84[["geometry"]],
            how="intersection",
            keep_geom_type=False,
        )
        clipped = repair_geometries(clipped)
        clipped["h3_resolution"] = resolution
        clipped = clipped.dissolve(
            by="h3",
            as_index=False,
            aggfunc={"h3_resolution": "first"},
        )
        clipped = repair_geometries(clipped)
    else:
        LOGGER.info(
            "Selecting full H3 cells intersecting boundary from %s candidates",
            len(h3_gdf),
        )
        try:
            candidate_indices = list(h3_gdf.sindex.query(boundary_geometry, predicate="intersects"))
        except TypeError:
            candidate_indices = list(h3_gdf.sindex.query(boundary_geometry))
        clipped = h3_gdf.iloc[candidate_indices].copy()
        clipped = clipped[clipped.geometry.intersects(boundary_geometry)].copy()
        clipped["h3_resolution"] = resolution
        clipped = repair_geometries(clipped)

    if clipped.empty:
        raise RuntimeError("No H3 cells intersect the supplied boundary.")

    clipped_area = clipped.to_crs(area_crs)
    clipped_area["h3_area_m2"] = clipped_area.geometry.area
    clipped_area = clipped_area[clipped_area["h3_area_m2"] > 0].copy()
    if clipped_area["h3"].duplicated().any():
        duplicate_count = int(clipped_area["h3"].duplicated().sum())
        raise RuntimeError(
            f"H3 grid contains {duplicate_count} duplicate cell rows after construction."
        )
    LOGGER.info("Built %s H3 cells", len(clipped_area))
    return clipped_area.to_crs(output_crs)
