"""Bounded regional H3 and WGS84 geodesic geometry; no provider dependencies."""
from __future__ import annotations

import math
import h3
import numpy as np
from pyproj import Geod
from shapely.geometry import Polygon, box

GEOD = Geod(ellps="WGS84")


def target_cells(bbox, target_h3, resolution, max_targets):
    if isinstance(resolution, bool) or not isinstance(resolution, int) or not 0 <= resolution <= 15:
        raise ValueError("h3_res must be an integer in [0, 15]")
    if (bbox is None) == (target_h3 is None):
        raise ValueError("Provide exactly one of bbox or target_h3")
    if bbox is not None:
        if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox):
            raise ValueError("bbox must contain four finite WGS84 coordinates")
        west, south, east, north = bbox
        if not (-180 < west < east < 180 and -80 < south < north < 80):
            raise ValueError("bbox must be ordered, non-polar and not cross the antimeridian")
        # Bound allocation before a high-resolution polyfill can exhaust memory.
        area, _ = GEOD.geometry_area_perimeter(box(*bbox))
        estimate = abs(area) / (h3.average_hexagon_area(resolution, unit="m^2") * 0.4)
        if estimate > max_targets * 2:
            raise ValueError("bbox/resolution exceeds max_targets; use a bounded explicit target list")
        cells = h3.geo_to_cells(box(west-1e-10, south-1e-10, east+1e-10, north+1e-10).__geo_interface__, resolution)
        cells = [c for c in cells if west <= h3.cell_to_latlng(c)[1] <= east
                 and south <= h3.cell_to_latlng(c)[0] <= north]
    else:
        cells = list(target_h3)
    if not cells or len(cells) > max_targets:
        raise ValueError(f"Expected 1..{max_targets} target cells, got {len(cells)}")
    if any(not isinstance(c, str) or not h3.is_valid_cell(c) for c in cells):
        raise ValueError("Invalid target H3 identity")
    cells = [h3.int_to_str(h3.str_to_int(c)) for c in cells]
    if len(cells) != len(set(cells)):
        raise ValueError("Duplicate target H3 identities")
    if any(h3.get_resolution(c) != resolution for c in cells):
        raise ValueError("Target H3 cells must all match h3_res")
    centers = np.array([h3.cell_to_latlng(c) for c in cells])
    if np.ptp(centers[:, 1]) > 30 or np.ptp(centers[:, 0]) > 30 or (abs(centers[:, 0]) >= 80).any():
        raise ValueError("Only non-polar regional extents spanning at most 30 degrees are supported")
    return sorted(cells)


def cell_polygon(cell, edge_step_m=1000):
    vertices = [(lon, lat) for lat, lon in h3.cell_to_boundary(cell)]
    output = []
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        output.append(a)
        _, _, length = GEOD.inv(*a, *b)
        count = max(0, math.ceil(length / edge_step_m) - 1)
        if count:
            output.extend(GEOD.npts(*a, *b, count))
    return Polygon(output)


def radius_vertex_count(radius_km, tolerance_m):
    return max(64, math.ceil(math.pi / math.acos(1 - min(tolerance_m / (radius_km * 1000), 0.5))))


def radius_polygon(lat, lon, radius_km, tolerance_m, *, vertices=None):
    radius_m = radius_km * 1000
    count = max(radius_vertex_count(radius_km, tolerance_m), vertices or 0)
    azimuths = np.linspace(0, 360, count, endpoint=False)
    lons, lats, _ = GEOD.fwd(np.full(count, lon), np.full(count, lat), azimuths,
                            np.full(count, radius_m))
    if np.ptp(lons) > 180 or max(abs(lats)) >= 85:
        raise ValueError("Required radius support crosses antimeridian or polar limit")
    return Polygon(zip(lons, lats))


def source_bounds(cells, radius_km, tolerance_m):
    # Every target's maximum circle plus its entire hexagon; padded for polygonal arcs.
    bounds = []
    for cell in cells:
        lat, lon = h3.cell_to_latlng(cell)
        bounds.extend((radius_polygon(lat, lon, radius_km, tolerance_m).bounds, cell_polygon(cell).bounds))
    bounds = np.array(bounds)
    south, north = bounds[:, 1].min(), bounds[:, 3].max()
    pad_lat = 2 * tolerance_m / 110000
    pad_lon = pad_lat / math.cos(math.radians(max(abs(south), abs(north))))
    return (float(bounds[:, 0].min() - pad_lon), float(south - pad_lat),
            float(bounds[:, 2].max() + pad_lon), float(north + pad_lat))
