"""Distance-binned access kernels using the production raster preparation and LOS.

The canonical modeled water support is the full water-mask pixel inventory,
assigned to R7 by pixel center and to R6 by logical H3 parent. Pixel areas use
that fixed projected grid. No outgoing-target normalization is applied.
"""

from __future__ import annotations

import hashlib
import json
import resource
import time
from dataclasses import asdict, replace
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import rasterio
from pyproj import CRS, Transformer
from scipy.spatial import cKDTree

from human.core.artifacts import checksum_path

from ...viewshed.config.distance import load_distance_weight_config
from ...viewshed.prepare.area.context import prepare_batch_context
from ...viewshed.prepare.area.inputs import load_source_cells
from ...viewshed.prepare.area.raster_stack import ensure_canonical_raster_stack
from ...viewshed.weights.distance.compute import distance_weight_values
from ...viewshed.weights.terrain.los import run_gdal_viewshed_to_bool_array
from .support import canonical_target_support

ACCESS_KERNEL_VERSION = "source_cell_radius_reference_kernel_v3"


def build_access_kernel(
    app, samples: gpd.GeoDataFrame, *, distance_bin_km: float, max_workers: int = 1
):
    """Build both bare-earth and canopy diagnostics without a shared mutable DEM.

    Fail on an uncomputed LOS. Successful invisible and out-of-radius pixels
    contribute certified zeros (out-of-radius absence is sparse). A source without represented sites is absent
    from this table and must be treated as unsupported by its consumer.
    """
    if not 1 <= max_workers <= 4:
        raise ValueError("Access kernel workers must be between one and four")
    metric = CRS.from_user_input(app.viewshed.crs_projected)
    if not metric.is_projected or not np.isclose(metric.axis_info[0].unit_conversion_factor, 1.0):
        raise ValueError("Access kernels require a projected raster grid in metres")
    if distance_bin_km <= 0:
        raise ValueError("Distance bin width must be positive")
    if app.viewshed.backend != "gdal" or app.source_type != "land":
        raise ValueError("Access-conditioned kernels require land GDAL viewsheds")
    stack = ensure_canonical_raster_stack(app, include_canopy=True)
    started = time.perf_counter()
    # Build the canonical inventory once in raster blocks, then index centers.
    coordinates, cell_blocks = [], []
    with rasterio.open(stack.water_mask_path) as water:
        crs = water.crs
        transform = water.transform
        area = abs(transform.a * transform.e - transform.b * transform.d)
        converter = Transformer.from_crs(crs, 4326, always_xy=True)
        for _, window in water.block_windows(1):
            rr, cc = np.nonzero(water.read(1, window=window) == 1)
            xs, ys = rasterio.transform.xy(water.window_transform(window), rr, cc)
            lon, lat = converter.transform(xs, ys)
            coordinates.append(np.column_stack([xs, ys]))
            cell_blocks.append(
                np.array([h3.latlng_to_cell(y, x, 7) for x, y in zip(lon, lat, strict=True)])
            )
    xy = np.concatenate(coordinates)
    cells = np.concatenate(cell_blocks)
    del coordinates, cell_blocks
    tree = cKDTree(xy)
    unique, codes, counts = np.unique(cells, return_inverse=True, return_counts=True)
    support = canonical_target_support(
        pd.DataFrame({"target_h3": unique, "target_water_area_m2": counts * area})
    )
    if samples.empty:
        raise ValueError(
            "Strict access-conditioned kernel unsupported: no usable observation geometry"
        )
    prepared = samples.copy()
    for column in ("SAMPLE_WEIGHT", "MAPPED_SITE_WEIGHT", "VERIFIED_SITE_WEIGHT"):
        if not np.isfinite(prepared[column]).all() or (prepared[column] < 0).any():
            raise ValueError("Unresolved or negative sample allocation")
    prepared = (
        prepared.loc[
            (prepared.SAMPLE_WEIGHT > 0)
            & ((prepared.MAPPED_SITE_WEIGHT > 0) | (prepared.VERIFIED_SITE_WEIGHT > 0))
        ]
        .sort_values(["source_h3", "sample_id"])
        .copy()
    )
    if prepared.empty or prepared.sample_id.duplicated().any():
        raise ValueError("Require unique eligible sample identities")
    prepared["source_h3_cell"] = prepared.source_h3
    prepared["sample_index"] = prepared.groupby("source_h3").cumcount() + 1
    prepared["lon"], prepared["lat"] = prepared.geometry.x, prepared.geometry.y
    count = prepared.groupby("source_h3").sample_id.transform("size")
    diagnostics = {
        "source_type": "land",
        "active_source_fraction": 1.0,
        "source_sampling_mode": "represented_access_sites",
        "source_sampling_projected_crs": str(crs),
        "source_sampling_candidate_grid_side": 0,
        "source_sampling_max_design_points": count,
        "source_sampling_component_count": count,
        "sample_points_max": count,
        "sample_points_min": count,
        "sample_points_requested": count,
        "sample_points_actual": count,
    }
    for key, value in diagnostics.items():
        prepared[key] = value
    metadata = load_source_cells(app)
    distance = load_distance_weight_config(app.raw_config)
    partitions = []
    # This independent plan is formed before any LOS output exists. A complete
    # partition certifies successful evaluation of every planned sample; sparse
    # absence then means outside the radius, with full water area retained.
    plan = (
        prepared[
            [
                "source_h3",
                "sample_id",
                "SAMPLE_WEIGHT",
                "MAPPED_SITE_WEIGHT",
                "VERIFIED_SITE_WEIGHT",
            ]
        ]
        .sort_values(["source_h3", "sample_id"])
        .to_dict("records")
    )
    runtime = kernel_runtime_info()
    signature = hashlib.sha256(
        json.dumps(
            {
                "version": ACCESS_KERNEL_VERSION,
                "plan": plan,
                "runtime": runtime,
                "geometry": prepared.geometry.to_wkt().tolist(),
                "config": app.raw_config,
                "effective_viewshed": asdict(app.viewshed),
                "implementation": {
                    str(path.relative_to(Path(__file__).parents[2])): checksum_path(path)
                    for path in [
                        Path(__file__),
                        *sorted((Path(__file__).parents[2] / "viewshed").rglob("*.py")),
                    ]
                },
                "endpoint": checksum_path(stack.endpoint_dem_path),
                "canopy": checksum_path(stack.base_canopy_surface_path),
                "water": checksum_path(stack.water_mask_path),
                "bin": distance_bin_km,
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    cache = Path(stack.endpoint_dem_path).parent / "access_partitions" / signature
    cache.mkdir(parents=True, exist_ok=True)
    profile = {
        "regional_water_pixels": len(cells),
        "processed_pixels": 0,
        "max_observer_pixels": 0,
        "resumed_partitions": 0,
        "partitions": [],
        "execution_modes": [],
    }
    value_columns = ["bare_earth_kernel", "canopy_kernel", "evaluated_support"]
    keys = ["source_h3", "target_h3", "DISTANCE_BIN_INDEX", "SCENARIO"]

    def process_source(item):
        batch_index, (source, group) = item
        partition_path = cache / f"{source}.parquet"
        metadata_path = cache / f"{source}.json"
        if partition_path.exists() and metadata_path.exists():
            saved = json.loads(metadata_path.read_text())
            if saved.get("evaluation_state") == "complete" and saved["sha256"] == checksum_path(
                partition_path
            ):
                return partition_path, saved, True
        aggregate = None
        processed = maximum = 0
        modes = set()
        context = prepare_batch_context(
            app,
            [source],
            batch_index,
            source_cells_gdf=metadata,
            source_sample_points_gdf=group,
        )
        for _, sample in group.to_crs(crs).iterrows():
            x, y = sample.geometry.x, sample.geometry.y
            selected = np.array(
                sorted(tree.query_ball_point([x, y], app.viewshed.max_distance_m)), dtype=int
            )
            xs, ys = xy[selected].T
            processed += len(selected)
            maximum = max(maximum, len(selected))
            distances = np.hypot(xs - x, ys - y) / 1000.0
            bins = np.rint(distances / distance_bin_km).astype("int32")
            attenuation = distance_weight_values(
                distances,
                distance,
                max_distance_km=app.viewshed.max_distance_m / 1000,
            )
            pixel_frame = pd.DataFrame({"target_h3": cells[selected], "DISTANCE_BIN_INDEX": bins})
            for surface in ("bare_earth", "canopy"):
                surface_app = replace(app, viewshed=replace(app.viewshed, surface_model=surface))
                surface_context = (
                    replace(context, analysis_dem_path=context.endpoint_dem_path)
                    if surface == "bare_earth"
                    else context
                )
                result = run_gdal_viewshed_to_bool_array(
                    context=surface_context,
                    app=surface_app,
                    observer_x=x,
                    observer_y=y,
                    observer_height_m=app.viewshed.observer_eye_height_m,
                    target_height_m=app.viewshed.target_height_m,
                    max_distance_m=app.viewshed.max_distance_m,
                    curvature_coefficient=app.viewshed.curvature_coefficient,
                    source_cell=source,
                    sample_index=int(sample.sample_index),
                    observer_id=sample.sample_id,
                )
                modes.add(result.metadata.get("gdal_execution_mode", "gdal_unknown"))
                # Convert world coordinates to this observer's validated output window.
                local_cols, local_rows = (~context.water_transform) * (xs, ys)
                rr = np.floor(local_rows).astype(int) - result.y_start
                cc = np.floor(local_cols).astype(int) - result.x_start
                inside = (
                    (rr >= 0)
                    & (cc >= 0)
                    & (rr < result.visible.shape[0])
                    & (cc < result.visible.shape[1])
                )
                if not inside.all():
                    raise ValueError(
                        "Incomplete LOS window for in-radius canonical water; geometry unresolved"
                    )
                visible = np.zeros(len(selected), dtype=float)
                visible[inside] = result.visible[rr[inside], cc[inside]]
                pixel_frame[f"{surface}_kernel"] = visible * attenuation / counts[codes[selected]]
            # Fractions of full modeled child water support, independent of LOS.
            pixel_frame["evaluated_support"] = 1.0 / counts[codes[selected]]
            binned = pixel_frame.groupby(["target_h3", "DISTANCE_BIN_INDEX"], as_index=False).sum()
            for scenario in ("MAPPED", "VERIFIED", "VERIFIED_REFERENCE"):
                allocation = (
                    sample.MAPPED_SITE_WEIGHT
                    if scenario == "VERIFIED_REFERENCE"
                    else sample[f"{scenario}_SITE_WEIGHT"]
                )
                if scenario == "VERIFIED_REFERENCE" and sample.VERIFIED_SITE_WEIGHT <= 0:
                    continue
                weight = float(allocation * sample.SAMPLE_WEIGHT)
                if weight == 0:
                    continue
                part = binned.copy()
                part["source_h3"], part["SCENARIO"] = source, scenario
                for column in ("bare_earth_kernel", "canopy_kernel", "evaluated_support"):
                    part[column] *= weight
                part = part.set_index(keys)[value_columns]
                aggregate = part if aggregate is None else aggregate.add(part, fill_value=0)
        if aggregate is None:
            return None
        partition = aggregate.reset_index()
        temporary = partition_path.with_suffix(".tmp.parquet")
        partition.to_parquet(temporary, index=False)
        temporary.replace(partition_path)
        saved = {
            "source_h3": source,
            "rows": len(partition),
            "bytes": partition_path.stat().st_size,
            "processed_pixels": processed,
            "max_observer_pixels": maximum,
            "execution_modes": sorted(modes),
            "sha256": checksum_path(partition_path),
            "evaluation_state": "complete",
        }
        metadata_path.write_text(json.dumps(saved, indent=2))
        return partition_path, saved, False

    # Each worker owns its source context, raster handles and output paths.
    # Aggregate in source order so concurrency does not change table ordering.
    from concurrent.futures import ThreadPoolExecutor

    jobs = enumerate(prepared.groupby("source_h3", sort=True))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for result in executor.map(process_source, jobs):
            if result is None:
                continue
            partition_path, saved, resumed = result
            partitions.append(partition_path)
            profile["resumed_partitions"] += int(resumed)
            profile["processed_pixels"] += 0 if resumed else saved["processed_pixels"]
            profile["max_observer_pixels"] = max(
                profile["max_observer_pixels"], saved["max_observer_pixels"]
            )
            profile["partitions"].append(saved)
            profile["execution_modes"].extend(saved["execution_modes"])
    profile["max_workers"] = max_workers
    if not partitions:
        raise ValueError("Strict access-conditioned kernel has no eligible weighted samples")
    kernel = pd.concat([pd.read_parquet(path) for path in partitions], ignore_index=True)
    kernel["KERNEL_ALGORITHM_VERSION"] = ACCESS_KERNEL_VERSION
    profile["elapsed_seconds"] = time.perf_counter() - started
    # macOS ru_maxrss is bytes; Linux reports KiB.
    import sys

    profile["process_peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    profile["execution_modes"] = sorted(set(profile["execution_modes"]))
    support.attrs["evaluation_plan"] = {
        "samples": plan,
        "state": "complete",
        "version": ACCESS_KERNEL_VERSION,
        "radius_m": app.viewshed.max_distance_m,
        "domain_rule": "canonical water pixel centers; inclusive metric radius; absent pairs are proven out of radius only after all planned samples complete",
        "signature": signature,
        "kernel_digest": kernel_digest(kernel),
        "support_digest": support_digest(support),
        "profile": profile,
        "runtime": runtime,
    }
    return kernel, support


def kernel_digest(kernel):
    """Bind a completed evaluation certificate to exact sparse rows."""
    columns = [
        "source_h3",
        "target_h3",
        "SCENARIO",
        "DISTANCE_BIN_INDEX",
        "canopy_kernel",
        "bare_earth_kernel",
        "evaluated_support",
    ]
    frame = kernel[columns].sort_values(columns[:4]).reset_index(drop=True)
    return hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=False).values.tobytes()
    ).hexdigest()


def support_digest(support):
    frame = support.sort_values("target_h3").reset_index(drop=True)
    return hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=False).values.tobytes()
    ).hexdigest()


def kernel_runtime_info():
    import shutil
    import subprocess
    from importlib.metadata import version

    runtime = {
        name: version(name) for name in ("numpy", "pandas", "scipy", "rasterio", "h3", "pyproj")
    }
    try:
        from osgeo import gdal

        runtime["gdal_python"] = gdal.VersionInfo()
    except ImportError:
        runtime["gdal_python"] = "unavailable"
    executable = shutil.which("gdal_viewshed")
    runtime["gdal_cli"] = (
        subprocess.run(
            [executable, "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
        if executable
        else "unavailable"
    )
    return runtime
