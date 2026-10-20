"""Qualified retained Human locations and source geometry, independent of applications."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import shutil

import geopandas as gpd
import h3
import pandas as pd
import pyarrow.parquet as pq
from shapely.geometry import Point, mapping
from shapely import make_valid

SCHEMA = 'human.native_features.v1'
FILE = 'human-native-features.geojson'
GROUPS = {
    'launches': {'name': 'Boat Launch Locations', 'family': 'Boat Launches', 'geometry': 'Point'},
    'shore_access': {'name': 'Shore Access Sites', 'family': 'Shore Access', 'geometry': 'Point'},
    'places': {'name': 'Named Destinations', 'family': 'Places', 'geometry': 'Point'},
    'access_lines': {'name': 'Shoreline Access Classifications', 'family': 'Shore Access', 'geometry': 'LineString / MultiLineString'},
}
LIMITS = ('Retained source records, not a complete regional inventory or guaranteed unique physical sites. '
          'Mapped facilities and destinations do not establish current public access, operation or use. '
          'Missing attributes remain unknown. Representative locations are not surveyed entrances. '
          'No visitation, observer effort or historical activity is inferred.')


from human.utils.artifacts import sha256_file as checksum


def clean(value):
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(v) for v in value]
    if value is None or (not isinstance(value, (bytes, str)) and pd.isna(value)): return None
    if hasattr(value, 'item'): value = value.item()
    if hasattr(value, 'isoformat'): return value.isoformat()
    if isinstance(value, bytes): return value.hex()
    return value


def bound_input(entry, key, receipts):
    path = Path(entry[key]).resolve()
    manifest_path = Path(entry['manifest']).resolve()
    manifest = json.loads(manifest_path.read_text())
    digest = checksum(path)
    matches = [a for a in [*manifest.get('artifacts', []), *manifest.get('inputs', [])]
               if Path(a['path']).name == path.name and a['sha256'] == digest]
    if len(matches) != 1: raise ValueError(f'Input lacks a unique checksum-bound source receipt: {path}')
    receipts.append({'path': str(path), 'sha256': digest, 'source_manifest': str(manifest_path),
        'source_manifest_sha256': checksum(manifest_path), 'dataset_id': matches[0].get('dataset_id'),
        'source_build_time_utc': manifest.get('build_time_utc'), 'source_bounds_wgs84': manifest.get('spatial_bounds_wgs84'),
        'configured_source_completeness': manifest.get('source_completeness'),
        'licenses': manifest.get('licenses'), 'attribution': manifest.get('attribution'),
        'known_limitations': manifest.get('known_limitations'), 'sources': manifest.get('sources')})
    return path


def read_geometry(path, *, allow_invalid=False):
    frame = gpd.read_parquet(path)
    if frame.crs is None: raise ValueError('Native geometry requires an explicit CRS')
    frame = frame.to_crs(4326)
    if frame.geometry.isna().any() or frame.geometry.is_empty.any() or (not allow_invalid and not frame.geometry.is_valid.all()):
        raise ValueError('Native source contains missing, empty or invalid geometry')
    return frame


def feature(group, identity, name, category, geometry, attributes, *, method='source_point', role='record', record_id=None):
    identity = f'{group}:{identity}'
    label = geometry if geometry.geom_type == 'Point' else geometry.representative_point()
    return {'type': 'Feature', 'id': identity, 'geometry': mapping(geometry), 'properties': {
        'record_id': record_id or identity, 'group': group, 'role': role, 'name': clean(name),
        'category': clean(category) or 'unclassified',
        'location_method': method, 'label_point': [label.x, label.y],
        'h3_index': attributes.get('H3_INDEX') or h3.latlng_to_cell(label.y, label.x, 7),
        'attributes': clean(attributes)}}


def compose_native_features(specification):
    spec = json.loads(Path(specification).read_text()) if not isinstance(specification, dict) else copy.deepcopy(specification)
    if spec.get('schema') != 'human.native_sources.v1': raise ValueError('Unsupported native source specification')
    receipts, features = [], []
    for group in ('launches', 'shore_access'):
        frame = read_geometry(bound_input(spec[group], 'facilities', receipts))
        if frame.ACCESS_SITE_ID.isna().any() or frame.ACCESS_SITE_ID.duplicated().any(): raise ValueError('Duplicate/null facility identities')
        for _, row in frame.iterrows():
            attrs = row.drop(labels=['geometry', 'REPRESENTED_GEOMETRY_WKB'], errors='ignore').to_dict()
            source_id = str(row.SOURCE_RECORD_ID)
            method = 'source_point' if not source_id.startswith(('way/', 'relation/')) else 'source_representative_point_not_entrance'
            if row.geometry.geom_type != 'Point': method = 'source_representative_point_not_entrance'
            category = 'boat_launch' if group == 'launches' else row.get('FACILITY_TYPE')
            features.append(feature(group, row.ACCESS_SITE_ID, row.get('NAME'), category, row.geometry, attrs,
                method=method))
    places_path = bound_input(spec['places'], 'catalog', receipts)
    places = json.loads(places_path.read_text())
    if not isinstance(places, list) or len({str(p['id']) for p in places}) != len(places): raise ValueError('Invalid/duplicate places catalog identities')
    parks = read_geometry(bound_input(spec['places'], 'park_polygons', receipts), allow_invalid=True) if spec['places'].get('park_polygons') else None
    park_lookup = {str(row.FID): row for _, row in parks.iterrows()} if parks is not None else {}
    for place in places:
        ids = list(dict.fromkeys([place['id'], *place.get('source_ids', [])]))
        polygons = [(identity, park_lookup[identity.split(':', 1)[1]]) for identity in ids
                    if identity.startswith('wa-state-parks:') and identity.split(':', 1)[1] in park_lookup]
        method = 'marine_facing_representative_point_not_entrance' if polygons else 'retained_catalog_point_not_verified_entrance'
        point = Point(float(place['longitude']), float(place['latitude']))
        if not (-180 <= point.x <= 180 and -90 <= point.y <= 90): raise ValueError('Invalid place coordinates')
        record = feature('places', place['id'], place.get('name'), place.get('destination_type'), point, place,
            method=method)
        features.append(record)
        for identity, row in polygons:
            display_geometry = row.geometry if row.geometry.is_valid else make_valid(row.geometry)
            area = feature('places', place['id'] + '/source-area/' + identity, place.get('name'), place.get('destination_type'), display_geometry,
                row.drop(labels=['geometry']).to_dict(), role='source_footprint', record_id=record['properties']['record_id'],
                method='original_source_polygon_not_access_boundary')
            area['properties']['source_geometry_valid'] = bool(row.geometry.is_valid)
            area['properties']['source_geometry_wkb_sha256'] = hashlib.sha256(row.geometry.wkb).hexdigest()
            if not row.geometry.is_valid:
                area['properties']['location_method'] = 'source_polygon_make_valid_for_display_not_access_boundary'
            features.append(area)
    if spec['shore_access'].get('access_lines'):
        frame = read_geometry(bound_input(spec['shore_access'], 'access_lines', receipts))
        if frame.OBJECTID.duplicated().any(): raise ValueError('Duplicate native access line IDs')
        for _, row in frame.iterrows():
            attrs = row.drop(labels=['geometry']).to_dict()
            features.append(feature('access_lines', row.OBJECTID, row.get('BEACH_NAME'), row.get('CLASS_DESC'), row.geometry,
                attrs, method='original_source_line_label_point_not_entrance'))
    if len({f['id'] for f in features}) != len(features): raise ValueError('Duplicate native feature identities')
    for receipt in receipts:
        if checksum(receipt['path']) != receipt['sha256']: raise ValueError('Source changed during native export')
    groups = {}
    for group, info in GROUPS.items():
        records = [f for f in features if f['properties']['group'] == group and f['properties']['role'] == 'record']
        if not records: continue
        groups[group] = {**info, 'records': len(records), 'context_features': sum(f['properties']['group'] == group and f['properties']['role'] == 'source_footprint' for f in features), 'categories': dict(sorted(Counter(str(f['properties']['category']) for f in records).items())),
            'limitations': LIMITS, 'regional_completeness': 'partial'}
    payload = {'type': 'FeatureCollection', 'schema': SCHEMA, 'features': features}
    metadata = {'schema': SCHEMA, 'crs': 'EPSG:4326', 'primary_key': 'feature.id', 'groups': groups,
        'source_receipts': receipts, 'limitations': [LIMITS,
            'Original park polygons are source footprints, not legal access boundaries; marine-facing point markers are not entrances.',
            'WA access line classifications include restricted, conditional, watercraft-only and unknown access; source text remains authoritative.',
            'Legacy all-class public-access length/fraction summaries are withheld from selection until their numerator is qualified.'],
        'source_footprint_display_repairs': [f['properties']['record_id'] for f in features if f['properties'].get('source_geometry_valid') is False],
        'model_eligible': False, 'source_specification': spec}
    return payload, metadata


def summary_fields(columns):
    """Only interpretable retained summaries; do not relabel unqualified length as public."""
    definitions = {
      'boat_launch_access__BOAT_LAUNCH_COUNT': ('Boat Launch Source Records', 'Boat Launches', 'records'),
      'boat_launch_access__VERIFIED_PUBLIC_BOAT_LAUNCH_COUNT': ('Source-Labeled Public Launch Records', 'Boat Launches', 'records'),
      'boat_launch_access__BOAT_LAUNCHES_WITH_KNOWN_RAMP_CAPABILITY': ('Launch Records with Known Ramp Capability', 'Boat Launches', 'records'),
      'boat_launch_access__BOAT_LAUNCHES_WITH_KNOWN_SEASONAL_OPERATION': ('Launch Records with Known Seasonal Operation', 'Boat Launches', 'records'),
      'public_shore_access__TOTAL_MARINE_SHORELINE_M': ('Mapped Marine Shoreline Length', 'Shore Access', 'm'),
      'public_shore_access__PUBLIC_ACCESS_SITE_COUNT': ('Shore Access Source Records', 'Shore Access', 'records'),
      'public_shore_access__VERIFIED_PUBLIC_ACCESS_SITE_COUNT': ('Authoritative Public Access Records', 'Shore Access', 'records'),
      'public_shore_access__OSM_EXPLICIT_PUBLIC_ACCESS_SITE_COUNT': ('OSM Explicit Public Access Records', 'Shore Access', 'records'),
      'public_shore_access__MAPPED_ACCESS_UNKNOWN_SITE_COUNT': ('Access-Unknown Site Records', 'Shore Access', 'records'),
      'public_shore_access__RESTRICTED_ACCESS_SITE_COUNT': ('Restricted Access Site Records', 'Shore Access', 'records'),
      'public_shore_access__CONDITIONAL_ACCESS_SITE_COUNT': ('Conditional Access Site Records', 'Shore Access', 'records'),
      'public_shore_access__PUBLIC_ACCESS_EVIDENCE_STATE': ('Shore Access Evidence', 'Shore Access', 'category'),
      'places__CATALOG_RECORD_COUNT': ('Named Destination Records', 'Places', 'records'),
    }
    return {field: {'label': label, 'family': family, 'units': unit,
        'qualifiers': [c for c in columns if c.startswith(field.split('__')[0] + '__') and c != 'public_shore_access__PUBLIC_ACCESS_STATE' and
            any(word in c for word in ('STATUS', 'STATE', 'COVERAGE', 'QC', 'SOURCE_DATASET_COUNT'))],
        'limitation': LIMITS + ' H3 summaries cover only the shared reporting grid. Missing cells are unknown, not zero facilities.'}
        for field, (label, family, unit) in definitions.items() if field in columns}


def export_native_features(specification, output_dir):
    output = Path(output_dir);output.mkdir(parents=True, exist_ok=True)
    path = output / FILE
    if path.exists() or path.with_suffix('.metadata.json').exists(): raise FileExistsError(path)
    payload, metadata = compose_native_features(specification)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':')))
    metadata['artifact'] = {'path': FILE, 'sha256': checksum(path), 'bytes': path.stat().st_size,
        'rows': sum(g['records'] for g in metadata['groups'].values()), 'features': len(payload['features']),
        'context_features': sum(g['context_features'] for g in metadata['groups'].values())}
    path.with_suffix('.metadata.json').write_text(json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False))
    return metadata


def _extend_static_delivery(input_dir, specification, output_dir):
    """Extend an existing verified current delivery without touching population values."""
    source, output = Path(input_dir), Path(output_dir)
    if output.exists() and any(output.iterdir()): raise FileExistsError('Use an empty staging directory')
    catalog = json.loads((source/'catalog.json').read_text());name = 'human-r7-static.parquet'
    old = json.loads((source/'human-r7-static.metadata.json').read_text())
    table = pq.read_table(source/name)
    if old['artifact']['sha256'] != checksum(source/name) or old not in catalog['products']:
        raise ValueError('Static source binding mismatch')
    native = export_native_features(specification, output)
    metadata = {k: copy.deepcopy(v) for k, v in old.items() if k != 'artifact'}
    metadata['infrastructure_fields'] = summary_fields(table.column_names)
    metadata['native_features'] = native['artifact']
    schema_meta = dict(table.schema.metadata or {})
    schema_meta[b'human_study'] = json.dumps(metadata, sort_keys=True).encode()
    pq.write_table(table.replace_schema_metadata(schema_meta), output/name, compression='zstd')
    metadata['artifact'] = {**old['artifact'], 'sha256': checksum(output/name)}
    (output/'human-r7-static.metadata.json').write_text(json.dumps(metadata, indent=2, sort_keys=True))
    assert pq.read_table(output/name).equals(table, check_metadata=False)
    catalog['products'] = [metadata if product == old else product for product in catalog['products'] if product.get('schema') != SCHEMA]
    catalog['products'].append(native)
    for product in catalog['products']:
        path = product['artifact']['path']
        if path not in (name, FILE):
            shutil.copy2(source/path, output/path)
            companion = (source/path).with_suffix('.metadata.json')
            if companion.exists(): shutil.copy2(companion, output/companion.name)
    (output/'catalog.json').write_text(json.dumps(catalog, indent=2, sort_keys=True))
    if checksum(source/name) != old['artifact']['sha256']: raise ValueError('Current source changed during extension')
    return output/'catalog.json'


def extend_static_delivery(input_dir, specification, output_dir, *, metadata_dir=None):
    """Extend legacy or final-only input and publish only final Parquets."""
    import tempfile
    from human.pipelines.delivery.finalize import finalize_delivery, materialize_legacy
    output=Path(output_dir)
    if output.exists() and any(output.iterdir()): raise FileExistsError('Use an empty final delivery directory')
    identity=hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:16]
    receipts=Path(metadata_dir) if metadata_dir else Path.cwd()/'data/delivery-metadata'/identity
    receipts.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='human-native-',dir=receipts.parent) as temporary:
        legacy,staging=Path(temporary)/'legacy',Path(temporary)/'extended'
        materialize_legacy(input_dir,legacy)
        _extend_static_delivery(legacy,specification,staging)
        return finalize_delivery(staging,output,receipts)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-specification',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--extend-static',type=Path)
    parser.add_argument('--metadata-dir',type=Path,help='Toolkit-managed receipts outside final data')
    args=parser.parse_args()
    result=extend_static_delivery(args.extend_static,args.source_specification,args.output_dir,metadata_dir=args.metadata_dir) if args.extend_static else export_native_features(args.source_specification,args.output_dir)
    print(result if isinstance(result,Path) else json.dumps(result['artifact']))


if __name__=='__main__':main()
