"""Final-only Human delivery: self-describing Parquets, external working receipts."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil

import pyarrow as pa
import pyarrow.parquet as pq

from human.pipelines.delivery.names import COMPACT_SCHEMAS

SCHEMA = 'human.delivery.final_only.v7'
READABLE_SCHEMAS = {SCHEMA, 'human.delivery.final_only.v1', 'human.delivery.final_only.v2', 'human.delivery.final_only.v3', 'human.delivery.final_only.v4', 'human.delivery.final_only.v5', 'human.delivery.final_only.v6'}
REVIEW_FILES = {'.DS_Store', 'REVIEW_RESULTS.txt'}
STATIC = 'static.parquet'
CALENDAR = 'dynamic-calendar.parquet'


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
        if delivery['schema'] not in READABLE_SCHEMAS:
            raise ValueError('Unsupported final-only Human delivery')
        catalog = json.loads(Path(delivery['receipt_path']).read_text())
        for product in catalog['products']:
            path=root/product['artifact']['path']
            if digest(path)!=product['artifact']['sha256']:
                raise ValueError(f'Human product checksum mismatch: {path}')
            info = embedded(path)
            if info != {key:value for key,value in product.items() if key!='artifact'}:
                raise ValueError('Embedded Human metadata differs from its receipt')
        if {p.name for p in root.iterdir() if p.name not in REVIEW_FILES} != {p['artifact']['path'] for p in catalog['products']}:
            raise ValueError('Final Human directory contains non-product files')
    for product in catalog['products']:
        artifact = product['artifact']
        if Path(artifact['path']).name != artifact['path']:
            raise ValueError('Human artifact paths must be plain filenames')
        path = root/artifact['path']
        if not path.is_file() or digest(path) != artifact['sha256']:
            raise ValueError(f'Human product checksum mismatch: {path}')
    for product in catalog['products']:
        if product['schema'] in COMPACT_SCHEMAS:
            from human.pipelines.delivery.static import read_audit
            audit_path=Path(product['static_audit']['artifact']['path']).resolve()
            receipt_path=Path(json.loads(pq.read_schema(root/STATIC).metadata[b'human_delivery'])['receipt_path'])
            if audit_path.parent != receipt_path.resolve().parent or audit_path.is_relative_to(root.resolve()):
                raise ValueError('Human static audit must stay beside its external receipt')
            read_audit(pq.read_table(root/STATIC,columns=['h3_index']), product)
    return catalog


def _relocate(value, relocations):
    if isinstance(value, dict): return {key:_relocate(item,relocations) for key,item in value.items()}
    if isinstance(value, list): return [_relocate(item,relocations) for item in value]
    if isinstance(value, str):
        for before,after in sorted(relocations.items(), key=lambda item:-len(item[0])):
            if value == before or value.startswith(before+'/'): return after+value[len(before):]
    return value


def finalize_delivery(source, output, metadata_dir, *, relocations=None, final_data_root=None):
    """Publish static values and a compact calendar, excluding native geometry exports.

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
    common.update(delivery_schema=SCHEMA, data_root=str(Path(final_data_root or output).resolve()))
    if common.get('default_population'):
        default=common['default_population']
        default['field']='population_within_100km'
        default['audit_fields']={**default.get('audit_fields', {}),
            **{key:default.pop(key) for key in ('zero_imputation_flag','method_field') if key in default}}
        default.pop('coverage_status', None)
        default['audit_fields']['coverage_status']='population_within_100km__coverage_status'
        default['support_location']='external_static_audit'
        default['zero_imputation_rule']='coverage_status in [partial, unavailable]'
    delivery = {'schema': SCHEMA, 'catalog': common, 'receipt_path': str((metadata_dir/'catalog.json').resolve())}
    output.mkdir(parents=True,exist_ok=True)
    metadata_dir.mkdir(parents=True,exist_ok=True)
    products=[]
    ordered=[p for p in catalog['products'] if p['schema']!='human.native_features.v1']
    for product in ordered:
        old_path = source/product['artifact']['path']
        info=_relocate({key:copy.deepcopy(value) for key,value in product.items() if key!='artifact'},relocations or {})
        table=pq.read_table(old_path)
        if info['schema'] in ('human.study.static.v1', *COMPACT_SCHEMAS):
            name=STATIC
            info.pop('native_features', None)
            native_info=next((p for p in catalog['products'] if p['schema']=='human.native_features.v1'), None)
            if native_info:
                info['infrastructure_source_provenance']={k:copy.deepcopy(v) for k,v in native_info.items() if k!='artifact'}
                if info.get('ports'):
                    from human.pipelines.native.ports import summary_binding
                    native_table=pq.read_table(source/native_info['artifact']['path'], columns=['group','properties_json'])
                    ports=[{'properties': json.loads(row['properties_json'])} for row in native_table.to_pylist() if row['group']=='ports']
                    info['port_h3_by_source_id']=summary_binding(ports)
            from human.pipelines.delivery.static import compact_static
            if 'population_count_collected' in table.column_names or info['schema'] in COMPACT_SCHEMAS:
                table, info=compact_static(table, info, metadata_dir)
        elif info['schema']=='human.study.calendar_lookup.v1':
            from human.pipelines.calendar.lookup import compact_calendar, compact_metadata
            from human.pipelines.delivery.validation import product_contract
            import base64
            name=CALENDAR
            table=pa.Table.from_pandas(compact_calendar(table.to_pandas()), preserve_index=False)
            spec=product_contract()['products'][CALENDAR]
            schema=pa.ipc.read_schema(pa.BufferReader(base64.b64decode(spec['arrow_schema_base64'])))
            table=table.cast(schema)
            info=compact_metadata(info)
        elif info['schema'] in ('human.study.calendar_lookup.v2','human.study.temporal.v1'): name=CALENDAR
        elif info['schema']=='human.study.dynamic.v1': name=f"dynamic-{info['family']}-r{info['h3_resolution']}.parquet"
        else: raise ValueError(f"Unsupported final Human product: {info['schema']}")
        path=output/name
        if path.exists(): raise ValueError('Multiple products require the same final filename')
        schema_metadata={**(table.schema.metadata or {}),b'human_study':json.dumps(info,sort_keys=True).encode(),
            b'human_delivery':json.dumps(delivery,sort_keys=True).encode()}
        pq.write_table(table.replace_schema_metadata(schema_metadata),path,compression='zstd')
        if not table.equals(pq.read_table(path),check_metadata=False): raise ValueError('Final Parquet value parity failed')
        info['artifact']={**info.get('storage_counts',{}),'path':name,'sha256':digest(path),'rows':table.num_rows}
        (metadata_dir/Path(name).with_suffix('.metadata.json')).write_text(json.dumps(info,indent=2,sort_keys=True))
        products.append(info)
    result={**common,'delivery_schema':SCHEMA,'data_root':str(Path(final_data_root or output).resolve()),'products':products}
    catalog_path=metadata_dir/'catalog.json'
    catalog_path.write_text(json.dumps(result,indent=2,sort_keys=True))
    # Re-read all embedded bindings after writing the final files.
    read_catalog(output)
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
        elif info['schema'] in ('human.study.static.v1', *COMPACT_SCHEMAS):
            name=f"human-r{info.get('h3_resolution',7)}-static.parquet"
            table=pq.read_table(old)
            if info['schema'] in COMPACT_SCHEMAS:
                from human.pipelines.delivery.static import restore_static
                table, info=restore_static(table, info)
            pq.write_table(table, output/name, compression='zstd')
        elif info['schema'] in ('human.study.temporal.v1','human.study.calendar_lookup.v1','human.study.calendar_lookup.v2'):name='calendar-temporal.parquet';shutil.copy2(old,output/name)
        else:name=old.name;shutil.copy2(old,output/name)
        info['artifact']={**info.get('artifact',{}),'path':name,'sha256':digest(output/name),'rows':pq.read_metadata(output/name).num_rows} if name.endswith('.parquet') else {**info['artifact'],'path':name,'sha256':digest(output/name)}
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
