"""Audit native evidence and atomically publish immutable static research snapshots.

Integrity is distinct from scientific qualification and application-profile conformance.
No acquisition, temporal reduction, scientific recalculation or eligibility promotion occurs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from human.static_matrix import KEYS, _checksum, export


def read_json(path: Path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f'Non-standard JSON number: {value}')

    return json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=invalid)


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def record_path(manifest: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else manifest.parent / path


def audit(manifests: list[Path]) -> dict:
    """Verify every declared native artifact and input, without reading whole tables."""
    reports = []
    for path in manifests:
        manifest = read_json(path)
        if manifest.get('stage') != 'build':
            raise ValueError(f'Expected a native build manifest: {path}')
        records = []
        for role in ('inputs', 'artifacts'):
            for item in manifest.get(role, []):
                source = record_path(path, item['path'])
                problems = []
                actual = None
                rows = None
                if not source.is_file():
                    problems.append('missing_file')
                else:
                    actual = _checksum(source)
                    if actual != item.get('sha256'):
                        problems.append('checksum_mismatch')
                    if source.suffix == '.parquet':
                        try:
                            rows = pq.read_metadata(source).num_rows
                            if item.get('rows') is not None and rows != item['rows']:
                                problems.append('row_count_mismatch')
                        except Exception as exc:
                            problems.append(f'invalid_parquet: {type(exc).__name__}')
                records.append({'role': role, 'path': str(source.resolve()),
                                'expected_sha256': item.get('sha256'), 'actual_sha256': actual,
                                'actual_rows': rows, 'problems': problems})
        if not manifest.get('artifacts'):
            raise ValueError(f'No native artifacts: {path}')
        reports.append({'manifest_path': str(path.resolve()), 'manifest_sha256': _checksum(path),
                        'product': manifest.get('product'),
                        'source_completeness': manifest.get('source_completeness', 'unknown'),
                        'known_limitations': manifest.get('known_limitations', []),
                        'licenses': manifest.get('licenses', []), 'records': records})
    if not reports:
        raise ValueError('At least one manifest is required')
    return {'audit_version': 1, 'integrity_passed': all(not r['problems'] for m in reports for r in m['records']),
            'scientific_qualification': 'not_assessed', 'manifests': reports}


def publish_static(manifests: list[Path], output: Path, *, code_sha: str,
                   input_manifest: Path | None = None, record_count_profile: bool = False) -> Path:
    """New-only whole-directory publication; preserve exact native evidence and nulls.

    Cooperating publishers serialize on an exclusive sibling lock. A crashed lock is
    retained for operator inspection; no stale-lock or previous-release deletion occurs.
    """
    if not re.fullmatch(r'[0-9a-f]{40}', code_sha):
        raise ValueError('code_sha must be the full producer Git revision')
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_name(f'.{output.name}.lock')
    # Check under the lock as well: a concurrent completed publication must not be replaced.
    with lock.open('x') as handle:
        handle.write(f'pid={os.getpid()}\n')
    stage = None
    try:
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        report = audit(manifests)
        if not report['integrity_passed']:
            raise ValueError('Native input/artifact integrity audit failed; run audit-manifests for details')
        stage = Path(tempfile.mkdtemp(prefix=f'.{output.name}.staging-', dir=output.parent))
        inventory = []

        def copy_verified(source, relative, expected):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if _checksum(target) != expected:
                raise ValueError(f'Source changed during publication: {source}')
            inventory.append({'path': str(relative), 'sha256': expected,
                              'bytes': target.stat().st_size, 'original_path': str(source)})
            return target

        # Export from release-local artifact copies, not mutable upstream paths.
        export_manifests = []
        for index, (path, audited) in enumerate(zip(manifests, report['manifests'])):
            prefix = Path('native') / str(index)
            original = copy_verified(path, prefix / 'manifest.json', audited['manifest_sha256'])
            manifest = read_json(original)
            for number, item in enumerate(manifest['artifacts']):
                source = record_path(path, item['path'])
                target = copy_verified(source, prefix / str(number) / source.name, item['sha256'])
                item['path'] = str(target)
            adapter = stage / f'export-{index}.json'
            write_json(adapter, manifest)
            export_manifests.append(adapter)
        supplemental = None
        if input_manifest is not None:
            supplemental = copy_verified(input_manifest, Path('source-inputs.json'), _checksum(input_manifest))
        matrix = export(export_manifests, stage / 'static-native.parquet', supplemental)
        table = pq.read_table(matrix)
        # Remove transient staging paths from embedded metadata, retaining original manifests.
        metadata = json.loads(table.schema.metadata[b'human_static_matrix'])
        metadata['sources'] = [
            {'manifest_path': f'native/{i}/manifest.json', 'manifest_sha256': m['manifest_sha256'],
             'native_manifest': read_json(stage / f'native/{i}/manifest.json')}
            for i, m in enumerate(report['manifests'])]
        table = table.replace_schema_metadata({**table.schema.metadata,
            b'human_static_matrix': json.dumps(metadata, sort_keys=True, allow_nan=False).encode()})
        pq.write_table(table, matrix, compression='zstd')
        frame = table.to_pandas()
        pd.testing.assert_frame_equal(frame, pd.read_parquet(matrix))
        fields = {}
        for name in frame.columns:
            series = frame[name]
            if pd.api.types.is_numeric_dtype(series):
                values = series.dropna().to_numpy(dtype=float)
                if not np.isfinite(values).all():
                    raise ValueError(f'Nonfinite metric: {name}')
            fields[name] = {'null_count': int(series.isna().sum())}
            if name not in KEYS and pd.api.types.is_numeric_dtype(series):
                fields[name]['zero_count'] = int(series.eq(0).sum())
        for adapter in export_manifests:
            adapter.unlink()
        write_json(stage / 'integrity-audit.json', report)
        for name in ('static-native.parquet', 'integrity-audit.json'):
            p = stage / name
            inventory.append({'path': name, 'sha256': _checksum(p), 'bytes': p.stat().st_size})
        release = {'release_schema_version': 1, 'data_release_id': output.name,
                   'created_at': datetime.now(UTC).isoformat(),
                   'producer': {'package': 'toolkit-human', 'version': version('toolkit-human'), 'git_sha': code_sha},
                   'scientific_method': 'native-static-union-v1: no scientific recalculation',
                   'publication_method_version': 'immutable-static-snapshot-v1',
                   'qualification': 'research_only', 'model_eligible': False,
                   'application_profile_conformance': 'not_claimed',
                   'spatial_support': 'Native H3 R7 union; no clipping, transfer or resampling',
                   'temporal_support': 'Native static reference vintages; no fabricated daily rows',
                   'rows': len(frame), 'fields': fields, 'artifacts': inventory,
                   'limitations': metadata['limitations'] + [
                       'Integrity checks do not qualify source rights, completeness or scientific use.',
                       'Native statuses are preserved; application metric/status/coverage mapping remains pending.',
                       'Raw inputs are verified at publication but remain external; their hashes and original paths are retained.',
                       'This release covers only supplied static families, not all Human products.']}
        if record_count_profile:
            from human.static_profile import write_profile
            for profile_path in write_profile(stage, code_sha=code_sha, data_release_id=output.name):
                inventory.append({'path': profile_path.name, 'sha256': _checksum(profile_path),
                                  'bytes': profile_path.stat().st_size})
            release['profile_products'] = ['human.retained_catalog_record_counts']
        write_json(stage / 'release.json', release)
        verify_static(stage)
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        stage.rename(output)
        stage = None
        return output
    finally:
        if stage is not None:
            shutil.rmtree(stage)
        lock.unlink()


def verify_static(root: Path) -> dict:
    """Consumer integrity round-trip using only release-local inventory paths."""
    release = read_json(root / 'release.json')
    seen = set()
    for record in release['artifacts']:
        relative = Path(record['path'])
        if relative.is_absolute() or '..' in relative.parts or record['path'] in seen:
            raise ValueError('Unsafe or duplicate release artifact path')
        seen.add(record['path'])
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()) or _checksum(path) != record['sha256']:
            raise ValueError(f'Release checksum mismatch: {relative}')
    if not {'static-native.parquet', 'integrity-audit.json'} <= seen:
        raise ValueError('Incomplete static release inventory')
    table = pq.read_table(root / 'static-native.parquet')
    if table.num_rows != release['rows'] or set(table.column_names) != set(release['fields']):
        raise ValueError('Release table schema/count mismatch')
    frame = table.to_pandas()
    if frame[KEYS].isna().any().any() or frame.H3_INDEX.duplicated().any():
        raise ValueError('Invalid release identity')
    if not frame.H3_RESOLUTION.eq(7).all() or any(
            not h3.is_valid_cell(cell) or h3.get_resolution(cell) != 7 for cell in frame.H3_INDEX):
        raise ValueError('Invalid native H3 R7 support')
    for name, summary in release['fields'].items():
        series = frame[name]
        if int(series.isna().sum()) != summary['null_count']:
            raise ValueError(f'Null count mismatch: {name}')
        if 'zero_count' in summary and int(series.eq(0).sum()) != summary['zero_count']:
            raise ValueError(f'Zero count mismatch: {name}')
    if release.get('profile_products'):
        from human.static_profile import verify_profile
        verify_profile(root / 'catalog-record-counts.manifest.json')
    return release
