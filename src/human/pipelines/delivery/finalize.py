"""Final-only Human delivery: self-describing Parquets, external working receipts."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil

import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS
from shapely.geometry import shape

SCHEMA = 'human.delivery.final_only.v1'
STATIC = 'static.parquet'
CALENDAR = 'dynamic-calendar.parquet'
NATIVE = 'static-native.parquet'


from human.utils.artifacts import sha256_file as digest


def embedded(path):
    return json.loads(pq.read_schema(path).metadata[b'human_study'])


def native_payload(path):
    """Reconstruct the exact source JSON geometry/properties without a GIS runtime."""
    path = Path(path)
    if path.suffix == '.geojson':
        return json.loads(path.read_text())
    table = pq.read_table(path, columns=['feature_id', 'geometry_json', 'properties_json'])
    features = [{'type': 'Feature', 'id': row['feature_id'],
        'geometry': json.loads(row['geometry_json']), 'properties': json.loads(row['properties_json'])}
        for row in table.to_pylist()]
    return {'type': 'FeatureCollection', 'schema': 'human.native_features.v1', 'features': features}


def read_catalog(root):
    """Read either the historical receipt layout or embedded final-only contracts."""
    root = Path(root)
    if (root/'catalog.json').exists():
        catalog = json.loads((root/'catalog.json').read_text())
    else:
        metadata = pq.read_schema(root/STATIC).metadata
        delivery = json.loads(metadata[b'human_delivery'])
        if delivery['schema'] != SCHEMA:
            raise ValueError('Unsupported final-only Human delivery')
        catalog = json.loads(Path(delivery['receipt_path']).read_text())
        for product in catalog['products']:
            path=root/product['artifact']['path']
            if digest(path)!=product['artifact']['sha256']:
                raise ValueError(f'Human product checksum mismatch: {path}')
            info = embedded(path)
            if info != {key:value for key,value in product.items() if key!='artifact'}:
                raise ValueError('Embedded Human metadata differs from its receipt')
        if {p.name for p in root.iterdir() if p.name != '.DS_Store'} != {p['artifact']['path'] for p in catalog['products']}:
            raise ValueError('Final Human directory contains non-product files')
    for product in catalog['products']:
        artifact = product['artifact']
        if Path(artifact['path']).name != artifact['path']:
            raise ValueError('Human artifact paths must be plain filenames')
        path = root/artifact['path']
        if not path.is_file() or digest(path) != artifact['sha256']:
            raise ValueError(f'Human product checksum mismatch: {path}')
    return catalog


def _native_table(payload, metadata):
    rows = []
    for feature in payload['features']:
        properties = feature['properties']
        rows.append({'feature_id': str(feature['id']),
            **{key: properties.get(key) for key in ('record_id','group','role','name','category','location_method','h3_index')},
            'label_point': properties.get('label_point'),
            'geometry': shape(feature['geometry']).wkb,
            'geometry_json': json.dumps(feature['geometry'], ensure_ascii=False, separators=(',', ':'), allow_nan=False),
            'properties_json': json.dumps(properties, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)})
    if len({row['feature_id'] for row in rows}) != len(rows):
        raise ValueError('Duplicate native feature IDs')
    table = pa.Table.from_pylist(rows)
    geo = {'version': '1.1.0', 'primary_column': 'geometry', 'columns': {'geometry': {
        'encoding': 'WKB', 'geometry_types': sorted({shape(feature['geometry']).geom_type for feature in payload['features']}),
        'crs': CRS.from_epsg(4326).to_json_dict()}}}
    metadata.update(primary_key=['feature_id'], storage={'format':'GeoParquet', 'geometry_column':'geometry',
        'exact_geometry_column':'geometry_json', 'exact_properties_column':'properties_json'},
        storage_counts={'features':len(rows), 'records':sum(row['role']=='record' for row in rows),
            'context_features':sum(row['role']=='source_footprint' for row in rows)})
    return table.replace_schema_metadata({b'geo':json.dumps(geo,sort_keys=True).encode()})


def _relocate(value, relocations):
    if isinstance(value, dict): return {key:_relocate(item,relocations) for key,item in value.items()}
    if isinstance(value, list): return [_relocate(item,relocations) for item in value]
    if isinstance(value, str):
        for before,after in sorted(relocations.items(), key=lambda item:-len(item[0])):
            if value == before or value.startswith(before+'/'): return after+value[len(before):]
    return value


def finalize_delivery(source, output, metadata_dir, *, relocations=None, final_data_root=None):
    """Repackage verified products without changing values, identity or native support.

    Source is read-only. The target must be empty; source caches/legacy files are
    never copied. Receipts are written outside the public final-data directory.
    """
    source, output, metadata_dir = Path(source), Path(output), Path(metadata_dir)
    if output.exists() and any(output.iterdir()): raise FileExistsError('Use an empty final delivery directory')
    if metadata_dir.resolve().is_relative_to(output.resolve()):
        raise ValueError('Delivery receipts must be outside the final data directory')
    if metadata_dir.exists() and any(metadata_dir.iterdir()):
        raise FileExistsError('Use a new immutable receipt directory')
    if metadata_dir.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(metadata_dir.resolve()):
        raise ValueError('Source and receipt directories must be disjoint')
    catalog = read_catalog(source)
    common = _relocate({key:copy.deepcopy(value) for key,value in catalog.items() if key!='products'}, relocations or {})
    delivery = {'schema': SCHEMA, 'catalog': common, 'receipt_path': str((metadata_dir/'catalog.json').resolve())}
    output.mkdir(parents=True,exist_ok=True)
    metadata_dir.mkdir(parents=True,exist_ok=True)
    products=[]; native=None
    ordered=sorted(catalog['products'],key=lambda p:p['schema']!='human.native_features.v1')
    for product in ordered:
        old_path = source/product['artifact']['path']
        info=_relocate({key:copy.deepcopy(value) for key,value in product.items() if key!='artifact'},relocations or {})
        if info['schema']=='human.native_features.v1':
            name=NATIVE; table=_native_table(native_payload(old_path),info)
        else:
            table=pq.read_table(old_path)
            if info['schema']=='human.study.static.v1': name=STATIC
            elif info['schema'] in ('human.study.temporal.v1','human.study.calendar_lookup.v1'): name=CALENDAR
            elif info['schema']=='human.study.dynamic.v1': name=f"dynamic-{info['family']}-r{info['h3_resolution']}.parquet"
            else: raise ValueError(f"Unsupported final Human product: {info['schema']}")
            if info['schema']=='human.study.static.v1' and native:
                info['native_features']=native['artifact']
        path=output/name
        if path.exists(): raise ValueError('Multiple products require the same final filename')
        schema_metadata={**(table.schema.metadata or {}),b'human_study':json.dumps(info,sort_keys=True).encode(),
            b'human_delivery':json.dumps(delivery,sort_keys=True).encode()}
        pq.write_table(table.replace_schema_metadata(schema_metadata),path,compression='zstd')
        if not table.equals(pq.read_table(path),check_metadata=False): raise ValueError('Final Parquet value parity failed')
        if info['schema']=='human.native_features.v1':
            original=json.loads(json.dumps(native_payload(old_path)))
            if native_payload(path)!=original: raise ValueError('Native geometry/properties round-trip failed')
        info['artifact']={**info.get('storage_counts',{}),'path':name,'sha256':digest(path),'rows':table.num_rows}
        (metadata_dir/Path(name).with_suffix('.metadata.json')).write_text(json.dumps(info,indent=2,sort_keys=True))
        products.append(info)
        if info['schema']=='human.native_features.v1': native=info
    result={**common,'delivery_schema':SCHEMA,'data_root':str(Path(final_data_root or output).resolve()),'products':products}
    catalog_path=metadata_dir/'catalog.json'
    catalog_path.write_text(json.dumps(result,indent=2,sort_keys=True))
    # Re-read all embedded bindings after writing the final files.
    current=read_catalog(output)
    static=next((p for p in current['products'] if p['schema']=='human.study.static.v1'),None)
    if static and native and static['native_features']!=native['artifact']: raise ValueError('Final native binding mismatch')
    return catalog_path


def materialize_legacy(source, output):
    """Temporary bridge for the historical native-extension implementation only."""
    source,output=Path(source),Path(output);output.mkdir(parents=True,exist_ok=True)
    catalog=read_catalog(source);products=[]
    for product in catalog['products']:
        info=copy.deepcopy(product);old=source/info['artifact']['path']
        if info['schema']=='human.native_features.v1':
            name='human-native-features.geojson'
            (output/name).write_text(json.dumps(native_payload(old),ensure_ascii=False,sort_keys=True,separators=(',',':')))
        elif info['schema']=='human.study.static.v1':name=f"human-r{info.get('h3_resolution',7)}-static.parquet";shutil.copy2(old,output/name)
        elif info['schema'] in ('human.study.temporal.v1','human.study.calendar_lookup.v1'):name='calendar-temporal.parquet';shutil.copy2(old,output/name)
        else:name=old.name;shutil.copy2(old,output/name)
        info['artifact']={**info['artifact'],'path':name,'sha256':digest(output/name)}
        (output/Path(name).with_suffix('.metadata.json')).write_text(json.dumps(info,indent=2,sort_keys=True))
        products.append(info)
    catalog['products']=products
    (output/'catalog.json').write_text(json.dumps(catalog,indent=2,sort_keys=True))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--metadata-dir',type=Path,required=True)
    args=parser.parse_args()
    print(finalize_delivery(args.source,args.output,args.metadata_dir))


if __name__=='__main__':main()
