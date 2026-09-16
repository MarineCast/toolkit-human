"""Build normalized boat-launch facilities and occupied H3 R7 evidence."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

import geopandas as gpd
import pandas as pd

from human.core.geo.h3 import latlng_to_cell
from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    atomic_write_parquet,
    load_manifest,
    manifest_payload,
    write_manifest,
)

from .config import DEFAULT_CONFIG_PATH, load_boat_launch_config

TRUE_VALUES = {
    "1",
    "true",
    "t",
    "yes",
    "y",
    "present",
    "available",
    "public",
    "both",
    "motorized",
    "non-motorized",
}
FALSE_VALUES = {"0", "false", "f", "no", "n", "absent", "none"}


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


def _boolean_state(value: Any) -> bool | None:
    text = (_clean(value) or "").casefold()
    if text in FALSE_VALUES or text.startswith(("no ", "not ")):
        return False
    if text in TRUE_VALUES or any(token in text for token in ("ramp", "slipway", "launch")):
        return True
    return None


def _ramp_state(*values: Any) -> str:
    cleaned = [(_clean(value) or "").casefold() for value in values]
    launch_type = cleaned[0] if cleaned else ""
    if launch_type == "both":
        return "motorized_and_non_motorized"
    if launch_type == "non-motorized":
        return "non_motorized"
    if launch_type == "motorized":
        return "motorized"
    text = " ".join(value for value in cleaned if value)
    if any(token in text for token in ("hand launch", "carry", "cartop", "car-top")):
        return "carry_in"
    if any(token in text for token in ("concrete ramp", "boat ramp", "ramp", "slipway")):
        return "ramp"
    if _boolean_state(launch_type) is True:
        return "present_type_unknown"
    return "unknown"


def _point_geometry(frame: gpd.GeoDataFrame) -> gpd.GeoSeries:
    return frame.geometry.map(
        lambda geometry: (
            geometry
            if geometry is None or geometry.is_empty or geometry.geom_type == "Point"
            else geometry.representative_point()
        )
    )


def _normalize_wa(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    launch = _column(frame, "Boat_Launch", "BOAT_LAUNCH")
    selected = frame.loc[launch.map(_boolean_state).eq(True)].copy()
    launch = launch.loc[selected.index]
    primary = _column(selected, "Primary_Acccess_Type", "Primary_Access_Type")
    secondary = _column(selected, "Secondary_Access_Type")
    record_id = _column(selected, "OBJECTID", "ECYBEACHID").astype(str)
    output = gpd.GeoDataFrame(
        {
            "ACCESS_SITE_ID": "wa_ecology:" + record_id,
            "JURISDICTION": "WA",
            "COUNTRY_CODE": "US",
            "SOURCE_DATASET": "wa_public_access_points",
            "SOURCE_RECORD_ID": record_id,
            "SOURCE_GROUP_ID": _column(selected, "ECYBEACHID").map(_clean),
            "NAME": _column(selected, "Beach_Name", "BEACH_NAME").map(_clean),
            "PUBLIC_ACCESS_STATE": "public",
            "BOAT_LAUNCH_PRESENT": True,
            "RAMP_CAPABILITY_STATE": [
                _ramp_state(a, b, c) for a, b, c in zip(launch, primary, secondary, strict=False)
            ],
            "SEASONAL_OPERATION_STATE": _column(
                selected, "Seasonal_Operation", "Open_Season", "Season"
            ).map(lambda value: _clean(value) or "unknown"),
            "SOURCE_COVERAGE_STATUS": "partial",
            "DATA_VINTAGE": _column(selected, "UpdatedDate", "UPDATED_DATE").map(_clean),
            "MEASUREMENT_STATUS": MeasurementStatus.OBSERVED.value,
        },
        geometry=_point_geometry(selected),
        crs=frame.crs or "EPSG:4326",
    )
    return output.to_crs("EPSG:4326")


def _normalize_bc(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    record_id = _column(frame, "BOAT_LAUNCH_ID", "OBJECTID").astype(str)
    return gpd.GeoDataFrame(
        {
            "ACCESS_SITE_ID": "bc_databc:" + record_id,
            "JURISDICTION": "BC",
            "COUNTRY_CODE": "CA",
            "SOURCE_DATASET": "bc_coastal_boat_launches",
            "SOURCE_RECORD_ID": record_id,
            "SOURCE_GROUP_ID": None,
            "NAME": _column(frame, "LOCATION", "NAME").map(_clean),
            "PUBLIC_ACCESS_STATE": "unknown",
            "BOAT_LAUNCH_PRESENT": True,
            "RAMP_CAPABILITY_STATE": "unknown",
            "SEASONAL_OPERATION_STATE": "unknown",
            "SOURCE_COVERAGE_STATUS": "legacy_partial",
            "DATA_VINTAGE": "circa_2004",
            "MEASUREMENT_STATUS": MeasurementStatus.OBSERVED.value,
        },
        geometry=_point_geometry(frame),
        crs=frame.crs or "EPSG:4326",
    ).to_crs("EPSG:4326")


def _normalize_osm(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    record_id = _column(frame, "OSM_TYPE").astype(str) + "/" + _column(frame, "OSM_ID").astype(str)
    access = _column(frame, "access").map(lambda value: (_clean(value) or "").casefold())
    public_state = access.map(
        lambda value: (
            "public" if value in {"yes", "public", "permissive", "designated"} else "unknown"
        )
    )
    seasonal = _column(frame, "seasonal", "opening_hours").map(
        lambda value: _clean(value) or "unknown"
    )
    return gpd.GeoDataFrame(
        {
            "ACCESS_SITE_ID": "osm:" + record_id,
            "JURISDICTION": "cross_border",
            "COUNTRY_CODE": None,
            "SOURCE_DATASET": "osm_slipways",
            "SOURCE_RECORD_ID": record_id,
            "SOURCE_GROUP_ID": None,
            "NAME": _column(frame, "name").map(_clean),
            "PUBLIC_ACCESS_STATE": public_state,
            "BOAT_LAUNCH_PRESENT": True,
            "RAMP_CAPABILITY_STATE": "slipway",
            "SEASONAL_OPERATION_STATE": seasonal,
            "SOURCE_COVERAGE_STATUS": "community_mapped_partial",
            "DATA_VINTAGE": None,
            "MEASUREMENT_STATUS": MeasurementStatus.OBSERVED.value,
        },
        geometry=_point_geometry(frame),
        crs=frame.crs or "EPSG:4326",
    ).to_crs("EPSG:4326")


def normalize_facilities(
    raw_frames: dict[str, gpd.GeoDataFrame], h3_resolution: int
) -> gpd.GeoDataFrame:
    facilities = pd.concat(
        [
            _normalize_wa(raw_frames["wa_public_access_points"]),
            _normalize_bc(raw_frames["bc_coastal_boat_launches"]),
            _normalize_osm(raw_frames["osm_slipways"]),
        ],
        ignore_index=True,
    )
    facilities = gpd.GeoDataFrame(facilities, geometry="geometry", crs="EPSG:4326")
    facilities = facilities.loc[facilities.geometry.notna() & ~facilities.geometry.is_empty].copy()
    if facilities.empty:
        raise ValueError("Boat-launch sources produced no usable point geometries.")
    facilities["H3_INDEX"] = facilities.geometry.map(
        lambda point: latlng_to_cell(point.y, point.x, h3_resolution)
    )
    facilities["H3_RESOLUTION"] = h3_resolution
    facilities = facilities.sort_values(["SOURCE_DATASET", "SOURCE_RECORD_ID"]).reset_index(
        drop=True
    )
    if facilities["ACCESS_SITE_ID"].duplicated().any():
        raise ValueError("Boat-launch ACCESS_SITE_ID must be unique within each source.")
    return facilities


def aggregate_h3(facilities: gpd.GeoDataFrame, *, source_complete: bool) -> pd.DataFrame:
    work = facilities.copy()
    work["VERIFIED_PUBLIC"] = work["PUBLIC_ACCESS_STATE"].eq("public")
    work["KNOWN_RAMP"] = ~work["RAMP_CAPABILITY_STATE"].eq("unknown")
    work["KNOWN_SEASON"] = ~work["SEASONAL_OPERATION_STATE"].eq("unknown")
    work["WA_RECORD"] = work["JURISDICTION"].eq("WA")
    work["BC_RECORD"] = work["JURISDICTION"].eq("BC")
    grouped = work.groupby("H3_INDEX", sort=True, observed=True)
    output = grouped.agg(
        BOAT_LAUNCH_COUNT=("ACCESS_SITE_ID", "size"),
        VERIFIED_PUBLIC_BOAT_LAUNCH_COUNT=("VERIFIED_PUBLIC", "sum"),
        BOAT_LAUNCHES_WITH_KNOWN_RAMP_CAPABILITY=("KNOWN_RAMP", "sum"),
        BOAT_LAUNCHES_WITH_KNOWN_SEASONAL_OPERATION=("KNOWN_SEASON", "sum"),
        WA_SOURCE_RECORD_COUNT=("WA_RECORD", "sum"),
        BC_SOURCE_RECORD_COUNT=("BC_RECORD", "sum"),
        SOURCE_DATASET_COUNT=("SOURCE_DATASET", "nunique"),
    ).reset_index()
    output.insert(1, "H3_RESOLUTION", 7)
    output["SOURCE_COVERAGE_COMPLETE"] = bool(source_complete)
    output["MEASUREMENT_STATUS"] = MeasurementStatus.DERIVED.value
    return output


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    allow_partial: bool = False,
    *,
    overwrite: bool = False,
) -> Path:
    cfg = load_boat_launch_config(config_path)
    raw_manifest = load_manifest(cfg.raw_manifest_path)
    if cfg.source_completeness != "complete" and not allow_partial:
        raise ValueError(
            "Boat-launch sources are partial; rerun with allow_partial=True for research use."
        )
    raw_frames = {
        str(item["dataset_id"]).rsplit(".", 1)[-1]: gpd.read_parquet(item["path"])
        for item in raw_manifest["artifacts"]
        if str(item["path"]).endswith((".parquet", ".geoparquet"))
    }
    facilities = normalize_facilities(raw_frames, cfg.h3_resolution)
    h3_output = aggregate_h3(facilities, source_complete=cfg.source_completeness == "complete")
    atomic_write_parquet(facilities, cfg.facilities_path, overwrite=overwrite)
    atomic_write_parquet(h3_output, cfg.h3_path, overwrite=overwrite)
    artifacts = [
        artifact_record(
            cfg.facilities_path,
            dataset_id="human.accessibility.boat_launch_access.facilities_r7",
            frame=facilities,
            h3_resolution=7,
        ),
        artifact_record(
            cfg.h3_path,
            dataset_id="human.accessibility.boat_launch_access.h3_r7",
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
            *(
                [MeasurementStatus.UNAVAILABLE]
                if any(
                    source.get("measurement_status") == "unavailable"
                    for source in raw_manifest["sources"]
                )
                else []
            ),
        ],
        attribution=raw_manifest["attribution"],
        licenses=raw_manifest["licenses"],
        h3_resolution=7,
        spatial_bounds=raw_manifest["spatial_bounds_wgs84"],
        limitations=[
            "Only occupied launch cells are emitted; absence from the table is not a zero.",
            "BC public status, ramp capability, and seasonal operation remain unknown.",
            "Agency and OSM source records are not yet deduplicated into physical "
            "launches; provenance remains separate.",
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
