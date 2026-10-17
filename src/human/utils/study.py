"""Read-only consumer of the exact-byte MarineCast registry artifact interface."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import h3


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def load_static_study(path):
    """Select verified water-reporting cells at the established Human R7 contract.

    An explicit future Human resolution entry is honored. No bbox selection,
    sibling imports, or changes to the shared study configuration occur here.
    """
    path = Path(path).resolve()
    raw = path.read_bytes()
    cfg = json.loads(raw, object_pairs_hook=_unique)
    registry = cfg['grid_registry']
    if (cfg['schema_version'] != 1 or cfg['domain']['status'] != 'approved'
            or not cfg['domain'].get('approval') or registry['status'] != 'validated'
            or registry['mask_hash_policy'] != 'sha256_exact_file_bytes'):
        raise ValueError('An approved study and validated exact-byte registry are required')
    root = (path.parent / cfg['storage']['data_root']).resolve()

    def artifact(relative, sha):
        if Path(relative).is_absolute():
            raise ValueError('Study artifacts must use config-relative paths')
        target = (path.parent / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError('Study artifact escapes configured Data root')
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError('Study artifact checksum mismatch: ' + target.name)
        return data

    artifact(registry['mask_relative_path'], registry['mask_sha256'])
    manifest = json.loads(artifact(registry['manifest_relative_path'], registry['manifest_sha256']), object_pairs_hook=_unique)
    for field in ('mask_revision', 'mask_sha256', 'mask_hash_policy', 'memberships'):
        if manifest[field] != registry[field]:
            raise ValueError('Registry differs from pinned manifest: ' + field)
    resolution = cfg.get('products', {}).get('human', {}).get('h3_resolution', 7)
    entries = [e for e in registry['memberships'] if e['role'] == 'water_reporting' and e['resolution'] == resolution]
    if len(entries) != 1:
        raise ValueError('Expected one Human water-reporting membership')
    entry = entries[0]
    data = artifact(entry['relative_path'], entry['sha256'])
    if data and (not data.endswith(b'\n') or b'\r' in data):
        raise ValueError('Membership requires LF and terminal newline')
    cells = data.decode('ascii').split('\n')[:-1] if data else []
    if (not cells or len(cells) != entry['count'] or cells != sorted(set(cells))
            or any(c != c.lower() or not h3.is_valid_cell(c) or h3.get_resolution(c) != resolution for c in cells)):
        raise ValueError('Invalid membership identity, ordering, count or resolution')
    return {'cells': cells, 'config': cfg, 'data_root': root, 'identity': {
        'config_path': str(path), 'config_sha256': hashlib.sha256(raw).hexdigest(),
        'mask_revision': registry['mask_revision'], 'mask_sha256': registry['mask_sha256'],
        'membership': entry, 'h3_resolution': resolution,
        'resolution_basis': 'explicit_human_product' if 'human' in cfg.get('products', {}) else 'existing_human_static_R7_contract',
    }}
