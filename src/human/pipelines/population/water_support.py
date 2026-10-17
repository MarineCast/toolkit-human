"""Qualify census-free water gaps using pinned shared-study physical context.

Only geographic support changes: published census allocations and unknown-source
intersections retain their original meaning. No mask is buffered or extrapolated.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import Transformer
import shapely
from shapely.ops import transform

from human.pipelines.population.static.geometry import cell_polygon, radius_polygon
from human.pipelines.population.static.sources import checksum


def load_qualified_water(study):
    """Return checksum-pinned water minus pinned physical land, or no assertion."""
    config_path = Path(study['identity']['config_path'])
    registry = study['config']['grid_registry']
    manifest_path = (config_path.parent / registry['manifest_relative_path']).resolve()
    if checksum(manifest_path) != registry['manifest_sha256']:
        raise ValueError('Registry manifest changed before water qualification')
    manifest = json.loads(manifest_path.read_text())
    context = manifest.get('physical_context')
    if not context:
        return None
    frames, provenance = {}, {}
    for name in ('water', 'land'):
        entry = context[name + '_polygon_path']
        path = (config_path.parent / entry['relative_path']).resolve()
        if not path.is_relative_to(study['data_root']) or checksum(path) != entry['sha256']:
            raise ValueError('Physical context path/checksum mismatch: ' + name)
        frame = gpd.read_parquet(path)
        if (frame.crs is None or frame.empty or frame.geometry.isna().any()
                or not frame.geometry.is_valid.all()
                or not frame.geometry.geom_type.isin(['Polygon', 'MultiPolygon']).all()):
            raise ValueError('Invalid physical context: ' + name)
        if name == 'water' and not frame.SOURCE_SCALE_QUALIFICATION.eq('static_cartographic_reference').all():
            raise ValueError('Unqualified water reference')
        frames[name] = frame.to_crs(4326)
        provenance[name] = {'path': str(path), 'sha256': entry['sha256']}
    water = shapely.difference(shapely.union_all(frames['water'].geometry.values),
                               shapely.union_all(frames['land'].geometry.values))
    if water.is_empty or not water.is_valid:
        raise ValueError('No valid qualified water support')
    provenance.update({'registry_manifest_sha256': registry['manifest_sha256'],
        'assumption': 'Census-free portions of pinned physical water outside pinned physical land assert zero resident-population support at the qualified static cartographic scale. Census polygon allocations and unknown counts are never overridden.',
        'extent_policy': 'Exact supplied water only; no buffer, extrapolation or inferred land zero.'})
    return gpd.GeoDataFrame(geometry=[water], crs=4326), provenance


def qualify_water_support(table, metadata, *, census_coverage, water, provenance, progress=None):
    """Recheck geographic completeness while reusing unchanged allocated subtotals."""
    frame, meta = table.copy(deep=True), copy.deepcopy(metadata)
    upstream_cache = {key:meta.pop(key) for key in ('cache_key','cache_sha256','benchmark') if key in meta}
    if upstream_cache:
        meta['water_support_upstream_computation'] = upstream_cache
    if meta.get('include_bands'):
        raise ValueError('Static water qualification requires cumulative outputs without bands')
    coverage = shapely.union_all([census_coverage, water.to_crs(meta['area_crs']).geometry.iloc[0]])
    shapely.prepare(coverage)
    project = Transformer.from_crs(4326, meta['area_crs'], always_xy=True).transform
    coords = [h3_center(c) for c in frame.h3_index]
    metrics = [('population_count', None), *[(f'population_within_{r:g}km', r) for r in meta['radii_km']]]
    parameters = meta['method_parameters']
    changes = {}
    for metric, radius in metrics:
        candidates = np.flatnonzero(frame[metric+'__unverified_geographic_support'].to_numpy())
        improved = []
        for start in range(0, len(candidates), 256):
            positions = candidates[start:start+256]
            supports = [transform(project, cell_polygon(frame.h3_index.iloc[i], parameters['edge_step_m'])
                if radius is None else radius_polygon(*coords[i], radius, parameters['buffer_tolerance_m'],
                    vertices=parameters.get('aligned_radius_vertices'))) for i in positions]
            improved.extend(positions[np.asarray(shapely.covers(coverage, supports))].tolist())
        index = frame.index[improved]
        frame.loc[index,metric+'__unverified_geographic_support'] = False
        no_unknown = frame.loc[index,metric+'__missing_sources'].map(json.loads).map(len).eq(0)
        complete_index = index[no_unknown]
        subtotal = frame.loc[complete_index,metric+'__available_source_subtotal'].fillna(0.)
        frame.loc[complete_index,metric] = subtotal
        frame.loc[complete_index,metric+'__available_source_subtotal'] = subtotal
        frame.loc[complete_index,metric+'__coverage_status'] = np.where(subtotal.eq(0), 'known_zero', 'complete')
        changes[metric] = {'geography_requalified_rows': len(improved), 'new_complete_rows': len(complete_index)}
        if progress:
            progress(metric, changes[metric])
    frame['population_density_per_km2'] = frame.population_count / frame.cell_area_km2
    report = meta['coverage_report']
    report['counts'] = {metric:{str(k):int(v) for k,v in frame[metric+'__coverage_status'].value_counts().items()} for metric,_ in metrics}
    report['incomplete_metrics'] = sum(int(frame[metric].isna().sum()) for metric,_ in metrics)
    report['examples'] = [{'h3_index':row.h3_index,'metric':metric,
        'status':getattr(row,metric+'__coverage_status'),
        'missing_sources':json.loads(getattr(row,metric+'__missing_sources')),
        'unverified_geographic_support':bool(getattr(row,metric+'__unverified_geographic_support'))}
        for metric,_ in metrics for row in frame[frame[metric].isna()].head(5).itertuples()]
    meta['verified_water_support'] = {**provenance, 'method':'qualified_water_gap_support.v1', 'changes':changes}
    return frame, meta


def h3_center(cell):
    import h3
    return h3.cell_to_latlng(cell)
