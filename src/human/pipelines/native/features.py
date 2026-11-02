"""Qualified retained Human locations and source geometry, independent of applications."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
from pathlib import Path

import geopandas as gpd
import h3
import pandas as pd
from shapely.geometry import Point, mapping
from shapely import make_valid

SCHEMA = 'human.native_features.v1'
from human.pipelines.native.ports import GROUP as PORT_GROUP, LIMITS as PORT_LIMITS

GROUPS = {
    'ports': PORT_GROUP,
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
    if spec.get('ports'):
        from human.pipelines.native.ports import port_features
        ports, receipt = port_features(spec['ports'])
        features.extend(ports); receipts.append(receipt)
    if len({f['id'] for f in features}) != len(features): raise ValueError('Duplicate native feature identities')
    for receipt in receipts:
        if checksum(receipt['path']) != receipt['sha256']: raise ValueError('Source changed during native export')
    groups = {}
    for group, info in GROUPS.items():
        records = [f for f in features if f['properties']['group'] == group and f['properties']['role'] == 'record']
        if not records: continue
        groups[group] = {**info, 'records': len(records), 'context_features': sum(f['properties']['group'] == group and f['properties']['role'] == 'source_footprint' for f in features), 'categories': dict(sorted(Counter(str(f['properties']['category']) for f in records).items())),
            'limitations': PORT_LIMITS if group == 'ports' else LIMITS, 'regional_completeness': 'partial'}
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
    from human.pipelines.delivery.names import STATIC_NAMES_V4 as STATIC_NAMES
    inverse = {value:key for key,value in STATIC_NAMES.items()}
    public_names = any(name in inverse and name != inverse[name] for name in columns)
    if public_names:
        original = [inverse.get(name,name) for name in columns]
        return {STATIC_NAMES.get(name,name): {**info, 'qualifiers':[STATIC_NAMES.get(q,q) for q in info['qualifiers']]}
            for name,info in summary_fields(original).items()}
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
    definitions['ports__SOURCE_RECORD_COUNT'] = ('Port Source Records', 'Ports', 'records')
    result = {field: {'label': label, 'family': family, 'units': unit,
        'qualifiers': [c for c in columns if c.startswith(field.split('__')[0] + '__') and c != 'public_shore_access__PUBLIC_ACCESS_STATE' and
            any(word in c for word in ('STATUS', 'STATE', 'COVERAGE', 'QC', 'SOURCE_DATASET_COUNT'))],
        'limitation': LIMITS + ' H3 summaries cover only the shared reporting grid. Missing cells are unknown, not zero facilities.'}
        for field, (label, family, unit) in definitions.items() if field in columns}
    if 'ports__SOURCE_RECORD_COUNT' in result:
        result['ports__SOURCE_RECORD_COUNT']['limitation'] = PORT_LIMITS
    return result


def delivery_fields():
    """Reviewed totals and port presence with explicit unknown-as-zero encoding."""
    from human.pipelines.delivery.names import STATIC_NAMES, TRANSFORMED_SOURCE_COLUMNS, PRESENCE_FROM_COUNTS
    source = summary_fields(list(TRANSFORMED_SOURCE_COLUMNS))
    labels = {'boat_launch_count':'Boat Launches', 'shore_access_site_count':'Shore Access Sites',
        'place_record_count':'Named Destination Records', 'has_port':'Port Present'}
    result = {}
    for raw, public in STATIC_NAMES.items():
        if raw not in TRANSFORMED_SOURCE_COLUMNS: continue
        info = {**source[raw], 'label':labels[public], 'qualifiers':[],
            'units':'0/1' if public=='has_port' else 'records',
            'source_count_field':raw, 'null_fill_value':0,
            'null_fill_policy':'unknown_as_zero_user_review_20261024',
            'limitation':source[raw]['limitation']+' Public counts encode missing source support as zero by user instruction; originals and null masks remain in the external audit. Zero does not establish physical absence.'}
        if public=='has_port':
            info['derivation']='int64(fill_null(source_count, 0) > 0)'
            info['measurement_type']='binary_presence'
        else: info['derivation']='fill_null(source_count, 0), checked nonnegative integral int64'
        if public=='shore_access_site_count':
            info['limitation']+=' Uses the existing all-site total, including unknown, public, conditional and restricted records; category subtotals are not added again.'
        result[public]=info
    labels={'has_boat_launch':'Boat Launch Present','has_shore_access_site':'Shore Access Site Present','has_place':'Place Present'}
    for presence,count in PRESENCE_FROM_COUNTS.items():
        result[presence]={**result[count], 'label':labels[presence], 'units':'0/1',
            'derived_from':count, 'derivation':'int64('+count+' > 0)', 'measurement_type':'binary_presence'}
    return result
