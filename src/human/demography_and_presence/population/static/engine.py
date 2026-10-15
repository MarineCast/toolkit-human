"""Indexed area-weighted reference and grouped calculation on original support."""
from __future__ import annotations

import math
import hashlib
import json
from pathlib import Path
import uuid
import threading
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import box
from shapely.strtree import STRtree


class AllocationIndex:
    def __init__(self, frame, *, allow_quarantined_cross_border=False, group_size_m=10000, overlap_tolerance_m2=1.0):
        self.geometry = frame.geometry.to_numpy()
        self.population = pd.to_numeric(frame.population).to_numpy(dtype=float, na_value=np.nan)
        self.areas = shapely.area(self.geometry)
        if (self.areas <= 0).any() or not np.isfinite(self.areas).all():
            raise ValueError("Sources must have finite positive original areas")
        self.ids = frame.source_id.astype(str).to_numpy()
        self.jurisdictions = frame.jurisdiction.astype(str).to_numpy()
        self.tree = STRtree(self.geometry)
        quarantined = frame.get("boundary_quarantined", pd.Series(False, index=frame.index)).to_numpy(dtype=bool)
        countries = frame.country.to_numpy()
        # Bounded self-intersection validation rejects duplicate support even with different IDs.
        for start in range(0, len(frame), 1024):
            pairs = self.tree.query(self.geometry[start:start + 1024], predicate="intersects")
            a, b = pairs[0] + start, pairs[1]
            mask = a < b
            a, b = a[mask], b[mask]
            if allow_quarantined_cross_border:
                # Both whole geographies are unknown and contribute no population.
                exempt = quarantined[a] & quarantined[b] & np.isnan(self.population[a]) & np.isnan(self.population[b]) & (countries[a] != countries[b])
                a, b = a[~exempt], b[~exempt]
            if len(a):
                interior = shapely.relate_pattern(self.geometry[a], self.geometry[b], "2********")
                a, b = a[interior], b[interior]
                overlap = shapely.area(shapely.intersection(self.geometry[a], self.geometry[b]))
                bad = np.flatnonzero(overlap > overlap_tolerance_m2)
                if len(bad):
                    i = bad[0]
                    raise ValueError(f"Overlapping source support: {self.ids[a[i]]}, {self.ids[b[i]]} "
                                     f"({overlap[i]:.3f} m2); reconcile inputs before allocation")
        self.known_tree = STRtree(self.geometry[np.isfinite(self.population)])
        self.unknown = np.flatnonzero(np.isnan(self.population))
        self.unknown_tree = STRtree(self.geometry[self.unknown])
        positive = np.flatnonzero(np.isfinite(self.population) & (self.population > 0))
        # Group by centroid only for indexing. Actual full envelopes control containment.
        centroids = shapely.centroid(self.geometry[positive])
        keys = np.column_stack((np.floor(shapely.get_x(centroids) / group_size_m),
                                np.floor(shapely.get_y(centroids) / group_size_m))).astype(np.int64)
        groups = {}
        source_bounds = shapely.bounds(self.geometry[positive])
        for i, key, bounds in zip(positive, map(tuple, keys), source_bounds):
            # Keep a very large source from widening an otherwise local group
            # envelope and forcing all its small neighbors into boundary clipping.
            oversized = max(bounds[2]-bounds[0], bounds[3]-bounds[1]) > group_size_m
            group_key = (*key, 1, str(self.ids[i])) if oversized else (*key, 0, "")
            groups.setdefault(group_key, []).append(i)
        self.members = [np.array(groups[key]) for key in sorted(groups)]
        self.envelopes = []
        for members in self.members:
            bounds = shapely.bounds(self.geometry[members])
            self.envelopes.append(box(bounds[:, 0].min(), bounds[:, 1].min(),
                                      bounds[:, 2].max(), bounds[:, 3].max()))
        self.envelopes = np.array(self.envelopes, dtype=object)
        self.group_tree = STRtree(self.envelopes)
        self.totals = np.array([math.fsum(self.population[indices]) for indices in self.members])
        # Materialize any lazy packed-tree structures before concurrent readers.
        for tree in (self.known_tree, self.unknown_tree, self.group_tree):
            if len(tree.geometries):
                tree.query(tree.geometries[0])

    def candidates(self, maximum):
        return self.group_tree.query(maximum, predicate="intersects")

    def total(self, area, *, candidates=None, reference=False):
        if reference:
            indices = self.tree.query(area, predicate="intersects")
            indices = indices[np.isfinite(self.population[indices]) & (self.population[indices] > 0)]
            return self._contributions(area, indices)
        if candidates is None:
            candidates = self.candidates(area)
        bounds = self.envelopes[candidates]
        intersects = shapely.intersects(area, bounds)
        candidates, bounds = candidates[intersects], bounds[intersects]
        contained = shapely.covers(area, bounds)
        full = math.fsum(self.totals[candidates[contained]])
        boundary = candidates[~contained]
        if not len(boundary):
            return full
        indices = np.concatenate([self.members[i] for i in boundary])
        return full + self._contributions(area, indices)

    def _contributions(self, area, indices):
        if not len(indices):
            return 0.0
        geometries = self.geometry[indices]
        hits = shapely.intersects(area, geometries)
        indices, geometries = indices[hits], geometries[hits]
        contained = shapely.covers(area, geometries)
        values = self.population[indices].copy()
        boundary = ~contained
        if boundary.any():
            fraction = shapely.area(shapely.intersection(area, geometries[boundary])) / self.areas[indices[boundary]]
            values[boundary] = self.population[indices[boundary]] * np.clip(fraction, 0, 1)
        return math.fsum(values)

    def unknown_sources(self, area, tolerance_m2):
        candidates = self.unknown_tree.query(area, predicate="intersects")
        if not len(candidates):
            return []
        indices = self.unknown[candidates]
        hit = shapely.area(shapely.intersection(area, self.geometry[indices])) > tolerance_m2
        return self.ids[indices[hit]].tolist()


class CoverageIndex:
    def __init__(self, coverage, extent, tolerance_m2, cache_path=None):
        # A prepared union answers full support directly, without repeatedly clipping
        # a detailed coastline for every target and radius. Any uncovered area stays
        # incomplete; no geographic percentage or tolerance-based zero is inferred.
        path = Path(cache_path) if cache_path is not None else None
        companion = path.with_suffix('.json') if path is not None else None
        self.cache_hit = bool(path is not None and path.exists() and companion.exists())
        if self.cache_hit:
            self._wkb = path.read_bytes()
            if hashlib.sha256(self._wkb).hexdigest() != json.loads(companion.read_text())["sha256"]:
                raise ValueError("Coverage geometry cache checksum mismatch")
            self.coverage = shapely.from_wkb(self._wkb)
        else:
            self.coverage = shapely.union_all(coverage.geometry.to_numpy()) if coverage is not None and len(coverage) else shapely.GeometryCollection()
            self._wkb = self.coverage.wkb
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.parent / (uuid.uuid4().hex + '.wkb.tmp')
                temporary.write_bytes(self._wkb)
                temporary.replace(path)
                companion.write_text(json.dumps({"sha256":hashlib.sha256(self._wkb).hexdigest()}))
        self._thread = threading.local()

    def complete(self, area):
        # GEOS prepared predicates build lazy internal indexes. Keep each prepared
        # object in its querying thread; sharing the immutable source WKB is safe.
        if not hasattr(self._thread, "coverage"):
            self._thread.coverage = shapely.from_wkb(self._wkb)
            shapely.prepare(self._thread.coverage)
        return bool(shapely.covers(self._thread.coverage, area))
