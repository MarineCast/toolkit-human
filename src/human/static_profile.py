"""Narrow v0.1 mapping of retained catalog-record counts, not physical site totals."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from jsonschema import Draft202012Validator, FormatChecker

from human.release import read_json, write_json
from human.static_matrix import _checksum

CONTRACT_REVISION = '16a3a926b214f4e186014c3efcaaf4156c7dc61d'
METHOD = 'retained-catalog-record-counts-v1'
STATUSES = ['observed', 'unknown', 'unavailable', 'not_applicable', 'partial']
MAPPING = {
    'place_catalog_record_count': 'places__CATALOG_RECORD_COUNT',
    'boat_launch_catalog_record_count': 'boat_launch_access__BOAT_LAUNCH_COUNT',
    'shore_access_catalog_record_count': 'public_shore_access__PUBLIC_ACCESS_SITE_COUNT',
}


def record_counts(native: pd.DataFrame) -> pd.DataFrame:
    """Preserve native known record counts and nulls without asserting site coverage."""
    result = pd.DataFrame({'h3_index': native.H3_INDEX})
    for name, source in MAPPING.items():
        values = native[source] if source in native else pd.Series(pd.NA, index=native.index, dtype='Float64')
        finite = values.dropna().astype(float)
        if not np.isfinite(finite).all() or (finite < 0).any() or (finite % 1 != 0).any():
            raise ValueError(f'Invalid catalog count: {source}')
        result[name] = values.astype('Int64')
        result[f'{name}_status'] = np.where(values.notna(), 'observed', 'unknown')
    return result


def write_profile(root: Path, *, code_sha: str, data_release_id: str) -> list[Path]:
    """Write a companion inside the publisher's owned staging directory."""
    native_table = pq.read_table(root / 'static-native.parquet')
    native_metadata = json.loads(native_table.schema.metadata[b'human_static_matrix'])
    frame = record_counts(native_table.to_pandas())
    artifact = root / 'catalog-record-counts.parquet'
    frame.to_parquet(artifact, index=False, compression='zstd')
    config = root / 'catalog-record-counts.config.json'
    write_json(config, {'mapping': MAPPING, 'method_version': METHOD,
                       'data_release_id': data_release_id, 'shared_contract_revision': CONTRACT_REVISION,
                       'native_matrix_sha256': _checksum(root / 'static-native.parquet'),
                       'native_manifests': [{'path': s['manifest_path'], 'sha256': s['manifest_sha256']}
                                            for s in native_metadata['sources']]})
    fields = {'h3_index': {'type': 'string', 'description': 'Native H3 R7 cell identity.',
                         'units': None, 'nullable': False, 'statistic': 'identity'}}
    for name, source in MAPPING.items():
        fields[name] = {'type': 'integer', 'description': f'Exact retained catalog record count from {source}; not unique physical sites, visits, people or complete regional coverage.',
                        'units': 'source records', 'nullable': True, 'statistic': 'count',
                        'missing_reason_field': f'{name}_status', 'minimum': 0}
        fields[f'{name}_status'] = {'type': 'string', 'description': f'Value state of {name}: observed means a valid derived count within retained source records; unknown means no count supplied. Catalog completeness outside those records is unknown.',
                                   'units': None, 'nullable': False, 'statistic': 'status', 'enum': STATUSES}
    sources = []
    for i, parent in enumerate(native_metadata['sources']):
        native = parent['native_manifest']
        for j, source in enumerate(native.get('sources', [])):
            retrieved = source.get('retrieved_at_utc')
            if not retrieved:
                raise ValueError('Source retrieval time is required for profile provenance')
            sources.append({'id': f'native_{i}.{source.get("name", j)}',
                            'source_version': source.get('sha256'), 'retrieved_at': retrieved,
                            'spatial_coverage': json.dumps(source['spatial_coverage'], sort_keys=True) if source.get('spatial_coverage') else 'unknown; native source manifest has no spatial coverage declaration',
                            'temporal_coverage': json.dumps(source['temporal_coverage'], sort_keys=True) if source.get('temporal_coverage') else 'unknown; native source manifest has no temporal coverage declaration',
                            'license': source.get('license') or 'unresolved',
                            'attribution': source.get('attribution') or 'unresolved',
                            'redistribution': 'Local research delivery only; native source terms retained. No new public redistribution determination.'})
    if not sources:
        raise ValueError('Profile requires native source provenance')
    manifest = {'contract_version': '0.1',
                'product': {'id': 'human.retained_catalog_record_counts', 'version': '0.1.0',
                            'quantity_kind': 'human_activity',
                            'description': 'Static retained-record inventory context; not measured human activity or a complete physical facility inventory.'},
                'producer': {'repository': 'toolkit-human', 'package': 'toolkit-human',
                             'package_version': version('toolkit-human'), 'git_sha': code_sha, 'working_tree_dirty': False},
                'identity': {'row_description': 'One row per native static-union H3 R7 cell.', 'primary_key': ['h3_index']},
                'spatial': {'type': 'h3', 'resolution': 7, 'index_fields': ['h3_index'], 'support': 'cell', 'crs': 'EPSG:4326',
                            'domain': 'Exact native static union in static-native.parquet; population-only rows retain unknown catalog counts. No dense domain or marine transfer.',
                            'method': 'Retain existing native R7 assignments without resampling; this product does not assign a new global Human resolution policy.'},
                'temporal': {'type': 'static', 'meaning': 'Retained snapshots at their native source vintages in the companion manifests; no current validity, daily observations or fabricated date.'},
                'fields': fields,
                'provenance': {'created_at': datetime.now(UTC).isoformat(),
                               'configuration': {'description': 'Exact UTF-8 bytes of catalog-record-counts.config.json beside this manifest; pinned native evidence and explicit field mapping.', 'sha256': _checksum(config)},
                               'sources': sources,
                               'processing': f'Method {METHOD}; generated data release {data_release_id}. Preserve native matrix and companions; known count => observed, absent/null count => unknown; no inferred zero, physical-site deduplication or regional completeness. Native calculation methods remain in pinned manifests. Shared schema revision {CONTRACT_REVISION}.'},
                'artifact': {'path': artifact.name, 'format': 'parquet', 'sha256': _checksum(artifact), 'row_count': len(frame)},
                'limitations': ['Only retained record counts are mapped; population estimates, shoreline lengths/fractions and other native metrics remain in native companions pending metric-specific qualification.',
                                'Source record counts can include multiple records for one physical facility; neither a positive count nor zero establishes actual people, visits, reporting effort or legal access.',
                                'Scientific/model eligibility and current source coverage are not established by profile conformance.',
                                'The observed status describes the exact count of retained records, not completeness against an unknown real-world facility denominator.']}
    path = root / 'catalog-record-counts.manifest.json'
    write_json(path, manifest)
    verify_profile(path)
    pd.testing.assert_frame_equal(frame, pd.read_parquet(artifact))
    return [artifact, config, path]


def verify_profile(path: Path) -> dict:
    """Validate this named static product, including its native mapping and pinned inputs."""
    manifest = read_json(path)
    schema = json.loads(files('human').joinpath('resources/contracts/product-manifest.schema.json').read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    if manifest['product']['id'] != 'human.retained_catalog_record_counts' or manifest['temporal']['type'] != 'static':
        raise ValueError('Unsupported product/profile')
    if manifest['identity']['primary_key'] != ['h3_index'] or manifest['spatial']['resolution'] != 7:
        raise ValueError('Unsupported identity/support')
    if manifest['spatial']['index_fields'] != ['h3_index'] or manifest['spatial']['support'] != 'cell' or manifest['artifact']['format'] != 'parquet':
        raise ValueError('Unsupported spatial support or artifact format')
    root = path.parent.resolve()
    artifact = root / manifest['artifact']['path']
    if not artifact.resolve().is_relative_to(root) or _checksum(artifact) != manifest['artifact']['sha256']:
        raise ValueError('Profile artifact checksum mismatch')
    config_path = root / 'catalog-record-counts.config.json'
    if _checksum(config_path) != manifest['provenance']['configuration']['sha256']:
        raise ValueError('Profile configuration checksum mismatch')
    config = read_json(config_path)
    if config['mapping'] != MAPPING or config['method_version'] != METHOD:
        raise ValueError('Unknown profile mapping/method')
    if _checksum(root / 'static-native.parquet') != config['native_matrix_sha256']:
        raise ValueError('Native matrix checksum mismatch')
    for item in config['native_manifests']:
        source = root / item['path']
        if not source.resolve().is_relative_to(root) or _checksum(source) != item['sha256']:
            raise ValueError('Native manifest checksum mismatch')
    frame = pd.read_parquet(artifact)
    if len(frame) != manifest['artifact']['row_count'] or set(frame) != set(manifest['fields']):
        raise ValueError('Profile row count or field set mismatch')
    if frame.h3_index.isna().any() or frame.h3_index.duplicated().any() or any(
            not h3.is_valid_cell(c) or h3.get_resolution(c) != 7 for c in frame.h3_index):
        raise ValueError('Invalid profile H3 identity')
    for name in MAPPING:
        field = manifest['fields'][name]
        status_name = f'{name}_status'
        status_field = manifest['fields'][status_name]
        if field['type'] != 'integer' or not field['nullable'] or field.get('minimum') != 0 or status_field['type'] != 'string' or status_field['nullable']:
            raise ValueError('Invalid profile field type/nullability')
        if field.get('missing_reason_field') != status_name or manifest['fields'][status_name].get('enum') != STATUSES:
            raise ValueError('Invalid metric/status association')
        if not frame[status_name].isin(['observed', 'unknown']).all():
            raise ValueError('Unsupported count status; partial estimates require a separate coverage method')
        if not frame[status_name].eq('observed').equals(frame[name].notna()):
            raise ValueError('Invalid count/status combination')
    expected = record_counts(pd.read_parquet(root / 'static-native.parquet'))
    pd.testing.assert_frame_equal(expected, frame)
    return manifest
