"""Build public-access facilities and separate accessible-waterfront fractions."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString
from shapely.ops import transform, unary_union

from human.core.geo.h3 import cell_to_polygon, grid_disk, latlng_to_cell
from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_parquet,
    load_manifest,
    manifest_payload,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, load_public_shore_config


def _column(frame: pd.DataFrame, *names: str) -> pd.Series:
    lookup = {str(column).casefold(): column for column in frame.columns}
    for name in names:
        if name.casefold() in lookup:
            return frame[lookup[name.casefold()]]
    return pd.Series([None] * len(frame), index=frame.index, dtype="object")


def _clean(value: Any) -> str | None:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _representative_points(frame: gpd.GeoDataFrame) -> gpd.GeoSeries:
    return frame.geometry.map(
        lambda geometry: (
            geometry
            if geometry is None or geometry.is_empty or geometry.geom_type == "Point"
            else geometry.representative_point()
        )
    )


PUBLIC_ACCESS_VALUES = {"yes", "public", "permissive", "designated"}
RESTRICTED_ACCESS_VALUES = {
    "no",
    "private",
    "customers",
    "destination",
    "permit",
    "delivery",
}


def pedestrian_access_state(
    access: Any,
    foot: Any,
    access_conditional: Any = None,
    foot_conditional: Any = None,
) -> str:
    """Classify explicit pedestrian permission without treating missing as public."""
    access_value = (_clean(access) or "").casefold()
    foot_value = (_clean(foot) or "").casefold()
    if _clean(foot_conditional) or _clean(access_conditional):
        return "conditional"
    effective = foot_value or access_value
    if effective in PUBLIC_ACCESS_VALUES:
        return "public"
    if effective in RESTRICTED_ACCESS_VALUES:
        return "restricted"
    return "unknown"


def _osm_feature_type(frame: pd.DataFrame) -> pd.Series:
    columns = ("waterway", "natural", "leisure", "highway", "tourism")
    values = [_column(frame, column).map(_clean) for column in columns]
    result = pd.Series([None] * len(frame), index=frame.index, dtype="object")
    for value in values:
        result = result.where(result.notna(), value)
    return result


def _bc_shore_distances(
    sites: gpd.GeoDataFrame,
    shoreline: gpd.GeoDataFrame,
    *,
    length_crs: str,
) -> pd.Series:
    if sites.empty or shoreline.empty:
        return pd.Series([math.nan] * len(sites), index=sites.index, dtype="float64")
    shore_union = unary_union(
        [
            geometry
            for geometry in shoreline.to_crs(length_crs).geometry
            if geometry is not None and not geometry.is_empty
        ]
    )
    if shore_union is None or shore_union.is_empty:
        return pd.Series([math.nan] * len(sites), index=sites.index, dtype="float64")
    projected = gpd.GeoSeries(_representative_points(sites), crs=sites.crs).to_crs(length_crs)
    return projected.distance(shore_union)


def normalize_facilities(
    wa_points: gpd.GeoDataFrame,
    osm_points: gpd.GeoDataFrame,
    *,
    h3_resolution: int,
    bc_recreation_sites: gpd.GeoDataFrame | None = None,
    bc_shorezone_lines: gpd.GeoDataFrame | None = None,
    reference_shoreline_lines: gpd.GeoDataFrame | None = None,
    length_crs: str = "EPSG:32610",
    bc_shore_connection_distance_m: float = 500.0,
    osm_shore_connection_distance_m: float = 500.0,
) -> gpd.GeoDataFrame:
    wa_id = _column(wa_points, "OBJECTID", "ECYBEACHID").astype(str)
    wa = gpd.GeoDataFrame(
        {
            "ACCESS_SITE_ID": "wa_ecology:" + wa_id,
            "JURISDICTION": "WA",
            "COUNTRY_CODE": "US",
            "SOURCE_DATASET": "wa_public_access_points",
            "REPRESENTED_GEOMETRY_WKB": wa_points.set_crs(wa_points.crs or "EPSG:4326")
            .to_crs(4326)
            .geometry.to_wkb(),
            "SOURCE_RECORD_ID": wa_id,
            "SOURCE_GROUP_ID": _column(wa_points, "ECYBEACHID").map(_clean),
            "NAME": _column(wa_points, "Beach_Name", "BEACH_NAME").map(_clean),
            "PUBLIC_ACCESS_STATE": "public",
            "ACCESS_EVIDENCE_TIER": "authoritative_verified",
            "SHORE_CONNECTION_STATE": "authoritative_access_record",
            "SHORE_DISTANCE_M": 0.0,
            "ACCESS_TAG": None,
            "FOOT_ACCESS_TAG": None,
            "ACCESS_CONDITIONAL_TAG": None,
            "FOOT_CONDITIONAL_TAG": None,
            "OPENING_HOURS": None,
            "OPERATOR": None,
            "OWNERSHIP": None,
            "PARKING_TAG": None,
            "CLOSURE_STATE": "unknown",
            "FACILITY_TYPE": _column(wa_points, "Primary_Acccess_Type", "Primary_Access_Type").map(
                _clean
            ),
            "ACCESS_FEE_STATE": _column(wa_points, "Access_Fee").map(
                lambda value: _clean(value) or "unknown"
            ),
            "ADA_FEATURE_STATE": _column(wa_points, "Primary_ADA_Features").map(
                lambda value: _clean(value) or "unknown"
            ),
            "SOURCE_COVERAGE_STATUS": "partial",
            "MEASUREMENT_STATUS": MeasurementStatus.OBSERVED.value,
        },
        geometry=_representative_points(wa_points),
        crs=wa_points.crs or "EPSG:4326",
    ).to_crs("EPSG:4326")

    osm_distances = pd.Series([math.nan] * len(osm_points), index=osm_points.index, dtype="float64")
    if reference_shoreline_lines is not None and not reference_shoreline_lines.empty:
        osm_distances = _bc_shore_distances(
            osm_points,
            reference_shoreline_lines,
            length_crs=length_crs,
        )
        coastal_osm = osm_distances.le(osm_shore_connection_distance_m)
        osm_points = osm_points.loc[coastal_osm].copy()
        osm_distances = osm_distances.loc[coastal_osm]
    osm_id = (
        _column(osm_points, "OSM_TYPE").astype(str)
        + "/"
        + _column(osm_points, "OSM_ID").astype(str)
    )
    osm_access = _column(osm_points, "access").map(_clean)
    osm_foot = _column(osm_points, "foot").map(_clean)
    osm_access_conditional = _column(osm_points, "access:conditional").map(_clean)
    osm_foot_conditional = _column(osm_points, "foot:conditional").map(_clean)
    osm_states = pd.Series(
        [
            pedestrian_access_state(access, foot, access_conditional, foot_conditional)
            for access, foot, access_conditional, foot_conditional in zip(
                osm_access,
                osm_foot,
                osm_access_conditional,
                osm_foot_conditional,
                strict=True,
            )
        ],
        index=osm_points.index,
        dtype="object",
    )
    osm = gpd.GeoDataFrame(
        {
            "ACCESS_SITE_ID": "osm:" + osm_id,
            "JURISDICTION": "cross_border",
            "COUNTRY_CODE": None,
            "SOURCE_DATASET": "osm_shore_access",
            "REPRESENTED_GEOMETRY_WKB": osm_points.set_crs(osm_points.crs or "EPSG:4326")
            .to_crs(4326)
            .geometry.to_wkb(),
            "SOURCE_RECORD_ID": osm_id,
            "SOURCE_GROUP_ID": None,
            "NAME": _column(osm_points, "name").map(_clean),
            "PUBLIC_ACCESS_STATE": osm_states,
            "ACCESS_EVIDENCE_TIER": osm_states.map(
                {
                    "public": "osm_explicit_public",
                    "restricted": "osm_explicit_restricted",
                    "conditional": "osm_conditional_access",
                }
            ).fillna("osm_mapped_access_unknown"),
            "SHORE_CONNECTION_STATE": (
                "within_official_shoreline_threshold"
                if reference_shoreline_lines is not None and not reference_shoreline_lines.empty
                else "community_mapped_candidate_shore_connection_unchecked"
            ),
            "SHORE_DISTANCE_M": osm_distances.astype(float),
            "ACCESS_TAG": osm_access,
            "FOOT_ACCESS_TAG": osm_foot,
            "ACCESS_CONDITIONAL_TAG": osm_access_conditional,
            "FOOT_CONDITIONAL_TAG": osm_foot_conditional,
            "OPENING_HOURS": _column(osm_points, "opening_hours").map(_clean),
            "OPERATOR": _column(osm_points, "operator").map(_clean),
            "OWNERSHIP": _column(osm_points, "ownership").map(_clean),
            "PARKING_TAG": _column(osm_points, "parking").map(_clean),
            "CLOSURE_STATE": "unknown",
            "FACILITY_TYPE": _osm_feature_type(osm_points),
            "ACCESS_FEE_STATE": _column(osm_points, "fee").map(
                lambda value: _clean(value) or "unknown"
            ),
            "ADA_FEATURE_STATE": _column(osm_points, "wheelchair").map(
                lambda value: _clean(value) or "unknown"
            ),
            "SOURCE_COVERAGE_STATUS": "community_mapped_partial",
            "MEASUREMENT_STATUS": MeasurementStatus.OBSERVED.value,
        },
        geometry=_representative_points(osm_points),
        crs=osm_points.crs or "EPSG:4326",
    ).to_crs("EPSG:4326")

    frames: list[gpd.GeoDataFrame] = [wa, osm]
    bc_sites = bc_recreation_sites
    bc_shore = bc_shorezone_lines
    if bc_sites is not None and bc_shore is not None and not bc_sites.empty and not bc_shore.empty:
        distances = _bc_shore_distances(bc_sites, bc_shore, length_crs=length_crs)
        coastal_mask = distances.le(bc_shore_connection_distance_m)
        coastal = bc_sites.loc[coastal_mask].copy()
        coastal_distances = distances.loc[coastal_mask]
        bc_id = _column(coastal, "FOREST_FILE_ID", "OBJECTID").astype(str)
        closure = _column(coastal, "CLOSURE_DESCRIPTION").map(_clean)
        # Official facility + shoreline proximity is not evidence of legal,
        # traversable access to that shore. Absence of a closure is not proof.
        bc_state = closure.notna().map({True: "restricted", False: "unknown"})
        bc = gpd.GeoDataFrame(
            {
                "ACCESS_SITE_ID": "bc_rstv:" + bc_id,
                "JURISDICTION": "BC",
                "COUNTRY_CODE": "CA",
                "SOURCE_DATASET": "bc_recreation_sites",
                "SOURCE_RECORD_ID": bc_id,
                "SOURCE_GROUP_ID": _column(coastal, "FOREST_FILE_ID").map(_clean),
                "NAME": _column(coastal, "PROJECT_NAME", "SITE_LOCATION").map(_clean),
                "PUBLIC_ACCESS_STATE": bc_state,
                "ACCESS_EVIDENCE_TIER": bc_state.map(
                    {
                        "unknown": "authoritative_facility_candidate",
                        "restricted": "authoritative_restricted",
                    }
                ),
                "SHORE_CONNECTION_STATE": "within_official_shorezone_threshold",
                "SHORE_DISTANCE_M": coastal_distances.astype(float),
                "ACCESS_TAG": _column(coastal, "ACCESS_DESC1").map(_clean),
                "FOOT_ACCESS_TAG": None,
                "ACCESS_CONDITIONAL_TAG": None,
                "FOOT_CONDITIONAL_TAG": None,
                "OPENING_HOURS": None,
                "OPERATOR": _column(coastal, "OPERATOR_CLIENT_NAME").map(_clean),
                "OWNERSHIP": "Province of British Columbia recreation site",
                "PARKING_TAG": None,
                "CLOSURE_STATE": closure.notna().map(
                    {True: "closed_or_restricted", False: "no_closure_reported"}
                ),
                "FACILITY_TYPE": "recreation_site",
                "ACCESS_FEE_STATE": "unknown",
                "ADA_FEATURE_STATE": "unknown",
                "SOURCE_COVERAGE_STATUS": "official_inventory_partial",
                "MEASUREMENT_STATUS": MeasurementStatus.OBSERVED.value,
            },
            geometry=_representative_points(coastal),
            crs=coastal.crs or "EPSG:4326",
        ).to_crs("EPSG:4326")
        frames.append(bc)

    facilities = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    facilities = facilities.loc[facilities.geometry.notna() & ~facilities.geometry.is_empty].copy()
    facilities["H3_INDEX"] = facilities.geometry.map(
        lambda point: latlng_to_cell(point.y, point.x, h3_resolution)
    )
    facilities["H3_RESOLUTION"] = h3_resolution
    facilities = facilities.sort_values(["SOURCE_DATASET", "SOURCE_RECORD_ID"]).reset_index(
        drop=True
    )
    if facilities["ACCESS_SITE_ID"].duplicated().any():
        raise ValueError("Public-shore ACCESS_SITE_ID must be unique within each source.")
    return facilities


def _line_parts(geometry: Any) -> Iterable[LineString]:
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, LineString):
        yield geometry
    elif isinstance(geometry, MultiLineString):
        yield from geometry.geoms
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _line_parts(part)


def line_lengths_by_h3(
    frame: gpd.GeoDataFrame,
    *,
    h3_resolution: int,
    length_crs: str,
    sample_spacing_m: float,
) -> dict[str, float]:
    """Measure dissolved line length in each intersected H3 cell."""
    if frame.empty:
        return {}
    source = frame.to_crs("EPSG:4326")
    projected = source.to_crs(length_crs)
    union_m = unary_union(
        [
            geometry
            for geometry in projected.geometry
            if geometry is not None and not geometry.is_empty
        ]
    )
    if union_m is None or union_m.is_empty:
        return {}
    inverse = Transformer.from_crs(length_crs, "EPSG:4326", always_xy=True)
    forward = Transformer.from_crs("EPSG:4326", length_crs, always_xy=True)
    candidates: set[str] = set()
    for line in _line_parts(union_m):
        steps = max(1, int(math.ceil(line.length / sample_spacing_m)))
        for index in range(steps + 1):
            point_m = line.interpolate(min(line.length, index * line.length / steps))
            longitude, latitude = inverse.transform(point_m.x, point_m.y)
            center = latlng_to_cell(latitude, longitude, h3_resolution)
            candidates.update(grid_disk(center, 1))

    lengths: dict[str, float] = {}
    for cell in sorted(candidates):
        polygon_m = transform(forward.transform, cell_to_polygon(cell))
        length = float(union_m.intersection(polygon_m).length)
        if length > 1e-6:
            lengths[cell] = length
    return lengths


def aggregate_h3(
    facilities: gpd.GeoDataFrame,
    total_lengths: dict[str, float],
    accessible_lengths: dict[str, float],
    *,
    source_complete: bool,
    bc_total_lengths: dict[str, float] | None = None,
) -> pd.DataFrame:
    bc_total_lengths = bc_total_lengths or {}
    wa_total_lengths = total_lengths
    total_lengths = {
        cell: wa_total_lengths.get(cell, 0.0) + bc_total_lengths.get(cell, 0.0)
        for cell in set(wa_total_lengths) | set(bc_total_lengths)
    }
    access_states = _column(facilities, "PUBLIC_ACCESS_STATE").fillna("unknown")
    evidence_tiers = _column(facilities, "ACCESS_EVIDENCE_TIER").fillna("legacy_authoritative")
    source_datasets = _column(facilities, "SOURCE_DATASET")
    site_counts = facilities.groupby("H3_INDEX", observed=True).size().to_dict()
    public_counts = (
        facilities.assign(_public=access_states.eq("public"))
        .groupby("H3_INDEX", observed=True)["_public"]
        .sum()
        .to_dict()
    )
    authoritative_public_counts = (
        facilities.assign(
            _verified=(
                access_states.eq("public")
                & evidence_tiers.isin({"authoritative_verified", "legacy_authoritative"})
            )
        )
        .groupby("H3_INDEX", observed=True)["_verified"]
        .sum()
        .to_dict()
    )
    osm_public_counts = (
        facilities.assign(_osm_public=evidence_tiers.eq("osm_explicit_public"))
        .groupby("H3_INDEX", observed=True)["_osm_public"]
        .sum()
        .to_dict()
    )
    osm_candidate_counts = (
        facilities.assign(_osm=source_datasets.eq("osm_shore_access"))
        .groupby("H3_INDEX", observed=True)["_osm"]
        .sum()
        .to_dict()
    )
    mapped_unknown_counts = (
        facilities.assign(_unknown=access_states.eq("unknown"))
        .groupby("H3_INDEX", observed=True)["_unknown"]
        .sum()
        .to_dict()
    )
    restricted_counts = (
        facilities.assign(_restricted=access_states.eq("restricted"))
        .groupby("H3_INDEX", observed=True)["_restricted"]
        .sum()
        .to_dict()
    )
    conditional_counts = (
        facilities.assign(_conditional=access_states.eq("conditional"))
        .groupby("H3_INDEX", observed=True)["_conditional"]
        .sum()
        .to_dict()
    )
    cells = sorted(set(total_lengths) | set(accessible_lengths) | set(site_counts))
    rows = []
    for cell in cells:
        total = total_lengths.get(cell)
        accessible = accessible_lengths.get(cell)
        has_public_evidence = accessible is not None or public_counts.get(cell, 0) > 0
        raw_fraction = (
            accessible / total if accessible is not None and total and total > 0 else None
        )
        denominator_mismatch = raw_fraction is not None and raw_fraction > 1.0 + 1e-9
        has_sites = cell in site_counts
        if authoritative_public_counts.get(cell, 0) > 0:
            evidence_state = "verified_public"
        elif osm_public_counts.get(cell, 0) > 0:
            evidence_state = "mapped_public"
        elif conditional_counts.get(cell, 0) > 0:
            evidence_state = "conditional"
        elif mapped_unknown_counts.get(cell, 0) > 0:
            evidence_state = "mapped_access_unknown"
        elif restricted_counts.get(cell, 0) > 0:
            evidence_state = "restricted"
        else:
            evidence_state = "unknown"
        in_wa = cell in wa_total_lengths or cell in accessible_lengths
        in_bc = cell in bc_total_lengths
        rows.append(
            {
                "H3_INDEX": cell,
                "H3_RESOLUTION": 7,
                "JURISDICTION": (
                    "cross_border"
                    if in_wa and in_bc
                    else "WA" if in_wa else "BC" if in_bc else "cross_border"
                ),
                "TOTAL_MARINE_SHORELINE_M": total,
                "PUBLIC_ACCESSIBLE_SHORELINE_M": accessible,
                "ACCESSIBLE_WATERFRONT_FRACTION": (
                    min(1.0, raw_fraction)
                    if raw_fraction is not None and not denominator_mismatch
                    else None
                ),
                "ACCESSIBLE_WATERFRONT_RAW_RATIO_QC": raw_fraction,
                "PUBLIC_ACCESS_STATE": "observed_access" if has_public_evidence else "unknown",
                "PUBLIC_ACCESS_EVIDENCE_STATE": evidence_state,
                "PUBLIC_ACCESS_SITE_COUNT": site_counts.get(cell),
                "VERIFIED_PUBLIC_ACCESS_SITE_COUNT": (
                    authoritative_public_counts.get(cell, 0) if has_sites else None
                ),
                "OSM_EXPLICIT_PUBLIC_ACCESS_SITE_COUNT": (
                    osm_public_counts.get(cell, 0) if has_sites else None
                ),
                "OSM_SHORE_CANDIDATE_COUNT": (
                    osm_candidate_counts.get(cell, 0) if has_sites else None
                ),
                "MAPPED_ACCESS_UNKNOWN_SITE_COUNT": (
                    mapped_unknown_counts.get(cell, 0) if has_sites else None
                ),
                "RESTRICTED_ACCESS_SITE_COUNT": (
                    restricted_counts.get(cell, 0) if has_sites else None
                ),
                "CONDITIONAL_ACCESS_SITE_COUNT": (
                    conditional_counts.get(cell, 0) if has_sites else None
                ),
                "SOURCE_COVERAGE_COMPLETE": bool(source_complete),
                "FRACTION_DENOMINATOR_MISMATCH_QC": denominator_mismatch,
                "MEASUREMENT_STATUS": MeasurementStatus.DERIVED.value,
            }
        )
    return pd.DataFrame(rows)


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    cfg = load_public_shore_config(config_path)
    raw_manifest = load_manifest(cfg.raw_manifest_path)
    if cfg.source_completeness != "complete" and not allow_partial:
        raise ValueError(
            "Public-shore sources are partial; rerun with allow_partial=True for research use."
        )
    raw_frames = {
        str(item["dataset_id"]).rsplit(".", 1)[-1]: gpd.read_parquet(item["path"])
        for item in raw_manifest["artifacts"]
        if str(item["path"]).endswith((".parquet", ".geoparquet"))
    }
    shoreline_frames = [raw_frames["wa_marine_shoreline"].to_crs("EPSG:4326")]
    if "bc_shorezone_lines" in raw_frames and not raw_frames["bc_shorezone_lines"].empty:
        shoreline_frames.append(raw_frames["bc_shorezone_lines"].to_crs("EPSG:4326"))
    reference_shoreline_lines = gpd.GeoDataFrame(
        pd.concat(shoreline_frames, ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326",
    )
    facilities = normalize_facilities(
        raw_frames["wa_public_access_points"],
        raw_frames["osm_shore_access"],
        h3_resolution=cfg.h3_resolution,
        bc_recreation_sites=raw_frames.get("bc_recreation_sites"),
        bc_shorezone_lines=raw_frames.get("bc_shorezone_lines"),
        reference_shoreline_lines=reference_shoreline_lines,
        length_crs=cfg.length_crs,
        bc_shore_connection_distance_m=cfg.bc_shore_connection_distance_m,
        osm_shore_connection_distance_m=cfg.osm_shore_connection_distance_m,
    )
    total_lengths = line_lengths_by_h3(
        raw_frames["wa_marine_shoreline"],
        h3_resolution=cfg.h3_resolution,
        length_crs=cfg.length_crs,
        sample_spacing_m=cfg.sample_spacing_m,
    )
    if not total_lengths:
        raise ValueError("The WA marine shoreline source produced no measurable H3 line length.")
    accessible_lengths = line_lengths_by_h3(
        raw_frames["wa_public_access_lines"],
        h3_resolution=cfg.h3_resolution,
        length_crs=cfg.length_crs,
        sample_spacing_m=cfg.sample_spacing_m,
    )
    bc_total_lengths = line_lengths_by_h3(
        raw_frames.get(
            "bc_shorezone_lines",
            gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326"),
        ),
        h3_resolution=cfg.h3_resolution,
        length_crs=cfg.length_crs,
        sample_spacing_m=cfg.sample_spacing_m,
    )
    h3_output = aggregate_h3(
        facilities,
        total_lengths,
        accessible_lengths,
        source_complete=cfg.source_completeness == "complete",
        bc_total_lengths=bc_total_lengths,
    )
    atomic_write_parquet(facilities, cfg.facilities_path, overwrite=overwrite)
    atomic_write_parquet(h3_output, cfg.h3_path, overwrite=overwrite)
    artifacts = [
        artifact_record(
            cfg.facilities_path,
            dataset_id="human.accessibility.public_shore_access.facilities_r7",
            frame=facilities,
            h3_resolution=7,
        ),
        artifact_record(
            cfg.h3_path,
            dataset_id="human.accessibility.public_shore_access.h3_r7",
            frame=h3_output,
            h3_resolution=7,
        ),
    ]
    payload = manifest_payload(
        config=cfg.human,
        stage="build",
        artifacts=artifacts,
        sources=raw_manifest["sources"],
        inputs=raw_manifest["artifacts"],
        source_completeness=cfg.source_completeness,
        measurement_statuses=[
            MeasurementStatus.OBSERVED,
            MeasurementStatus.DERIVED,
            MeasurementStatus.UNAVAILABLE,
        ],
        attribution=raw_manifest["attribution"],
        licenses=raw_manifest["licenses"],
        h3_resolution=7,
        spatial_bounds=raw_manifest["spatial_bounds_wgs84"],
        limitations=[
            "Accessible-waterfront fraction uses only WA authoritative access and "
            "linework; BC ShoreZone supplies a denominator but no accessible-line numerator.",
            "Cells without mapped access retain null accessible length/fraction under "
            "partial coverage.",
            "BC recreation facilities within the ShoreZone proximity threshold are "
            "authoritative facility candidates, not verified public shore access. "
            "A missing closure does not establish legal access or reachable shoreline.",
            "OSM candidates require explicit pedestrian access tags to be mapped-public "
            "evidence and never substitute for authoritative coverage.",
            "Agency and OSM facility rows remain separate source records and are not "
            "deduplicated into physical access sites.",
        ],
    )
    write_manifest(cfg.manifest_path, payload)
    return cfg.h3_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(build(args.config, allow_partial=args.allow_partial, overwrite=args.overwrite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
