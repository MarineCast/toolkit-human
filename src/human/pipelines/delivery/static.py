"""Compact static delivery and lossless, H3-keyed external audit receipts."""
from __future__ import annotations

import base64
import copy
from importlib.resources import files
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from human.utils.artifacts import sha256_file as digest
from human.pipelines.native.features import delivery_fields

from human.pipelines.delivery.names import STATIC_NAMES, STATIC_NAMES_V3, STATIC_NAMES_V4, TRANSFORMED_SOURCE_COLUMNS, PRESENCE_FROM_COUNTS, COMPACT_SCHEMAS

SCHEMA = 'human.study.static.v6'
AUDIT_SCHEMA = 'human.static.audit.v1'
AUDIT_FILE = 'static-audit.parquet'


def specification(version=7):
    return json.loads(files('human').joinpath(f'resources/contracts/final-products.v{version}.json').read_text())['products']['static.parquet']


def read_audit(table, info):
    """Verify the external checksum, unique keys and complete cell membership."""
    binding = info['static_audit']
    if binding['schema'] != AUDIT_SCHEMA or binding.get('primary_key') != ['h3_index']:
        raise ValueError('Unsupported Human static audit schema')
    artifact = binding['artifact']
    path = Path(artifact['path'])
    if path.name != AUDIT_FILE or not path.is_file() or digest(path) != artifact['sha256']:
        raise ValueError('Human static audit checksum mismatch or missing receipt')
    audit = pq.read_table(path)
    if digest(path) != artifact['sha256']:
        raise ValueError('Human static audit changed during read')
    if audit.num_rows != table.num_rows or audit.num_rows != artifact['rows'] or audit.column_names != binding['columns']:
        raise ValueError('Human static audit shape mismatch')
    ids = audit['h3_index']
    if ids.null_count or len(pc.unique(ids)) != audit.num_rows:
        raise ValueError('Human static audit requires unique nonnull H3 keys')
    order = pc.index_in(table['h3_index'], value_set=ids)
    if order.null_count or len(pc.unique(table['h3_index'])) != table.num_rows:
        raise ValueError('Human static audit H3 membership differs from delivery')
    header = json.loads(audit.schema.metadata[b'human_static_audit'])
    if header['schema'] != AUDIT_SCHEMA or header['source_metadata']['schema'] != binding['source_schema']:
        raise ValueError('Human static audit metadata schema mismatch')
    return audit.take(order), header


def restore_static(table, info):
    """Reconstruct original column order/types/nulls using explicit H3 alignment."""
    audit, header = read_audit(table, info)
    original = pa.ipc.read_schema(pa.BufferReader(base64.b64decode(header['source_arrow_schema_base64'])))
    public = table
    transformed = TRANSFORMED_SOURCE_COLUMNS if info['schema'] in (SCHEMA, 'human.study.static.v5') else set()
    if info['schema'] in (SCHEMA, 'human.study.static.v3', 'human.study.static.v4', 'human.study.static.v5'):
        mapping = {'human.study.static.v3':STATIC_NAMES_V3, 'human.study.static.v4':STATIC_NAMES_V4}.get(info['schema'], STATIC_NAMES)
        if info.get('column_name_mapping') != mapping:
            raise ValueError('Static column name mapping differs from contract')
        inverse = {value:key for key,value in mapping.items()}
        table = table.rename_columns([inverse.get(name,name) for name in table.column_names])
    expected = ['h3_index', *(name for name in original.names if name not in table.column_names or name in transformed)]
    if audit.column_names != expected:
        raise ValueError('Human static audit column partition mismatch')
    columns = []
    for field in original:
        values = table[field.name] if field.name in table.column_names and field.name not in transformed else audit[field.name]
        if pa.types.is_null(field.type):
            if values.null_count != len(values):
                raise ValueError('Originally unmeasured static field gained values')
            values = pa.nulls(len(values))
        columns.append(values)
    full = pa.Table.from_arrays(columns, names=original.names).cast(original)
    if info['schema'] in (SCHEMA, 'human.study.static.v5') and not project_static(full, version=6 if info['schema']=='human.study.static.v5' else 7).equals(public, check_metadata=False):
        raise ValueError('Public infrastructure transformation or population projection mismatch')
    return full, copy.deepcopy(header['source_metadata'])


def project_static(source, *, version=7):
    """Apply reviewed aliases and count encodings, without modifying source arrays."""
    schema=pa.ipc.read_schema(pa.BufferReader(base64.b64decode(specification(version)['arrow_schema_base64'])))
    arrays=[]
    for raw,public in STATIC_NAMES.items():
        field=schema.field(public)
        if raw not in source.column_names:
            if raw not in TRANSFORMED_SOURCE_COLUMNS:
                raise ValueError('Static source lacks required population/spatial support')
            values=pa.nulls(source.num_rows,type=pa.int64())
        else: values=source[raw]
        if raw in TRANSFORMED_SOURCE_COLUMNS:
            if pa.types.is_null(values.type):values=pa.nulls(source.num_rows,type=pa.int64())
            # Safe casts reject fractional, nonfinite and out-of-range record counts.
            counts=pc.cast(values,pa.int64(),safe=True)
            if pc.any(pc.less(counts,0)).as_py():
                raise ValueError('Infrastructure record counts must be nonnegative integers')
            counts=pc.fill_null(counts,0)
            values=pc.greater(counts,0) if public=='has_port' else counts
        arrays.append(pc.cast(values,field.type,safe=True))
    projected=dict(zip(STATIC_NAMES.values(),arrays))
    for presence,count in PRESENCE_FROM_COUNTS.items():
        if presence in schema.names:
            arrays.append(pc.cast(pc.greater(projected[count],0),schema.field(presence).type))
    return pa.Table.from_arrays(arrays,schema=schema)


def compact_static(table, info, metadata_dir):
    """Project quantities/support and bind all omitted arrays to an immutable receipt."""
    if info['schema'] in COMPACT_SCHEMAS:
        table, info = restore_static(table, info)
    source = table
    original_info = copy.deepcopy(info)
    compact = project_static(source)
    invertible = set(STATIC_NAMES) - TRANSFORMED_SOURCE_COLUMNS
    removed = [name for name in source.column_names if name not in invertible]
    audit = source.select(['h3_index', *removed]).replace_schema_metadata({
        b'human_static_audit': json.dumps({'schema': AUDIT_SCHEMA,
            'source_arrow_schema_base64': base64.b64encode(source.schema.remove_metadata().serialize()).decode(),
            'source_metadata': original_info}, sort_keys=True).encode()})
    path = Path(metadata_dir)/AUDIT_FILE
    if path.exists():
        raise FileExistsError('Use a new immutable static audit receipt')
    pq.write_table(audit, path, compression='zstd')
    if not audit.equals(pq.read_table(path), check_metadata=True):
        raise ValueError('Static audit readback changed values/types/nulls')
    result = copy.deepcopy(info)
    result.update(schema=SCHEMA, column_name_mapping=STATIC_NAMES.copy(), infrastructure_fields=delivery_fields(),
        static_audit={'schema': AUDIT_SCHEMA, 'primary_key': ['h3_index'],
            'columns': audit.column_names, 'source_schema': original_info['schema'],
            'artifact': {'path': str(path.resolve()), 'sha256': digest(path), 'rows': audit.num_rows},
            'scope': 'Removed row-specific fields and original dictionary; join by H3, never implicit row order.'})
    if 'distance_delivery' in result['population']:
        result['population']['distance_delivery']['fields'] = [STATIC_NAMES[name] for name in result['population']['distance_delivery']['fields']]
    result['population']['fields'] = {STATIC_NAMES[name]:definition for name,definition in result['population']['fields'].items()}
    for name, definition in result['population']['fields'].items():
        base = definition['base_metric']
        definition.pop('coverage_status', None)
        definition.pop('unverified_geographic_support', None)
        definition['support_location'] = 'external_static_audit'
        definition['zero_imputation_rule'] = 'coverage_status in [partial, unavailable]'
        definition['audit_fields'] = {**definition.get('audit_fields', {}),
            **{key: definition.pop(key) for key in ('zero_imputed','method_column','source_subtotal') if key in definition},
            'coverage_status': base+'__coverage_status',
            'unverified_geographic_support': base+'__unverified_geographic_support'}
        if 'density' in name:
            definition['audit_fields']['cell_area_km2'] = 'cell_area_km2'
    restored, _ = restore_static(compact, result)
    if not restored.equals(source, check_metadata=False):
        raise ValueError('Compact static and external audit do not preserve the source table')
    return compact, result
