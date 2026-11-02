"""Retained NGA physical port points and their native R7 recorded-point summary.

Reads a checksum-pinned original GeoParquet; never downloads or imports Governance.
"""
from __future__ import annotations

from human.pipelines.delivery.names import COMPACT_SCHEMAS

from collections import Counter
import copy
import json
from pathlib import Path

import geopandas as gpd
import h3
import pyarrow as pa
import pyarrow.parquet as pq

from human.utils.artifacts import sha256_file as digest

COUNT = 'ports__SOURCE_RECORD_COUNT'
STATUS = 'ports__MEASUREMENT_STATUS'
COMPLETE = 'ports__SOURCE_COVERAGE_COMPLETE'
FIELDS = [(COUNT, pa.int64()), (STATUS, pa.large_string()), (COMPLETE, pa.bool_())]
LIMITS = ('NGA World Port Index physical point references; partial published inventory. '
          'Counts are distinct retained source IDs assigned to their containing R7 cell, '
          'not port footprints, marina counts, vessel activity, navigation advice or legal authority. '
          'Zero means no retained source point in that cell, not absence of a physical facility. '
          'Harbor/facility codes and unknown attributes retain their original source values. '
          'No proximity-based facility deduplication or distance metric is applied. '
          'Local reference use only; source redistribution rights remain uncleared.')
GROUP = {'name': 'Port Locations', 'family': 'Ports', 'geometry': 'Point'}


def resolve_entry(entry, root):
    result = copy.deepcopy(entry)
    result['path'] = str((Path(root)/result['path']).resolve())
    return result


def port_features(entry):
    from human.pipelines.native.features import feature, clean
    path = Path(entry['path'])
    if not entry.get('sha256') or digest(path) != entry['sha256']:
        raise ValueError('Port original source checksum mismatch')
    raw = pq.read_schema(path).metadata or {}
    provenance = json.loads(raw.get(b'human_port_source', raw.get(b'governance_original', b'null')))
    if not provenance or provenance.get('receipt', {}).get('source_id') != 'nga_world_port_index':
        raise ValueError('Port original requires retained NGA provenance')
    frame = gpd.read_parquet(path)
    if frame.crs is None or frame.crs.to_epsg() != 4326:
        raise ValueError('Port original requires EPSG:4326')
    if frame.portNumber.isna().any() or frame.portNumber.duplicated().any():
        raise ValueError('Duplicate/null NGA port identity')
    if (not frame.geom_type.eq('Point').all() or frame.geometry.isna().any()
            or frame.geometry.is_empty.any() or not frame.geometry.is_valid.all()):
        raise ValueError('Port originals must be nonempty valid source points')
    requested = entry.get('source_feature_ids')
    if requested is not None:
        if len(requested) != len(set(requested)):
            raise ValueError('Duplicate selected NGA port identity')
        ids = frame.portNumber.astype(str)
        if set(requested)-set(ids):
            raise ValueError('Selected NGA port identities missing from pinned source')
        frame = frame.loc[ids.isin(requested)]
    features = []
    for _, row in frame.iterrows():
        identity = str(row.portNumber)
        attrs = clean(row.drop(labels='geometry').to_dict())
        attrs.update(SOURCE_DATASET_ID='nga_world_port_index', SOURCE_RECORD_ID=identity,
                     PORT_ID='nga_wpi:'+identity, FACILITY_TYPE='published_port_location_point')
        features.append(feature('ports', 'nga_wpi:'+identity, row.portName, 'port', row.geometry,
                                attrs, method='original_nga_physical_port_point', record_id='nga_wpi:'+identity))
    receipt = {'path': str(path.resolve()), 'sha256': entry['sha256'],
               'source_id': 'nga_world_port_index', 'original_provenance': provenance,
               'selected_source_feature_ids': sorted(str(row['properties']['attributes']['SOURCE_RECORD_ID']) for row in features),
               'selection_scope': entry.get('selection_scope', 'All records in the pinned original'),
               'deduplication': 'Exact NGA source identity only; marinas and nearby distinct facilities remain separate.'}
    if digest(path) != entry['sha256']:
        raise ValueError('Port source changed during read')
    return json.loads(json.dumps(features)), receipt


def group_metadata(features):
    return {**GROUP, 'records': len(features), 'context_features': 0,
            'categories': dict(Counter(f['properties']['category'] for f in features)),
            'limitations': LIMITS, 'regional_completeness': 'partial'}


def add_summary(table, features):
    """Append only port columns; all existing Arrow arrays and nulls are preserved."""
    if any(name in table.column_names for name, _ in FIELDS):
        raise ValueError('Port summary already present; explicit reconciliation required')
    counts = Counter(f['properties']['h3_index'] for f in features)
    cells = table['h3_index'].to_pylist()
    for cell in cells:
        if not h3.is_valid_cell(cell) or h3.get_resolution(cell) != 7:
            raise ValueError('Port summaries require native R7 cells')
    arrays = ([counts[cell] for cell in cells], ['partial_published_port_inventory']*len(cells), [False]*len(cells))
    for (name, kind), values in zip(FIELDS, arrays):
        table = table.append_column(name, pa.array(values, type=kind))
    return table


def summary_binding(features):
    """Small source-ID/cell binding for validation without a delivered point file."""
    return {str(f['properties']['attributes']['SOURCE_RECORD_ID']): f['properties']['h3_index'] for f in features}


def extend_staging(staging, entry):
    """Append static port counts and retained source provenance, without a native export."""
    from human.pipelines.native.features import summary_fields
    staging = Path(staging)
    catalog_path = staging/'catalog.json'
    catalog = json.loads(catalog_path.read_text())
    static = next(p for p in catalog['products'] if p['schema'] == 'human.study.static.v1')
    features, receipt = port_features(entry)
    static_path = staging/static['artifact']['path']
    table = pq.read_table(static_path)
    if COUNT not in table.column_names:
        table = add_summary(table, features)
    else:
        base = table.drop_columns([name for name, _ in FIELDS])
        if not add_summary(base, features).equals(table, check_metadata=False):
            raise ValueError('Existing Human port summaries differ')
    static.update(ports=receipt, port_h3_by_source_id=summary_binding(features),
                  infrastructure_fields=summary_fields(table.column_names))
    metadata = {k: v for k, v in static.items() if k != 'artifact'}
    pq.write_table(table.replace_schema_metadata({**(table.schema.metadata or {}), b'human_study': json.dumps(metadata, sort_keys=True).encode()}), static_path, compression='zstd')
    static['artifact'].update(sha256=digest(static_path))
    for product in (static,):
        (staging/product['artifact']['path']).with_suffix('.metadata.json').write_text(json.dumps(product, indent=2, sort_keys=True))
    catalog['ports_migration'] = {'method': 'nga-physical-port-ownership-1', 'source': receipt, 'limitations': LIMITS}
    catalog_path.write_text(json.dumps(catalog, indent=2, sort_keys=True))


def migrate_delivery(root, entry, *, metadata_dir):
    """Narrow, locked publication with verified preservation and rollback.

    Retains originals and receipt ancestry, creates no extra current/replay product.
    """
    import tempfile
    from human.pipelines.delivery.finalize import read_catalog, materialize_legacy, finalize_delivery
    from human.pipelines.delivery.validation import validate_delivery
    root, metadata_dir = Path(root).resolve(), Path(metadata_dir).resolve()
    lock = root.parent/('.'+root.name+'.human.lock')
    lock.mkdir()
    try:
        validate_delivery(root)
        before = {p.name: digest(p) for p in root.iterdir() if p.suffix == '.parquet'}
        if 'ports' in next(p for p in read_catalog(root)['products'] if p['schema'] in ('human.study.static.v1', *COMPACT_SCHEMAS)):
            raise ValueError('Ports already present; inspect current source binding before migration')
        with tempfile.TemporaryDirectory(prefix='.'+root.name+'-ports-', dir=root.parent) as temporary:
            stage, candidate, backup = (Path(temporary)/name for name in ('legacy', 'candidate', 'previous'))
            materialize_legacy(root, stage)
            extend_staging(stage, entry)
            finalize_delivery(stage, candidate, metadata_dir, final_data_root=root)
            if (root/'REVIEW_RESULTS.txt').is_file():
                import shutil
                shutil.copy2(root/'REVIEW_RESULTS.txt', candidate/'REVIEW_RESULTS.txt')
            report = validate_delivery(candidate)
            for name in ('static.parquet', 'dynamic-calendar.parquet'):
                old, new = pq.read_table(root/name), pq.read_table(candidate/name)
                if name=='static.parquet':
                    from human.pipelines.delivery.static import restore_static
                    old_info=next(p for p in read_catalog(root)['products'] if p['artifact']['path']==name)
                    new_info=next(p for p in read_catalog(candidate)['products'] if p['artifact']['path']==name)
                    if old_info['schema'] in COMPACT_SCHEMAS: old,_=restore_static(old,old_info)
                    new,_=restore_static(new,new_info)
                columns=[c for c in old.column_names if name!='static.parquet' or c not in {n for n,_ in FIELDS}]
                if not old.select(columns).equals(new.select(columns), check_metadata=False):
                    raise ValueError('Non-port Human values changed')
            if before != {name: digest(root/name) for name in before}:
                raise ValueError('Human delivery changed during migration')
            root.rename(backup)
            try:
                candidate.rename(root)
                report = validate_delivery(root)
            except BaseException:
                if root.exists(): root.rename(candidate)
                backup.rename(root)
                raise
        return report
    finally:
        lock.rmdir()
