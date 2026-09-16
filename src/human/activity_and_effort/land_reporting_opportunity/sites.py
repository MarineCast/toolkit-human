"""Represented access geometry and conservative, budget-preserving sampling.

No proximity deduplication, land-cell substitution, or inferred shoreline.
Source group IDs identify parent destinations only within a source dataset.
"""

from __future__ import annotations

import hashlib
import json

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from shapely import from_wkb, line_merge, normalize, union_all, voronoi_polygons
from shapely.geometry import MultiPoint

from ...viewshed.prepare.area.sampling import sample_points_in_source_geometry

SITE_ALGORITHM_VERSION = "source_cell_local_destinations_v3"
SAMPLE_ALGORITHM_VERSION = "parent_budget_nested_samples_v1"


def _text(value: object) -> str | None:
    return None if value is None or pd.isna(value) or str(value).strip() == "" else str(value)


def build_observation_sites(facilities: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Union computational pieces by explicit parent identity, retaining lineage.

    Different source records are retained unless a shared source group asserts
    destination identity. Cross-provider duplicates require an explicit shared
    PARENT_SITE_ID; names and proximity do not establish identity.
    """
    required = {"SOURCE_DATASET", "SOURCE_RECORD_ID", "PUBLIC_ACCESS_STATE", "ACCESS_EVIDENCE_TIER"}
    if missing := required - set(facilities):
        raise ValueError(f"Facilities missing lineage/access columns: {sorted(missing)}")
    if facilities.crs is None:
        raise ValueError("Facilities require a declared CRS")
    frame = facilities.to_crs(4326).copy()
    if "REPRESENTED_GEOMETRY_WKB" in frame:
        # Source producer stores original WGS84 support alongside its facility point.
        frame.geometry = [
            from_wkb(value) if value is not None and not pd.isna(value) else point
            for value, point in zip(frame.REPRESENTED_GEOMETRY_WKB, frame.geometry, strict=True)
        ]
    if frame.empty:
        raise ValueError("Strict access-conditioned output unsupported: empty facilities inventory")
    keys = []
    for _, row in frame.iterrows():
        dataset, record = _text(row.SOURCE_DATASET), _text(row.SOURCE_RECORD_ID)
        if dataset is None or record is None:
            raise ValueError("Site source identity is unavailable")
        parent = _text(row.get("PARENT_SITE_ID"))
        group = _text(row.get("SOURCE_GROUP_ID"))
        keys.append(parent or f"{dataset}:{group or record}")
    frame["PARENT_SITE_ID"] = keys
    # Keep global parent identity and declared source-cell fragments. Fragment
    # shares are computed on deduplicated represented geometry, never row counts.
    fragments = []
    for parent, parts in frame.groupby("PARENT_SITE_ID", sort=True):
        cells = parts.H3_INDEX.dropna().unique() if "H3_INDEX" in parts else []
        groups = list(parts.groupby("H3_INDEX", sort=True)) if len(cells) > 1 else [(None, parts)]
        measures = []
        for cell, piece in groups:
            geometry = union_all(list(piece.geometry))
            metric = gpd.GeoSeries([geometry], crs=4326).to_crs(6933).iloc[0]
            measures.append(
                metric.area
                if metric.area > 0
                else metric.length if metric.length > 0 else len(getattr(metric, "geoms", [metric]))
            )
        shares = np.array(measures) / sum(measures)
        for (cell, piece), share in zip(groups, shares, strict=True):
            fragments.append((parent, cell, piece, float(share)))
    rows = []
    for parent, fragment_cell, parts, fragment_share in fragments:
        geometries = list(parts.geometry)
        usable = all(
            g is not None
            and not g.is_empty
            and g.is_valid
            and g.geom_type
            in {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}
            for g in geometries
        )
        access = set(parts.PUBLIC_ACCESS_STATE)
        eligible = access == {"public"}
        geometry = normalize(union_all(geometries)) if usable else None
        if geometry is not None and geometry.geom_type == "MultiLineString":
            geometry = normalize(line_merge(geometry))
        if geometry is not None:
            # Remove redundant collinear subdivision vertices before reprojection.
            geometry = normalize(geometry.simplify(0, preserve_topology=True))
        # Mixed-dimensional representations may describe different facilities;
        # preserve but flag rather than replacing them by their convex hull.
        usable = usable and geometry.geom_type != "GeometryCollection"
        tier = sorted(set(parts.ACCESS_EVIDENCE_TIER.astype(str)))
        verified = eligible and all(t == "authoritative_verified" for t in tier)
        # A supplied source-cell identity remains stable under computational splits.
        cells = set(parts.H3_INDEX.dropna().astype(str)) if "H3_INDEX" in parts else set()
        if len(cells) > 1:
            raise ValueError(f"Parent site {parent} spans multiple declared source cells")
        source = (
            next(iter(cells))
            if cells
            else (
                h3.latlng_to_cell(
                    geometry.representative_point().y, geometry.representative_point().x, 7
                )
                if usable
                else None
            )
        )
        if source is not None and h3.get_resolution(source) != 7:
            raise ValueError("Observation-site activity source must be H3 R7")
        rows.append(
            {
                "PARENT_SITE_ID": parent,
                "SITE_ID": hashlib.sha256(f"{parent}:{fragment_cell or ''}".encode()).hexdigest()[
                    :24
                ],
                "PARENT_FRAGMENT_SHARE": fragment_share,
                "source_h3": source,
                "ACCESS_EVIDENCE_TIER": json.dumps(tier),
                "MAPPED_ELIGIBLE": eligible and usable,
                "VERIFIED_ELIGIBLE": verified and usable,
                "GEOMETRY_TYPE": geometry.geom_type if geometry is not None else "unavailable",
                "GEOMETRY_STATE": "supported" if usable else "unsupported",
                "SUPPORT_LIMITATION": (
                    "point-only access support; authoritative shoreline lines are not connected"
                    if geometry is not None and geometry.geom_type in {"Point", "MultiPoint"}
                    else "represented source geometry only; mapping completeness unknown"
                ),
                "SOURCE_LINEAGE_JSON": json.dumps(
                    sorted(
                        set(f"{r.SOURCE_DATASET}:{r.SOURCE_RECORD_ID}" for r in parts.itertuples())
                    )
                ),
                "SITE_ALGORITHM_VERSION": SITE_ALGORITHM_VERSION,
                "geometry": geometry,
            }
        )
    sites = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)
    for scenario in ("MAPPED", "VERIFIED"):
        eligible = sites[f"{scenario}_ELIGIBLE"]
        counts = sites.loc[eligible].groupby("source_h3").size()
        sites[f"{scenario}_SITE_WEIGHT"] = np.where(
            eligible, 1.0 / sites.source_h3.map(counts), 0.0
        )
        allocated = sites.groupby("source_h3")[f"{scenario}_SITE_WEIGHT"].transform("sum")
        sites[f"{scenario}_SOURCE_UNALLOCATED_WEIGHT"] = 1.0 - allocated
        if (allocated > 1 + 1e-12).any():
            raise ValueError("Fragment allocation exceeded source activity budget")
    return sites


def sample_observation_sites(
    sites: gpd.GeoDataFrame,
    *,
    samples_per_site: int = 20,
    max_design_points: int = 80,
    projected_crs: str = "EPSG:32610",
) -> gpd.GeoDataFrame:
    """Use nested metric polygon designs, nested line midpoints, or exact points.

    A parent geometry is unioned before sampling. Polygon sample weights are
    clipped metric Voronoi area; line weights use one-dimensional Voronoi lengths.
    """
    if not 1 <= samples_per_site <= max_design_points:
        raise ValueError("Require 1 <= samples_per_site <= max_design_points")
    rows = []
    for _, site in sites.loc[sites.MAPPED_ELIGIBLE].sort_values("PARENT_SITE_ID").iterrows():
        projected = gpd.GeoSeries([site.geometry], crs=sites.crs).to_crs(projected_crs).iloc[0]
        kind = projected.geom_type
        if kind in {"Point", "MultiPoint"}:
            points = [projected] if kind == "Point" else list(projected.geoms)
            weights = np.full(len(points), 1.0 / len(points))
            method = "equal_represented_points"
        elif kind in {"LineString", "MultiLineString"}:
            # Van der Corput dyadic midpoint order is nested and independent of n.
            positions = []
            level = 1
            while len(positions) < samples_per_site:
                positions.extend((2 * i + 1) / (2**level) for i in range(2 ** (level - 1)))
                level += 1
            positions = np.asarray(positions[:samples_per_site])
            points = [projected.interpolate(float(p), normalized=True) for p in positions]
            order = np.argsort(positions)
            edges = np.r_[0.0, (positions[order][:-1] + positions[order][1:]) / 2, 1.0]
            weights = np.empty(len(points))
            weights[order] = np.diff(edges)
            method = "nested_line_voronoi_length"
        else:
            dense = sample_points_in_source_geometry(
                site.source_h3,
                projected,
                max_design_points,
                geometry_crs=projected_crs,
                projected_crs=projected_crs,
                max_design_points=max_design_points,
            ).to_crs(projected_crs)
            points = list(dense.geometry.iloc[:samples_per_site])
            if len(points) == 1:
                weights = np.array([1.0])
            else:
                cells = list(
                    voronoi_polygons(MultiPoint(points), extend_to=projected.envelope).geoms
                )
                weights = np.array(
                    [
                        next(
                            cell.intersection(projected).area
                            for cell in cells
                            if cell.covers(point)
                        )
                        for point in points
                    ]
                )
                weights /= weights.sum()
            method = "nested_polygon_clipped_voronoi_area"
        geographic = gpd.GeoSeries(points, crs=projected_crs).to_crs(4326)
        for index, (point, weight) in enumerate(zip(geographic, weights, strict=True)):
            rows.append(
                {
                    "PARENT_SITE_ID": site.PARENT_SITE_ID,
                    "SITE_ID": site.SITE_ID,
                    "source_h3": site.source_h3,
                    "sample_id": f"{site.SITE_ID}:{index}",
                    "SAMPLE_WEIGHT": float(weight),
                    "SAMPLE_WEIGHT_METHOD": method,
                    "MAPPED_SITE_WEIGHT": site.MAPPED_SITE_WEIGHT,
                    "VERIFIED_SITE_WEIGHT": site.VERIFIED_SITE_WEIGHT,
                    "SOURCE_LINEAGE_JSON": site.SOURCE_LINEAGE_JSON,
                    "SAMPLE_ALGORITHM_VERSION": SAMPLE_ALGORITHM_VERSION,
                    "geometry": point,
                }
            )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)
