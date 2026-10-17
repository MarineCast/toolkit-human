"""Versioned normalized source bundles with explicit geographic support assertions."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely


@dataclass
class PopulationSources:
    """Original polygons, optional complete-support polygons, and source provenance.

    Coverage geometry asserts a complete geography inventory, not non-null population.
    Without coverage, all estimates are explicitly unavailable/partial. Source polygons
    alone never certify the completeness of an arbitrary surrounding region.
    """
    geographies: gpd.GeoDataFrame
    coverage: gpd.GeoDataFrame | None = None
    metadata: dict = field(default_factory=dict)

    def validate(self):
        frame = self.geographies
        required = {"source_id", "jurisdiction", "country", "vintage", "population", "geometry"}
        if not required.issubset(frame) or frame.crs is None:
            raise ValueError(f"Normalized sources require CRS and {sorted(required)}")
        if frame[list(required - {"population", "geometry"})].isna().any().any():
            raise ValueError("Source identity and vintage cannot be null")
        if frame.source_id.duplicated().any():
            raise ValueError("Duplicate source_id: merge acquisition tiles without duplicating geographies")
        for name in ("source_id", "jurisdiction", "country"):
            if frame[name].astype(str).str.strip().eq("").any():
                raise ValueError(f"Empty source {name}")
        numeric = pd.to_numeric(frame.population, errors="raise").astype(float)
        if ((numeric < 0) | np.isinf(numeric)).any():
            raise ValueError("Population must be nonnegative finite or null (unknown)")
        for label, geo in (("sources", frame), ("coverage", self.coverage)):
            if geo is None:
                continue
            if geo.crs is None or geo.geometry.isna().any() or geo.geometry.is_empty.any():
                raise ValueError(f"{label}: missing CRS or empty geometry")
            if (~geo.geometry.is_valid).any() or not geo.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
                raise ValueError(f"{label}: require valid Polygon/MultiPolygon geometry")
        return self

    def fingerprint(self):
        self.validate()
        digest = hashlib.sha256(json.dumps(self.metadata, sort_keys=True).encode())
        for geo in (self.geographies.sort_values("source_id"), self.coverage):
            if geo is None:
                digest.update(b"no_coverage")
                continue
            digest.update(str(geo.crs).encode())
            columns = sorted(set(geo.columns) - {geo.geometry.name})
            if columns:
                digest.update(pd.util.hash_pandas_object(geo[columns], index=False).values.tobytes())
            for value in shapely.to_wkb(geo.geometry.values):
                digest.update(value)
        return digest.hexdigest()


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bundle_inventory(path):
    path = Path(path)
    manifest = json.loads(path.read_text())
    if manifest.get("schema") != "human.population.sources.v1":
        raise ValueError("Unsupported population source manifest")
    for record in manifest["files"].values():
        candidate = path.parent / record["path"]
        if checksum(candidate) != record["sha256"]:
            raise ValueError(f"Source checksum mismatch: {candidate}")
    return manifest


def read_sources(path):
    path = Path(path)
    manifest = bundle_inventory(path)
    files = manifest["files"]
    return PopulationSources(
        gpd.read_parquet(path.parent / files["geographies"]["path"]),
        gpd.read_parquet(path.parent / files["coverage"]["path"]) if "coverage" in files else None,
        manifest["metadata"],
    ).validate()


def write_sources(sources, directory):
    """Write a content-addressed bundle; existing bundles are verified and reused."""
    sources.validate()
    directory = Path(directory) / sources.fingerprint()
    manifest_path = directory / "sources.json"
    if manifest_path.exists():
        bundle_inventory(manifest_path)
        return manifest_path
    directory.mkdir(parents=True, exist_ok=True)
    records = {}
    for label, geo in (("geographies", sources.geographies), ("coverage", sources.coverage)):
        if geo is None:
            continue
        path = directory / f"{label}.parquet"
        temp = path.with_suffix(".parquet.tmp")
        geo.to_parquet(temp, index=False, compression="zstd")
        temp.replace(path)
        records[label] = {"path": path.name, "sha256": checksum(path), "rows": len(geo)}
    manifest = {"schema": "human.population.sources.v1", "files": records, "metadata": sources.metadata,
                "fingerprint": sources.fingerprint()}
    temp = manifest_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    temp.replace(manifest_path)
    return manifest_path


def quarantine_cross_border_overlaps(sources, *, area_tolerance_m2=1):
    """Explicitly quarantine whole original geographies at disputed US/Canada boundaries.

    Geometry is preserved intact. Population becomes null for every source involved
    in an overlap; the original value remains a provenance-only column. Canonical
    estimates intersecting either source remain incomplete. No disputed population
    contributes to an available-source subtotal. Same-country overlaps remain errors.
    """
    return _qualify_cross_border_overlaps(sources, area_tolerance_m2=area_tolerance_m2, retain_counts=False)


def retain_distinct_national_counts(sources, *, area_tolerance_m2=1):
    """Retain independently enumerated US/Canada counts despite provider boundary overlaps.

    Counts belong to distinct national populations. Each source contributes once,
    using its unchanged full original polygon denominator. An overlap of mapped
    support does not duplicate a census record. Same-country overlaps still fail.
    This explicit policy does not claim positional agreement of the two providers.
    """
    return _qualify_cross_border_overlaps(sources, area_tolerance_m2=area_tolerance_m2, retain_counts=True)


def _qualify_cross_border_overlaps(sources, *, area_tolerance_m2, retain_counts):
    from shapely.strtree import STRtree
    sources.validate()
    original = sources.geographies.to_crs(4326).sort_values('source_id').reset_index(drop=True)
    if sources.metadata.get('cross_border_overlap_policy'):
        raise ValueError('Cross-border quarantine requires an unreconciled source bundle')
    bounds = original.total_bounds
    area_crs = f'+proj=laea +lat_0={(bounds[1]+bounds[3])/2} +lon_0={(bounds[0]+bounds[2])/2} +datum=WGS84 +units=m'
    projected = original.to_crs(area_crs)
    geometries = projected.geometry.to_numpy()
    tree = STRtree(geometries)
    affected, conflicts, records = {}, [], []
    for start in range(0,len(original),1024):
        pairs = tree.query(geometries[start:start+1024],predicate='intersects')
        a,b = pairs[0]+start,pairs[1]
        mask=a<b
        a,b=a[mask],b[mask]
        interior=shapely.relate_pattern(geometries[a],geometries[b],"2********")
        a,b=a[interior],b[interior]
        intersections=shapely.intersection(geometries[a],geometries[b])
        areas=shapely.area(intersections)
        for i,j,area,geometry in zip(a,b,areas,intersections):
            if area <= area_tolerance_m2:
                continue
            if original.country.iloc[i] == original.country.iloc[j]:
                raise ValueError(f'Same-country overlapping sources: {original.source_id.iloc[i]}, {original.source_id.iloc[j]}')
            affected.setdefault(i,[]).append(geometry)
            affected.setdefault(j,[]).append(geometry)
            conflicts.append(geometry)
            records.append({'source_ids':[original.source_id.iloc[i],original.source_id.iloc[j]],'area_m2':float(area)})
    if not conflicts:
        return sources
    if retain_counts:
        if set(original.country) - {"US", "CA"}:
            raise ValueError("Distinct national census overlap policy requires US/CA sources")
        metadata = {**sources.metadata, "allocation_crs": area_crs,
            "cross_border_overlap_policy": "retain_distinct_national_census_counts",
            "cross_border_conflicts": records,
            "cross_border_overlap_source_ids": original.loc[sorted(affected), "source_id"].tolist(),
            "cross_border_overlap_source_count": len(affected),
            "cross_border_conflict_union_area_m2": float(shapely.union_all(conflicts).area),
            "coverage_limitations": [*sources.metadata.get("coverage_limitations", []),
                "US/Canada provider boundaries overlap locally. Distinct national census records retain their original counts and full polygon areas; cartographic overlap does not certify positional agreement. Each record contributes once, without clipping or redistributing its count."]}
        return PopulationSources(projected, sources.coverage, metadata).validate()
    # Preserve whole original support. Near-coincident cross-provider boundaries can
    # be numerically unstable under polygon subtraction; never manufacture slivers.
    projected["vintage"] = projected.vintage.astype(str)
    projected["boundary_quarantined"] = False
    projected["quarantined_original_population"] = np.nan
    affected_indices = sorted(affected)
    projected.loc[affected_indices, "quarantined_original_population"] = projected.loc[affected_indices, "population"].to_numpy()
    projected.loc[affected_indices, "population"] = np.nan
    projected.loc[affected_indices, "boundary_quarantined"] = True
    metadata = {**sources.metadata, "allocation_crs": area_crs,
        "cross_border_overlap_policy": "quarantine_entire_sources_unknown",
        "cross_border_conflicts": records,
        "quarantined_source_ids": projected.loc[affected_indices, "source_id"].tolist(),
        "quarantined_source_count": len(affected_indices),
        "quarantined_known_population": float(projected.loc[affected_indices, "quarantined_original_population"].sum()),
        "cross_border_conflict_union_area_m2": float(shapely.union_all(conflicts).area),
        "coverage_limitations": [*sources.metadata.get("coverage_limitations", []),
            "Every original census geography involved in a cross-border overlap is quarantined in full as unknown; available subtotals exclude its entire population."]}
    return PopulationSources(projected,sources.coverage,metadata).validate()
