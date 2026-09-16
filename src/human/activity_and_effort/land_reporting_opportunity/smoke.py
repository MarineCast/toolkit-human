"""Offline coastal fixture through production preparation, build, and publication."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from human.core.config.paths import project_root

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import rasterio
import yaml
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from human.core.artifacts import checksum_path
from human.core.geo.h3 import cell_to_polygon
from human.utils.artifacts import (
    MeasurementStatus,
    artifact_record,
    manifest_payload,
    sha256_file,
    write_manifest,
)
from human.viewshed.config import load_app_config
from human.viewshed.finalize.final_artifacts import _write_static_artifact_metadata

from .access_kernel import build_access_kernel
from .build import build
from .config import DEFAULT_CONFIG_PATH, load_land_reporting_config
from .download import download
from .inspect import inspect
from .sites import build_observation_sites, sample_observation_sites


def run_smoke(output_dir: Path) -> Path:
    directory = output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    project = project_root()
    base = load_app_config(project / "config/modeling/effort/viewshed.yaml")
    raw = dict(base.raw_config)
    paths = dict(raw["paths"])
    paths.update(
        {
            "regional_dem_path": str(directory / "dem.tif"),
            "projected_dem_path": str(directory / "dem_projected.tif"),
            "water_polygon_path": str(directory / "water.parquet"),
            "land_polygon_path": str(directory / "land.geojson"),
            "canopy_height_path": str(directory / "canopy.tif"),
            "land_h3_path": str(directory / "sources.parquet"),
            "source_cells_path": str(directory / "sources.parquet"),
            "output_dir": str(directory / "viewshed_work"),
            "final_output_dir": str(directory / "viewshed_final"),
            "raw_dem_dir": str(directory / "raw_dem"),
            "map_dir": str(directory / "maps"),
        }
    )
    raw["paths"] = paths
    raw["run"] = {**raw.get("run", {}), "overwrite": True, "keep_intermediate_rasters": False}
    raw["viewshed"] = {
        **raw.get("viewshed", {}),
        "surface_model": "canopy",
        "crs_projected": "EPSG:32610",
        "dem_resolution_m": 60,
        "max_distance_m": 20000,
        "aoi_margin_m": 0,
        "canopy_nodata_policy": "error",
        "canopy_resampling": "nearest",
        "observer_canopy_clearance_radius_m": 0,
    }
    viewshed_config = directory / "viewshed.yaml"
    viewshed_config.write_text(yaml.safe_dump(raw, sort_keys=False))
    extent = box(498000, 5390000, 522000, 5402000)
    land = box(498000, 5390000, 522000, 5396000)
    water = extent.difference(land)
    gpd.GeoDataFrame(geometry=[land], crs=32610).to_file(
        directory / "land.geojson", driver="GeoJSON"
    )
    gpd.GeoDataFrame(geometry=[water], crs=32610).to_crs(4326).to_parquet(
        directory / "water.parquet"
    )
    transform = from_origin(498000, 5402000, 60, 60)
    dem = np.full((200, 400), 5, dtype="float32")
    canopy = np.zeros_like(dem)
    canopy[100:, 60] = 35
    for name, values in (("dem", dem), ("canopy", canopy)):
        with rasterio.open(
            directory / f"{name}.tif",
            "w",
            driver="GTiff",
            width=400,
            height=200,
            count=1,
            dtype="float32",
            crs=32610,
            transform=transform,
            nodata=-9999,
        ) as out:
            out.write(values, 1)
    locations = gpd.GeoSeries(
        [Point(500500, 5395900), Point(516500, 5395900), Point(500600, 5395700)], crs=32610
    ).to_crs(4326)
    cells = [h3.latlng_to_cell(p.y, p.x, 7) for p in locations]
    unique = sorted(set(cells))
    gpd.GeoDataFrame(
        {"h3_cell": unique, "land_fraction": [1.0] * len(unique)},
        geometry=[cell_to_polygon(c) for c in unique],
        crs=4326,
    ).to_parquet(directory / "sources.parquet")
    facilities = gpd.GeoDataFrame(
        {
            "SOURCE_DATASET": ["synthetic"] * 3,
            "SOURCE_RECORD_ID": ["beach", "pier", "headland"],
            "PUBLIC_ACCESS_STATE": ["public", "public", "restricted"],
            "ACCESS_EVIDENCE_TIER": ["authoritative_verified", "osm_explicit_public", "restricted"],
            "H3_INDEX": cells,
        },
        geometry=locations,
        crs=4326,
    )
    represented = list(facilities.geometry.to_wkb())
    represented[0] = (
        gpd.GeoSeries([LineString([(500450, 5395900), (500550, 5395900)])], crs=32610)
        .to_crs(4326)
        .iloc[0]
        .wkb
    )
    facilities["REPRESENTED_GEOMETRY_WKB"] = represented
    facilities.to_parquet(directory / "facilities.parquet")
    # General diagnostic deliberately includes the inaccessible headland.
    broad = facilities.copy()
    broad["PUBLIC_ACCESS_STATE"] = "public"
    broad_sites = build_observation_sites(broad)
    broad_samples = sample_observation_sites(broad_sites, samples_per_site=1)
    app = load_app_config(viewshed_config)
    general_kernel, support = build_access_kernel(app, broad_samples, distance_bin_km=0.25)
    static = (
        general_kernel.loc[general_kernel.SCENARIO == "MAPPED"]
        .groupby(["source_h3", "target_h3"], as_index=False)
        .canopy_kernel.sum()
        .rename(columns={"canopy_kernel": "weight_static_viewability"})
    )
    static["weight_terrain"] = static.weight_static_viewability
    static["weight_vegetation"] = 1.0
    static["weight_distance"] = 1.0  # diagnostic only; integrated distance is in the kernel
    static_path = directory / "general_static.parquet"
    static = static[
        [
            "source_h3",
            "target_h3",
            "weight_terrain",
            "weight_distance",
            "weight_vegetation",
            "weight_static_viewability",
        ]
    ]
    static.to_parquet(static_path, index=False)
    _write_static_artifact_metadata(
        static_path,
        raw=app.raw_config,
        source_type="land",
        input_paths={"dem": directory / "dem.tif", "canopy": directory / "canopy.tif"},
        input_checksums={
            "dem": checksum_path(directory / "dem.tif"),
            "canopy": checksum_path(directory / "canopy.tif"),
        },
        coverage={"fixture": True},
        row_count=len(static),
    )
    config = yaml.safe_load((project / DEFAULT_CONFIG_PATH).read_text())
    pipeline = config["pipeline"]
    pipeline["static_weights_path"] = str(static_path)
    pipeline["viewshed_config_path"] = str(viewshed_config)
    for key in list(pipeline):
        if key not in {"static_weights_path", "viewshed_config_path"}:
            pipeline[key] = str(
                directory
                / (
                    key
                    + (
                        ".json"
                        if "manifest" in key
                        else "" if key in {"surface_weather_path", "daylight_path"} else ".parquet"
                    )
                )
            )
    for section in ("raw", "output", "inspection"):
        for key in config[section]:
            suffix = (
                ".html"
                if section == "inspection"
                else (
                    ".json"
                    if "manifest" in key or "metadata" in key or "inventory" in key
                    else ".parquet"
                )
            )
            config[section][key] = str(directory / section / (key + suffix))
    config["parameters"]["samples_per_site"] = 5
    config["parameters"]["start_date"] = None
    config["parameters"]["end_date"] = None
    config_path = directory / "land.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    cfg = load_land_reporting_config(config_path, resolve_generation=False)
    transport = pd.DataFrame(
        {
            "H3_INDEX": unique,
            "ROAD_PROXIMITY_COMPONENT": 1.0,
            "CITY_TRAVEL_ACCESS_COMPONENT": 1.0,
            "LAND_TRANSPORT_ACCESS_OPPORTUNITY_INDEX": 1.0,
            "LAND_TRANSPORT_ACCESS_AVAILABLE": True,
        }
    )
    population = pd.DataFrame(
        {
            "H3_INDEX": unique,
            "POPULATION_TRAVEL_OPPORTUNITY_INDEX": 1.0,
            "POPULATION_TRAVEL_OPPORTUNITY_AVAILABLE": True,
        }
    )
    shore = pd.DataFrame(
        {
            "H3_INDEX": unique,
            "PUBLIC_ACCESS_EVIDENCE_STATE": "mapped_public",
            "VERIFIED_PUBLIC_ACCESS_SITE_COUNT": 1,
            "OSM_EXPLICIT_PUBLIC_ACCESS_SITE_COUNT": 1,
            "OSM_SHORE_CANDIDATE_COUNT": 0,
            "SOURCE_COVERAGE_COMPLETE": False,
        }
    )
    dates = pd.date_range("2026-01-05", periods=7)
    calendar = pd.DataFrame({"date": dates, "calendar_effort_weight": 1.0})
    for name, frame in (
        ("transport", transport),
        ("population_travel", population),
        ("public_shore", shore),
        ("calendar", calendar),
    ):
        path = Path(pipeline[f"{name}_path"])
        frame.to_parquet(path, index=False)
        artifacts = [artifact_record(path, dataset_id=f"fixture.{name}")]
        if name == "public_shore":
            artifacts.append(
                artifact_record(
                    directory / "facilities.parquet",
                    dataset_id="human.accessibility.public_shore_access.facilities_r7",
                )
            )
        manifest = manifest_payload(
            config=cfg.human,
            stage="build",
            artifacts=artifacts,
            inputs=[],
            source_completeness="partial",
            measurement_statuses=[MeasurementStatus.DERIVED],
            attribution=["Synthetic fixture"],
            licenses=["CC0"],
            h3_resolution=7,
            limitations=["Synthetic inputs; no empirical observation effort"],
        )
        write_manifest(pipeline[f"{name}_manifest_path"], manifest)
    weather_cells = sorted({h3.cell_to_parent(c, 5) for c in unique})
    weather = pd.DataFrame(
        {
            "H3_INDEX": weather_cells[0],
            "DATE": dates,
            "VISIBILITY_KM_MEAN": 10.0,
            "WIND_SPEED_10M_MS_MEAN": 2.0,
            "PRECIP_MM_DAY_ESTIMATE": 0.0,
            "SAMPLE_COVERAGE_FRAC": 1.0,
            "QC_STATE": "COMPLETE",
        }
    )
    cfg.surface_weather_path.mkdir(exist_ok=True)
    weather.to_parquet(cfg.surface_weather_path / "part.parquet", index=False)
    daylight = pd.DataFrame(
        [
            {"H3_INDEX": c, "DATE": d, "DAYLIGHT_FRACTION": 0.5}
            for c in sorted({h3.cell_to_parent(c, 4) for c in unique})
            for d in dates
        ]
    )
    cfg.daylight_path.mkdir(exist_ok=True)
    daylight.to_parquet(cfg.daylight_path / "part.parquet", index=False)
    for path in (cfg.surface_weather_manifest_path, cfg.daylight_manifest_path):
        path.write_text(
            json.dumps({"source_completeness": "complete", "temporal_coverage": {"fixture": True}})
        )
    download(config_path, overwrite=True)
    build(config_path, allow_partial=True, overwrite=True)
    inspect(config_path)
    from .dynamic import compute_dynamic_chunk, prepare_dynamic_basis

    published = load_land_reporting_config(config_path)
    source = pd.read_parquet(published.source_output_path)
    legacy_basis = prepare_dynamic_basis(published, static, source)
    legacy = compute_dynamic_chunk(published, legacy_basis, legacy_basis.dates)
    legacy_means = pd.Series(
        pd.DataFrame(legacy["LAND_EFFORT_PROXY_RAW"]).mean(axis=0).to_numpy(),
        index=legacy_basis.target_order,
        name="LEGACY_GENERAL_EQUAL_CHILD_RAW_MEAN",
    )
    current = (
        pd.read_parquet(published.daily_output_path)
        .groupby("H3_INDEX")
        .LAND_OBSERVATION_OPPORTUNITY_RAW.mean()
    )
    comparison = pd.concat([legacy_means, current], axis=1)
    comparison["DIFFERENCE_NEW_MINUS_LEGACY"] = (
        comparison.LAND_OBSERVATION_OPPORTUNITY_RAW - comparison.LEGACY_GENERAL_EQUAL_CHILD_RAW_MEAN
    )
    comparison.to_csv(directory / "method_comparison.csv")
    sites = build_observation_sites(facilities)
    estimates = {}
    for count in (5, 20, 80):
        design = sample_observation_sites(sites, samples_per_site=count, max_design_points=80)
        kernel, water_support = build_access_kernel(app, design, distance_bin_km=0.25)
        kernel.to_parquet(directory / f"sampling_reference_{count}.parquet", index=False)
        weighted = kernel.merge(
            water_support[["target_h3", "H3_INDEX", "R6_WATER_AREA_WEIGHT"]], on="target_h3"
        )
        weighted["value"] = weighted.canopy_kernel * weighted.R6_WATER_AREA_WEIGHT
        estimates[count] = weighted.groupby(["SCENARIO", "H3_INDEX"]).value.sum()
    comparison = {
        str(count): {
            "maximum_absolute_discrepancy_from_80": float((values - estimates[80]).abs().max()),
            "mean_absolute_discrepancy_from_80": float((values - estimates[80]).abs().mean()),
        }
        for count, values in estimates.items()
    }
    comparison["interpretation"] = (
        "Sampling discrepancy against a denser reference, not an exact invariance or monotonic convergence claim"
    )
    (directory / "sampling_convergence.json").write_text(json.dumps(comparison, indent=2))
    return config_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(run_smoke(args.output_dir))


if __name__ == "__main__":
    main()
