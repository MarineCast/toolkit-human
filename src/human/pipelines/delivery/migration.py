"""Explicit, rollback-safe migration to the compact Human delivery."""
from __future__ import annotations

from human.pipelines.delivery.names import COMPACT_SCHEMAS

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile

import pyarrow.parquet as pq

from human.pipelines.delivery.finalize import digest, finalize_delivery, read_catalog
from human.pipelines.delivery.validation import validate_delivery


def migrate_reviewed_delivery(root, *, metadata_dir):
    """Publish the 14-column static table and its external audit receipt.

    The compact output applies the reviewed encodings; reconstruction preserves
    every original value/type/null, the review
    note, original sources and prior external receipts. Final readback failure
    rolls back the directory. No source collection or population calculation runs.
    """
    root, receipt = Path(root).resolve(), Path(metadata_dir).resolve()
    if receipt.is_relative_to(root) or root.is_relative_to(receipt):
        raise ValueError('Migration receipts must be outside delivery')
    if receipt.exists():
        raise FileExistsError('Use a new immutable migration receipt directory')
    from human.main import _runtime
    runtime = _runtime()
    lock = root.parent/('.'+root.name+'.human.lock')
    lock.mkdir()
    try:
        catalog = read_catalog(root)
        names={p['artifact']['path'] for p in catalog['products']}
        if names not in ({'static.parquet','dynamic-calendar.parquet'}, {'static.parquet','dynamic-calendar.parquet','static-native.parquet'}):
            raise ValueError('Migration requires a verified Human delivery')
        before = {p.name: digest(p) for p in root.iterdir() if p.name != '.DS_Store'}
        if shutil.disk_usage(root.parent).free < 3*sum(p.stat().st_size for p in root.iterdir()):
            raise OSError('Insufficient free space for staged migration and rollback')
        with tempfile.TemporaryDirectory(prefix='.'+root.name+'-review-', dir=root.parent) as temp:
            candidate, backup = Path(temp)/'candidate', Path(temp)/'previous'
            finalize_delivery(root, candidate, receipt, final_data_root=root)
            from human.pipelines.delivery.static import restore_static
            old_info=next(p for p in catalog['products'] if p['artifact']['path']=='static.parquet')
            original=pq.read_table(root/'static.parquet')
            if old_info['schema'] in COMPACT_SCHEMAS:original, _=restore_static(original,old_info)
            new_info=next(p for p in read_catalog(candidate)['products'] if p['artifact']['path']=='static.parquet')
            reconstructed, _=restore_static(pq.read_table(candidate/'static.parquet'),new_info)
            if not original.equals(reconstructed, check_metadata=False):
                raise ValueError('Migration changed static values, types or nulls')
            old_calendar=next(p for p in catalog['products'] if p['artifact']['path']=='dynamic-calendar.parquet')
            if old_calendar['schema']=='human.study.calendar_lookup.v2' and not pq.read_table(root/'dynamic-calendar.parquet').equals(pq.read_table(candidate/'dynamic-calendar.parquet'),check_metadata=False):
                raise ValueError('Migration changed the compact calendar')
            if (root/'REVIEW_RESULTS.txt').is_file():
                shutil.copy2(root/'REVIEW_RESULTS.txt', candidate/'REVIEW_RESULTS.txt')
            report = validate_delivery(candidate)
            if before != {p.name: digest(p) for p in root.iterdir() if p.name != '.DS_Store'}:
                raise ValueError('Human data changed during migration')
            root.rename(backup)
            try:
                candidate.rename(root)
                report = validate_delivery(root)
            except BaseException:
                if root.exists(): root.rename(candidate)
                backup.rename(root)
                raise
        # TemporaryDirectory discards only the replaced task-owned backup after readback.
        summary = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
            'method': 'human-compact-static-migration-4', 'previous_sha256': before,
            'runtime': runtime,
            'original_static_value_type_null_parity': True, 'static_audit':new_info['static_audit'], 'validation': report,
            'sources': 'Retained in place; no acquisition or population recalculation',
            'cleanup': 'Task staging and superseded publication backup removed after successful readback'}
        (receipt/'migration-validation.json').write_text(json.dumps(summary, indent=2, sort_keys=True))
        return report
    finally:
        lock.rmdir()
