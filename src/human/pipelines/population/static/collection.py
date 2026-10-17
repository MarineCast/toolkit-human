"""Explicit collection support for complete cells and all requested neighborhoods."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

import h3
import shapely

from .geometry import cell_polygon, radius_polygon, target_cells

METHOD = "full_cells_and_geodesic_neighborhood_union.v1"


def geometry_digest(geometry):
    return hashlib.sha256(shapely.to_wkb(shapely.normalize(geometry))).hexdigest()


@dataclass(frozen=True)
class CollectionFootprint:
    geometry: object
    metadata: dict

    def validate(self):
        if (self.geometry.is_empty or not self.geometry.is_valid
                or self.geometry.geom_type not in ("Polygon", "MultiPolygon")
                or self.metadata.get("geometry_wkb_sha256") != geometry_digest(self.geometry)
                or self.metadata.get("method") != METHOD):
            raise ValueError("Invalid collection footprint or geometry binding")
        return self


def collection_footprint(cells, resolution, radii_km=(25, 50, 100), *, tolerance_m=25, edge_step_m=1000):
    """Cover every target, including disconnected regions and interior cells.

    Acquisition uses outer circles with two sagitta tolerances of margin. Scientific
    calculations retain their original unexpanded radii. Full polygons are selected,
    never clipped to this collection geometry or to the displayed registry.
    """
    cells = target_cells(None, cells, resolution, 100000)
    radii = tuple(float(r) for r in radii_km)
    if (not radii or tuple(sorted(set(radii))) != radii
            or any(not math.isfinite(r) or not 0 < r <= 500 for r in radii)
            or not math.isfinite(tolerance_m) or not 0 < tolerance_m <= 100
            or not math.isfinite(edge_step_m) or not 0 < edge_step_m <= 10000):
        raise ValueError("Invalid collection radii or geometry tolerances")
    parts = []
    margin_m = 2 * tolerance_m
    for start in range(0, len(cells), 256):
        batch = []
        for cell in cells[start:start+256]:
            lat, lon = h3.cell_to_latlng(cell)
            batch.extend([cell_polygon(cell, edge_step_m),
                radius_polygon(lat, lon, radii[-1] + margin_m/1000, tolerance_m)])
        parts.append(shapely.union_all(batch))
    geometry = shapely.normalize(shapely.union_all(parts))
    west, south, east, north = geometry.bounds
    if (west <= -180 or east >= 180 or south <= -85 or north >= 85
            or east-west > 30 or north-south > 30):
        raise ValueError("Collection exceeds bounded regional projection limits")
    metadata = {"schema": "human.population.collection.v1", "method": METHOD,
        "crs": "EPSG:4326", "target_count": len(cells), "h3_resolution": resolution,
        "target_membership_sha256": hashlib.sha256(("\n".join(cells)+"\n").encode()).hexdigest(),
        "radii_km": list(radii), "maximum_radius_km": radii[-1],
        "distance_basis": "WGS84 ellipsoidal distance from each H3 center",
        "acquisition_margin_m": margin_m, "buffer_tolerance_m": tolerance_m,
        "edge_step_m": edge_step_m, "source_extent_wgs84": list(geometry.bounds),
        "geometry_wkb_sha256": geometry_digest(geometry),
        "selection": "Conservative bounding envelope of the union of all target-cell polygons and maximum-radius neighborhoods; retain intersecting full original source polygons.",
        "coverage_semantics": "Acquisition footprint is not an assertion of complete census values or geographic support."}
    return CollectionFootprint(geometry, metadata).validate()
