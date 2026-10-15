"""Static resident-population public API and streaming, content-addressed products."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from importlib.resources import files
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import platform
import resource
import time

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS, Transformer
from shapely.geometry import box
from shapely.ops import transform
import yaml

from .geometry import target_cells, cell_polygon, radius_polygon, radius_vertex_count, source_bounds
from .sources import PopulationSources, bundle_inventory, checksum, read_sources

LOGGER = logging.getLogger(__name__)
METHOD_VERSION = "area_weighted_original_polygons.v1.2"
DEFAULT_RADII = (25, 50, 100, 250)


@dataclass(frozen=True)
class ResourceLimits:
    max_targets: int = 100000
    max_sources: int = 1000000
    batch_size: int = 128
    workers: int = 1
    max_radius_km: float = 500
    group_size_m: float = 10000
    buffer_tolerance_m: float = 25
    edge_step_m: float = 1000
    area_tolerance_m2: float = 1
    max_elapsed_seconds: float = 14400

    def validate(self):
        for name in ("max_targets", "max_sources", "batch_size", "workers"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.workers > 32:
            raise ValueError("workers must be <= 32")
        for name in ("max_radius_km", "group_size_m", "buffer_tolerance_m", "edge_step_m", "area_tolerance_m2", "max_elapsed_seconds"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.buffer_tolerance_m > 100 or self.edge_step_m > 10000 or self.area_tolerance_m2 > 100:
            raise ValueError("Geometry tolerances exceed supported bounds (100 m, 10 km edges, 100 m2 area)")
        return self


class PopulationCoverageError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__("Incomplete population source coverage; provision original census polygons/values "
                         "and verified coverage for the expanded source extent, or explicitly use "
                         "allow_partial=True. " + json.dumps(report, sort_keys=True))


@dataclass
class PopulationResult:
    """Lazy pandas table plus metadata; batches/export do not load the whole product."""
    path: Path
    metadata: dict
    cache_hit: bool = False

    @property
    def table(self):
        return pd.read_parquet(self.path)

    def iter_batches(self, batch_size=1024):
        for batch in pq.ParquetFile(self.path).iter_batches(batch_size=batch_size):
            yield batch.to_pandas()

    def to_parquet(self, output):
        output = Path(output)
        companion = output.with_suffix(".metadata.json")
        if output.exists() or companion.exists():
            raise FileExistsError(f"Population export will not replace {output} or {companion}")
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(".parquet.tmp")
        try:
            with self.path.open("rb") as source, temp.open("xb") as destination:
                import shutil
                shutil.copyfileobj(source, destination)
            temp.replace(output)
            payload = {**self.metadata, "artifact": {"path": output.name, "sha256": checksum(output)}}
            companion.write_text(json.dumps(payload, indent=2, sort_keys=True))
        finally:
            temp.unlink(missing_ok=True)
        return output


def _label(value):
    return format(value, ".12g").replace(".", "_")


def _config(workspace, config):
    if config is not None:
        return yaml.safe_load(Path(config).read_text())
    path = workspace / "config/data/human/demography_and_presence/population_static.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text())
    return yaml.safe_load(files("human").joinpath("resources/config/data/human/demography_and_presence/population_static.yaml").read_text())


def get_population(*, bbox=None, target_h3=None, h3_res=None, radii_km=None,
                   include_bands=False, workspace=None, mode="offline", allow_partial=False,
                   sources: PopulationSources | None = None, source_manifest=None,
                   resource_limits=None, config=None, dry_run=False, reference=False):
    """Get static area-allocated resident population at explicitly requested H3 centers.

    Offline never contacts a provider. Live acquisition requires a configured bounded
    census source specification. `dry_run` returns a plan without creating directories,
    downloading, normalizing inputs, or calculating population. See docs/population.md.
    """
    started = time.perf_counter()
    workspace = Path(workspace or os.environ.get("HUMAN_WORKSPACE", Path.cwd())).expanduser().resolve()
    cfg = _config(workspace, config)
    h3_res = cfg.get("h3_resolution", 7) if h3_res is None else h3_res
    options = {**cfg.get("resource_limits", {}), **(resource_limits or {})}
    limits = ResourceLimits(**options).validate()
    radii = tuple(float(r) for r in (radii_km if radii_km is not None else cfg.get("radii_km", DEFAULT_RADII)))
    if not radii or any(not math.isfinite(r) or r <= 0 or r > limits.max_radius_km for r in radii):
        raise ValueError(f"radii_km must be finite, positive and <= {limits.max_radius_km}")
    if tuple(sorted(set(radii))) != radii or len({_label(r) for r in radii}) != len(radii):
        raise ValueError("radii_km must be unique and strictly increasing")
    if mode not in ("offline", "live"):
        raise ValueError("mode must be offline or live")
    cells = target_cells(bbox, target_h3, h3_res, limits.max_targets)
    bounds = source_bounds(cells, radii[-1], limits.buffer_tolerance_m)
    if bounds[0] <= -180 or bounds[2] >= 180 or bounds[1] <= -85 or bounds[3] >= 85 or bounds[2]-bounds[0] > 30 or bounds[3]-bounds[1] > 30:
        raise ValueError("Required full-cell/radius support exceeds the bounded regional projection limits")
    manifest = Path(source_manifest or cfg.get("source_manifest", "cache/population/sources.json"))
    if not manifest.is_absolute():
        manifest = workspace / manifest
    inventory, missing = None, []
    if sources is None and manifest.exists():
        inventory = bundle_inventory(manifest)
        fingerprint = checksum(manifest)
    elif sources is not None:
        fingerprint = sources.fingerprint()
    else:
        fingerprint = None
        missing.append(f"Normalized census bundle not found: {manifest}")
    # Execution scheduling does not alter scientific identity.
    parameters = {k: v for k, v in asdict(limits).items() if k in (
        "group_size_m", "buffer_tolerance_m", "edge_step_m", "area_tolerance_m2")}
    aligned_vertices = None
    if any((b-a)*1000 <= 4*limits.buffer_tolerance_m for a,b in zip(radii,radii[1:])):
        aligned_vertices = radius_vertex_count(radii[-1], limits.buffer_tolerance_m)
        parameters["aligned_radius_vertices"] = aligned_vertices
    identity = {"schema": "human.population.static.v1", "method": METHOD_VERSION,
                "sources": fingerprint, "cells": cells, "h3_res": h3_res,
                "radii_km": radii, "include_bands": include_bands,
                "parameters": parameters, "reference": reference}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache_dir = workspace / "cache/population/products" / key
    cached, meta_path = cache_dir / "population.parquet", cache_dir / "metadata.json"
    plan = {"targets": len(cells), "h3_resolution": h3_res, "target_selection": "center_in_bbox_inclusive" if bbox is not None else "explicit_h3",
            "source_extent_wgs84": bounds, "radii_km": radii, "source_manifest": str(manifest),
            "source_available": fingerprint is not None, "cache_available": cached.exists() and meta_path.exists(),
            "missing_work": missing, "resource_limits": asdict(limits), "mode": mode,
            "acquisition": cfg.get("acquisition", {}),
            "source_coverage": inventory.get("metadata", {}) if inventory else (sources.metadata if sources else None)}
    plan["raw_source_availability"] = [
        {"jurisdiction": row.get("jurisdiction"), "role": role,
         "path": str(workspace / row[role]), "available": (workspace / row[role]).exists()}
        for country in ("us", "canada") for row in cfg.get("acquisition", {}).get(country, [])
        for role in ("geography", "population") if row.get(role)]
    if dry_run:
        return plan
    if cached.exists() and meta_path.exists():
        metadata = json.loads(meta_path.read_text())
        if checksum(cached) != metadata["cache_sha256"]:
            raise ValueError(f"Population cache checksum mismatch: {cached}")
        if metadata["coverage_report"]["incomplete_metrics"] and not allow_partial:
            raise PopulationCoverageError(metadata["coverage_report"])
        return PopulationResult(cached, metadata, cache_hit=True)
    if sources is None and inventory is None:
        if mode == "offline":
            raise FileNotFoundError(missing[0] + "; prepare_census_sources or supply PopulationSources")
        from .census import prepare_census_sources
        manifest = prepare_census_sources(workspace=workspace, source_extent=bounds,
                                         specification=cfg.get("acquisition", {}), mode="live")
        return get_population(bbox=bbox, target_h3=target_h3, h3_res=h3_res, radii_km=radii,
            include_bands=include_bands, workspace=workspace, mode="offline", allow_partial=allow_partial,
            source_manifest=manifest, resource_limits=options, config=config, reference=reference)
    if sources is None:
        LOGGER.info("Loading checksum-verified normalized population sources")
        sources = read_sources(manifest)
    sources.validate()
    if len(sources.geographies) > limits.max_sources:
        raise ValueError("Source count exceeds max_sources; provision a bounded intersecting source bundle")
    from .engine import AllocationIndex, CoverageIndex
    centers = np.array([h3.cell_to_latlng(c) for c in cells])
    lat, lon = centers.mean(axis=0)
    area_crs = CRS.from_proj4(f"+proj=laea +lat_0={lat:.12f} +lon_0={lon:.12f} +datum=WGS84 +units=m +no_defs")
    if sources.metadata.get("allocation_crs"):
        area_crs = CRS.from_user_input(sources.metadata["allocation_crs"])
        if area_crs.coordinate_operation is None or "Lambert Azimuthal Equal Area" not in area_crs.coordinate_operation.method_name:
            raise ValueError("Source allocation_crs must use Lambert Azimuthal Equal Area")
    project = Transformer.from_crs(4326, area_crs, always_xy=True).transform
    frame = sources.geographies.sort_values("source_id").to_crs(area_crs).reset_index(drop=True)
    coverage = sources.coverage.to_crs(area_crs) if sources.coverage is not None else None
    LOGGER.info("Building population spatial index for %s original sources", len(frame))
    index = AllocationIndex(frame,
        allow_quarantined_cross_border=sources.metadata.get("cross_border_overlap_policy") == "quarantine_entire_sources_unknown",
        group_size_m=limits.group_size_m, overlap_tolerance_m2=limits.area_tolerance_m2)
    import shapely
    extent = transform(project, shapely.segmentize(box(*bounds), 0.05))
    LOGGER.info("Preparing complete geographic support and explicit coverage gaps")
    coverage_key = hashlib.sha256(json.dumps({"source_fingerprint":fingerprint, "area_crs":area_crs.to_wkt(), "method":"coverage_union.v1"}, sort_keys=True).encode()).hexdigest()
    support = CoverageIndex(coverage, extent, limits.area_tolerance_m2,
        cache_path=workspace / "cache/population/coverage" / (coverage_key + ".wkb"))
    counts, samples = {}, []
    metadata = {"schema": identity["schema"], "method": METHOD_VERSION, "measurement_type": "area_allocated_estimate",
        "cache_key": key, "h3_resolution": h3_res, "radii_km": radii, "include_bands": include_bands,
        "target_selection": plan["target_selection"], "bbox": bbox, "source_extent_wgs84": bounds,
        "sources": sources.metadata, "source_fingerprint": fingerprint,
        "code_version": "toolkit-human 0.1.0; " + METHOD_VERSION,
        "source_vintages": sorted(set(zip(frame.loc[frame.country.isin(["US", "CA"]), "country"].astype(str), frame.loc[frame.country.isin(["US", "CA"]), "vintage"].astype(str)))),
        "area_crs": area_crs.to_string(), "density_denominator": "full_target_h3_polygon_area_km2",
        "area_method": "WGS84_Lambert_azimuthal_equal_area_intersection", "method_parameters": parameters,
        "resource_limits": asdict(limits), "model_eligible": False,
        "limitations": ["Uniform population per original census polygon area; unrounded floating-point allocations.",
            "Mixed-vintage static baseline; no annual interpolation or daily expansion.",
            "Overlapping neighborhoods must not be summed across target cells.",
            "Unverified support and null source values are unknown, including unverified offshore areas.",
            "Resident population/proximity is not visitation, observer effort, access, detection or whale occurrence."],
        "benchmark": {"source_count": len(frame), "target_count": len(cells), "radii_km": radii,
            "python": platform.python_version(), "platform": platform.platform(),
            "algorithm": "reference" if reference else "contained_groups_boundary_intersections",
            "coverage_geometry_cache_hit": support.cache_hit}}
    metric_names = ["population_count", *[f"population_within_{_label(r)}km" for r in radii]]
    metadata["fields"] = {name: {"units": "persons", "coverage_status": f"{name}__coverage_status",
        "available_source_subtotal": f"{name}__available_source_subtotal", "measurement_type": "area_allocated_estimate"} for name in metric_names}
    metadata["fields"]["population_density_per_km2"] = {"units": "persons/km2", "coverage_status": "population_count__coverage_status"}

    def calculate(cell):
        lat, lon = h3.cell_to_latlng(cell)
        polygon = transform(project, cell_polygon(cell, limits.edge_step_m))
        areas = [polygon, *[transform(project, radius_polygon(lat, lon, r, limits.buffer_tolerance_m, vertices=aligned_vertices)) for r in radii]]
        # Cell support can exceed the smallest or even maximum radius at coarse resolution.
        candidates = index.candidates(areas[-1].union(polygon))
        row = {"h3_index": cell, "h3_resolution": h3_res, "cell_area_km2": polygon.area / 1e6}
        for name, area in zip(metric_names, areas):
            unknown = index.unknown_sources(area, limits.area_tolerance_m2)
            geographic_complete = support.complete(area)
            complete = geographic_complete and not unknown
            subtotal = index.total(area, candidates=candidates, reference=reference)
            known = subtotal > 0 or len(index.known_tree.query(area, predicate="intersects")) > 0
            status = ("known_zero" if subtotal == 0 else "complete") if complete else ("partial" if known else "unavailable")
            row[name] = subtotal if complete else None
            row[f"{name}__available_source_subtotal"] = subtotal if known or complete else None
            row[f"{name}__coverage_status"] = status
            row[f"{name}__missing_sources"] = json.dumps(unknown, separators=(",", ":"))
            row[f"{name}__unverified_geographic_support"] = not geographic_complete
        row["population_density_per_km2"] = row["population_count"] / row["cell_area_km2"] if row["population_count"] is not None else None
        if include_bands:
            previous, lower = 0.0, "0"
            for r in radii:
                upper = _label(r)
                cumulative = row[f"population_within_{upper}km"]
                value = cumulative - previous if cumulative is not None and previous is not None else None
                if value is not None and value < -1e-7:
                    raise ArithmeticError("Non-monotone cumulative population beyond numerical tolerance")
                row[f"population_{lower}_{upper}km"] = max(0.0, value) if value is not None else None
                previous, lower = cumulative, upper
        return row

    cache_dir.mkdir(parents=True, exist_ok=True)
    import uuid
    temp = cache_dir / f"{uuid.uuid4().hex}.parquet.tmp"
    writer = None
    executor = ThreadPoolExecutor(max_workers=limits.workers) if limits.workers > 1 else None
    try:
        for start in range(0, len(cells), limits.batch_size):
            if time.perf_counter() - started > limits.max_elapsed_seconds:
                raise TimeoutError("Population calculation exceeded max_elapsed_seconds")
            batch = cells[start:start + limits.batch_size]
            rows = list(executor.map(calculate, batch)) if executor else [calculate(c) for c in batch]
            for row in rows:
                for name in metric_names:
                    status = row[f"{name}__coverage_status"]
                    counts.setdefault(name, {}).setdefault(status, 0)
                    counts[name][status] += 1
                    if status in ("partial", "unavailable") and len(samples) < 20:
                        samples.append({"h3_index": row["h3_index"], "metric": name, "status": status,
                            "missing_sources": json.loads(row[f"{name}__missing_sources"]),
                            "unverified_geographic_support": row[f"{name}__unverified_geographic_support"]})
            data = pd.DataFrame(rows)
            for name in data:
                if name.startswith("population_") and "__" not in name:
                    data[name] = data[name].astype(float)
                elif name.endswith("__available_source_subtotal"):
                    data[name] = data[name].astype(float)
            table = pa.Table.from_pandas(data, preserve_index=False)
            if writer is None:
                schema = table.schema.with_metadata({b"human_population": json.dumps(metadata, sort_keys=True).encode()})
                writer = pq.ParquetWriter(temp, schema, compression="zstd")
            writer.write_table(table)
            LOGGER.info("Population targets: %s/%s", min(start + len(batch), len(cells)), len(cells))
        writer.close()
        writer = None
        temp.replace(cached)
    finally:
        if executor:
            executor.shutdown()
        if writer:
            writer.close()
        temp.unlink(missing_ok=True)
    incomplete = sum(values.get("partial", 0) + values.get("unavailable", 0) for values in counts.values())
    metadata["coverage_report"] = {"incomplete_metrics": incomplete, "counts": counts, "examples": samples,
        "source_limitations": sources.metadata.get("coverage_limitations", []),
        "missing_jurisdictions": sources.metadata.get("missing_jurisdictions", []), "source_extent_wgs84": bounds}
    metadata["benchmark"]["elapsed_seconds"] = time.perf_counter() - started
    # ru_maxrss is bytes on macOS and KiB on Linux; this is process high-water RSS.
    metadata["benchmark"]["process_peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**2 if platform.system() == "Darwin" else 1024)
    metadata["cache_sha256"] = checksum(cached)
    meta_temp = cache_dir / "metadata.json.tmp"
    meta_temp.write_text(json.dumps(metadata, indent=2, sort_keys=True))
    meta_temp.replace(meta_path)
    if incomplete and not allow_partial:
        raise PopulationCoverageError(metadata["coverage_report"])
    return PopulationResult(cached, metadata)
