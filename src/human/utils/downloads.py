"""Checksum-pinned, bounded immutable source downloads shared by Human pipelines."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse

from human.utils.artifacts import sha256_file as digest


class MissingHumanSources(FileNotFoundError):
    """An original or normalized input is unavailable at its configured location."""


def _pin(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Source assets require a lowercase SHA256')
    return value


def obtain(path, url, *, expected_sha256=None, mode, budget, allowed_hosts=None):
    """Keep originals immutable; publish only complete, verified HTTPS responses.

    Existing unpinned inputs are accepted only for lower-level offline compatibility.
    New live acquisitions always require a pin. Requests share download/byte limits,
    do not follow redirects, and never overwrite an existing original or partial file.
    """
    path = Path(path)
    if expected_sha256 is not None:
        _pin(expected_sha256)
    if path.exists():
        if not path.is_file():
            raise ValueError(f'Original source must be a regular file: {path}')
        if expected_sha256 and digest(path) != expected_sha256:
            raise ValueError(f'Source checksum mismatch: {path}')
        return path
    if mode != 'live' or not url:
        raise MissingHumanSources(f'Unresolved source: {path}; configure a retained location or an exact source URL/checksum')
    expected_sha256 = _pin(expected_sha256)
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Collection requires an HTTPS source URL without embedded credentials')
    if allowed_hosts is not None and parsed.hostname not in allowed_hosts:
        raise ValueError('Census downloads require an explicitly configured official HTTPS source URL')
    if budget['downloads'] <= 0 or budget['bytes'] <= 0:
        raise ValueError('Source collection exceeds max_downloads or max_download_bytes')
    receipt = path.with_name(path.name + '.receipt.json')
    if receipt.exists():
        raise FileExistsError(f'Original source is absent but its receipt already exists: {path}')
    import requests
    budget['downloads'] -= 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.human-source-', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            with requests.get(url, stream=True, timeout=(15, 120), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise ValueError(f'Source response must be HTTP 200 (got {response.status_code}): {url}')
                for block in response.iter_content(1024 * 1024):
                    budget['bytes'] -= len(block)
                    if budget['bytes'] < 0:
                        raise ValueError('Source collection exceeded max_download_bytes')
                    handle.write(block)
                provider = {key: response.headers[key] for key in ('Last-Modified', 'ETag', 'Content-Type') if key in response.headers}
            handle.flush()
            if digest(temporary) != expected_sha256:
                raise ValueError(f'Source checksum mismatch: {path}')
            try:
                os.link(temporary, path)
            except FileExistsError:
                if not path.is_file() or digest(path) != expected_sha256:
                    raise ValueError(f'Another collector published a different original: {path}') from None
                return path
            payload = {'schema': 'human.source.download.v1', 'url': url, 'path': str(path.resolve()),
                'sha256': expected_sha256, 'bytes': path.stat().st_size,
                'collected_at_utc': datetime.now(timezone.utc).isoformat(), 'provider_headers': provider}
            # A receipt cannot silently replace another collection's evidence.
            with receipt.open('x', encoding='utf-8') as destination:
                json.dump(payload, destination, indent=2, sort_keys=True)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def collect_assets(records, root, *, mode, byte_budget):
    """Collect/reuse explicitly pinned assets under one shared byte budget."""
    budget = {'downloads': len(records), 'bytes': byte_budget}
    receipts = []
    for record in records:
        path = (root / record['path']).resolve()
        expected = _pin(record['sha256'])
        obtain(path, record.get('url'), expected_sha256=expected, mode=mode, budget=budget)
        receipts.append({**record, 'path': str(path), 'sha256': expected, 'bytes': path.stat().st_size})
    return receipts
