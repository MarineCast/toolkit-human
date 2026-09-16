"""Frozen San Juan Islands research pilot using existing regional source products."""

from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import polars as pl
import rasterio
import yaml
from rasterio.windows import from_bounds
from shapely import union_all
from shapely.geometry import box

from human.core.artifacts import checksum_path
from human.core.geo.h3 import cell_to_polygon
from human.utils.artifacts import artifact_record, sha256_file
from human.viewshed.config import load_app_config
from human.viewshed.finalize.final_artifacts import _write_static_artifact_metadata

from .access_kernel import build_access_kernel
from .build import build
from .config import DEFAULT_CONFIG_PATH, load_land_reporting_config
from .download import download
from .sites import build_observation_sites, sample_observation_sites


def connect_supported_lines(facilities, lines):
    """Exact provider destination IDs, government-public class, local-cell clip.

    These are represented public shoreline segments, not certified observer
    locations or permission inferred from proximity. Preserve original points.
    """
    result = facilities.copy()
    result["REPRESENTED_GEOMETRY_WKB"] = result.geometry.to_wkb()
    result["SHORELINE_CONNECTION_EVIDENCE"] = "facility_point_only"
    for index, row in result.iterrows():
        if row.SOURCE_DATASET != "wa_public_access_points" or row.PUBLIC_ACCESS_STATE != "public":
            continue
        matched = lines.loc[lines.ECYBEACHID.eq(row.SOURCE_GROUP_ID) & lines.CLASS.eq("PUB1")]
        if matched.empty:
            continue
        geometry = union_all(matched.geometry).intersection(cell_to_polygon(row.H3_INDEX))
        if geometry.is_empty or geometry.geom_type not in {"LineString", "MultiLineString"}:
            continue
        result.at[index, "REPRESENTED_GEOMETRY_WKB"] = geometry.wkb
        result.at[index, "SHORELINE_CONNECTION_EVIDENCE"] = (
            "exact_ECYBEACHID;CLASS=PUB1;clip_to_declared_source_cell;OBJECTID="
            + ",".join(matched.OBJECTID.astype(str))
        )
    return result


def run_pilot(output_dir: Path):
    started = time.perf_counter()
    directory = output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    original = load_land_reporting_config(resolve_generation=False)
    base = load_app_config(original.viewshed_config_path)
    inventory = []

    def record(path):
        path = Path(path)
        inventory.append({"path": str(path.resolve()), "sha256": sha256_file(path)})

    source_manifest = json.loads(original.public_shore_manifest_path.read_text())
    facility_path = Path(
        next(
            a["path"]
            for a in source_manifest["artifacts"]
            if a["dataset_id"].endswith(".facilities_r7")
        )
    )
    facilities = gpd.read_parquet(facility_path).cx[-123.2:-122.9, 48.45:48.62].copy()
    facilities["AUDIT_STRATUM"] = (
        facilities.SOURCE_DATASET
        + ":"
        + facilities.PUBLIC_ACCESS_STATE
        + ":"
        + pd.cut(
            facilities.SHORE_DISTANCE_M,
            [-np.inf, 30, 150, np.inf],
            labels=["near", "intermediate", "far"],
        ).astype(str)
    )
    # Round-robin deterministic stratification; not a random population sample.
    groups = [
        group.sort_values(["H3_INDEX", "SOURCE_RECORD_ID"])
        for _, group in facilities.groupby("AUDIT_STRATUM")
    ]
    order = [
        group.iloc[[i]] for i in range(max(map(len, groups))) for group in groups if i < len(group)
    ]
    facilities = pd.concat(order[:25]).reset_index(drop=True)
    if len(facilities) != 25:
        raise ValueError("Pilot requires 25 represented sites")
    line_path = Path(
        "data/raw/human/accessibility/public_shore_access/wa_public_access_lines.parquet"
    ).resolve()
    lines = gpd.read_parquet(line_path).to_crs(4326)
    facilities = connect_supported_lines(facilities, lines)
    record(facility_path)
    record(original.public_shore_manifest_path)
    record(line_path)
    facilities.to_parquet(directory / "facilities.parquet")
    audit = facilities.drop(columns=["geometry", "REPRESENTED_GEOMETRY_WKB"]).copy()
    audit["FACILITY_LONGITUDE"] = facilities.geometry.x
    audit["FACILITY_LATITUDE"] = facilities.geometry.y
    audit["PLACEMENT_FINDING"] = np.where(
        facilities.SHORELINE_CONNECTION_EVIDENCE.eq("facility_point_only"),
        "Represented facility point; actual viewing position unverified",
        "Source-supported local public shoreline substituted for facility point; actual visitation unverified",
    )
    audit.to_csv(directory / "placement_audit.csv", index=False)
    cells = sorted(facilities.loc[facilities.PUBLIC_ACCESS_STATE.eq("public"), "H3_INDEX"].unique())
    bbox = (-123.27, 48.39, -122.83, 48.68)
    clip = box(*bbox)
    for label, path in (
        ("dem", base.paths.regional_dem_path),
        ("canopy", base.paths.canopy_height_path),
    ):
        record(path)
        with rasterio.open(path) as src:
            if src.crs.to_epsg() != 4326:
                raise ValueError("Pilot crop requires geographic regional rasters")
            window = from_bounds(*bbox, transform=src.transform).round_offsets().round_lengths()
            values = src.read(1, window=window)
            profile = {
                **src.profile,
                "height": values.shape[0],
                "width": values.shape[1],
                "transform": src.window_transform(window),
            }
            with rasterio.open(directory / f"{label}.tif", "w", **profile) as dst:
                dst.write(values, 1)
    water = gpd.read_parquet(base.paths.water_polygon_path).to_crs(4326)
    water = water.loc[water.intersects(clip)].copy()
    water.geometry = water.geometry.intersection(clip)
    water.to_parquet(directory / "water.parquet")
    record(base.paths.water_polygon_path)
    land = gpd.read_file(base.paths.land_polygon_path, bbox=bbox).to_crs(4326)
    land.geometry = land.geometry.intersection(clip)
    land.to_file(directory / "land.geojson", driver="GeoJSON")
    for p in base.paths.land_polygon_path.parent.glob(base.paths.land_polygon_path.stem + ".*"):
        record(p)
    sources = gpd.GeoDataFrame(
        {"h3_cell": cells}, geometry=[cell_to_polygon(c) for c in cells], crs=4326
    )
    projected = sources.to_crs(32610)
    land_union = union_all(land.to_crs(32610).geometry)
    sources["land_fraction"] = [
        g.intersection(land_union).area / g.area for g in projected.geometry
    ]
    sources.to_parquet(directory / "sources.parquet")
    raw = copy.deepcopy(base.raw_config)
    raw["paths"].update(
        {
            k: str(directory / v)
            for k, v in {
                "regional_dem_path": "dem.tif",
                "projected_dem_path": "projected_dem.tif",
                "canopy_height_path": "canopy.tif",
                "water_polygon_path": "water.parquet",
                "land_polygon_path": "land.geojson",
                "land_h3_path": "sources.parquet",
                "source_cells_path": "sources.parquet",
                "output_dir": "viewshed_work",
                "final_output_dir": "viewshed_final",
                "raw_dem_dir": "raw_dem",
                "map_dir": "maps",
            }.items()
        }
    )
    raw["viewshed"].update(
        {
            "surface_model": "canopy",
            "max_distance_m": 3000,
            "aoi_margin_m": 1000,
            "dem_resolution_m": 30,
        }
    )
    raw["run"].update({"overwrite": True, "keep_intermediate_rasters": False})
    (directory / "viewshed.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    app = load_app_config(directory / "viewshed.yaml")
    sites = build_observation_sites(facilities)
    samples = sample_observation_sites(sites, samples_per_site=5)
    kernel, support = build_access_kernel(app, samples, distance_bin_km=0.25)
    resumed_kernel, resumed_support = build_access_kernel(app, samples, distance_bin_km=0.25)
    pd.testing.assert_frame_equal(kernel, resumed_kernel)
    pd.testing.assert_frame_equal(support, resumed_support)
    static = (
        kernel.loc[kernel.SCENARIO.eq("MAPPED")]
        .groupby(["source_h3", "target_h3"], as_index=False)
        .canopy_kernel.sum()
        .rename(columns={"canopy_kernel": "weight_static_viewability"})
    )
    static["weight_terrain"] = static.weight_static_viewability
    static["weight_distance"] = 1.0
    static["weight_vegetation"] = 1.0
    static_path = directory / "pilot_static.parquet"
    static[
        [
            "source_h3",
            "target_h3",
            "weight_terrain",
            "weight_distance",
            "weight_vegetation",
            "weight_static_viewability",
        ]
    ].to_parquet(static_path, index=False)
    _write_static_artifact_metadata(
        static_path,
        raw=app.raw_config,
        source_type="land",
        input_paths={"dem": directory / "dem.tif", "canopy": directory / "canopy.tif"},
        input_checksums={
            "dem": checksum_path(directory / "dem.tif"),
            "canopy": checksum_path(directory / "canopy.tif"),
        },
        coverage={
            "bounded_real_source_pilot": True,
            "reference": "represented mapped sites; not an all-land comparison",
        },
        row_count=len(static),
    )
    config = yaml.safe_load(Path(DEFAULT_CONFIG_PATH).read_text())
    pipeline = config["pipeline"]
    pipeline["static_weights_path"] = str(static_path)
    pipeline["viewshed_config_path"] = str(directory / "viewshed.yaml")
    start, end = pd.Timestamp("2024-06-03"), pd.Timestamp("2024-06-09")
    for name in ("transport", "population_travel", "public_shore", "calendar"):
        source = Path(getattr(original, f"{name}_path"))
        record(source)
        data = pl.read_parquet(source)
        data = (
            data.filter(pl.col("date").cast(pl.Date).is_between(start.date(), end.date()))
            if name == "calendar"
            else data.filter(pl.col("H3_INDEX").is_in(cells))
        )
        path = directory / f"{name}.parquet"
        data.write_parquet(path)
        pipeline[f"{name}_path"] = str(path)
        mp = Path(getattr(original, f"{name}_manifest_path"))
        record(mp)
        m = json.loads(mp.read_text())
        m["artifacts"] = [artifact_record(path, dataset_id=f"pilot.{name}")]
        if name == "public_shore":
            m["artifacts"].append(
                artifact_record(
                    directory / "facilities.parquet",
                    dataset_id="human.accessibility.public_shore_access.facilities_r7",
                )
            )
            m["artifacts"].append(
                artifact_record(line_path, dataset_id="pilot.authoritative_shoreline_source")
            )
        m["inputs"] = [artifact_record(source, dataset_id=f"pilot.original.{name}")]
        m.setdefault("limitations", []).append(
            "Frozen bounded San Juan research subset; upstream interpretation retained"
        )
        manifest_path = directory / f"{name}_manifest.json"
        manifest_path.write_text(json.dumps(m, indent=2))
        pipeline[f"{name}_manifest_path"] = str(manifest_path)
    for name, resolution in (("surface_weather", 5), ("daylight", 4)):
        source = getattr(original, f"{name}_path")
        mp = getattr(original, f"{name}_manifest_path")
        record(mp)
        parents = sorted({h3.cell_to_parent(c, resolution) for c in cells})
        paths = sorted(source.rglob("*.parquet"))
        # Record the exact enumerated files; freeze only the consumed bounded rows.
        for p in paths:
            record(p)
        data = (
            pl.scan_parquet([str(p) for p in paths])
            .filter(
                pl.col("H3_INDEX").is_in(parents)
                & pl.col("DATE").cast(pl.Date).is_between(start.date(), end.date())
            )
            .collect(engine="streaming")
        )
        target = directory / name
        target.mkdir(exist_ok=True)
        data.write_parquet(target / "part.parquet")
        m = json.loads(mp.read_text())
        m["temporal_coverage"] = {"start_date": str(start.date()), "end_date": str(end.date())}
        m["pilot_source_manifest_sha256"] = sha256_file(mp)
        manifest_path = directory / f"{name}_manifest.json"
        manifest_path.write_text(json.dumps(m, indent=2))
        pipeline[f"{name}_path"] = str(target)
        pipeline[f"{name}_manifest_path"] = str(manifest_path)
    for section in ("raw", "output", "inspection"):
        for key in config[section]:
            suffix = (
                ".html"
                if section == "inspection"
                else (
                    ".json"
                    if any(s in key for s in ("manifest", "metadata", "inventory"))
                    else ".parquet"
                )
            )
            config[section][key] = str(directory / section / (key + suffix))
    config["parameters"]["samples_per_site"] = 5
    config["parameters"]["start_date"] = str(start.date())
    config["parameters"]["end_date"] = str(end.date())
    path = directory / "land.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    (directory / "frozen_regional_sources.json").write_text(json.dumps(inventory, indent=2))
    for item in inventory:
        if sha256_file(item["path"]) != item["sha256"]:
            raise ValueError(f"Regional source changed during pilot freeze: {item['path']}")
    download(path, overwrite=True)
    build(path, allow_partial=True, overwrite=True)
    result = {
        "elapsed_seconds": time.perf_counter() - started,
        "audit_sites": 25,
        "eligible_sites": int(sites.MAPPED_ELIGIBLE.sum()),
        "samples": len(samples),
        "connected_line_records": int(
            facilities.SHORELINE_CONNECTION_EVIDENCE.ne("facility_point_only").sum()
        ),
        "kernel_profile": support.attrs["evaluation_plan"]["profile"],
        "resume_profile": resumed_support.attrs["evaluation_plan"]["profile"],
        "fresh_resume_numerical_equality": True,
        "bbox_wgs84": bbox,
        "dates": [str(start.date()), str(end.date())],
        "source_config": str(path),
        "limitations": [
            "Stratified engineering audit, not a population placement-error estimate",
            "Facility markers without matched public shoreline remain unverified viewing positions",
            "Contemporary static access context applied to a declared historical week",
            "No whale truth or model-selection changes",
        ],
    }
    (directory / "pilot_profile.json").write_text(json.dumps(result, indent=2))
    code_files = sorted(Path(__file__).resolve().parents[2].rglob("*.py"))
    evidence_files = [
        path,
        directory / "viewshed.yaml",
        directory / "placement_audit.csv",
        directory / "pilot_profile.json",
        directory / "frozen_regional_sources.json",
    ]
    selected = load_land_reporting_config(path)
    generation_manifest = selected.source_output_path.parent / "manifest.json"
    evidence_files.append(generation_manifest)
    evidence = {
        "status": "bounded_research_release",
        "code_identity": [{"path": str(p), "sha256": sha256_file(p)} for p in code_files],
        "artifacts": [{"path": str(p), "sha256": sha256_file(p)} for p in evidence_files],
        "generation_manifest": str(generation_manifest),
        "model_eligible": False,
    }
    (directory / "release_evidence.json").write_text(json.dumps(evidence, indent=2))
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    print(run_pilot(parser.parse_args().output_dir))
