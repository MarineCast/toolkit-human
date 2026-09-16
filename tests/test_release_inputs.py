import hashlib
import json

import pytest

from human.utils.release_inputs import resolve_sightings_release_artifact
from human.core.artifacts import checksum_path


def test_release_input_preserves_identity_and_checksum_guards(tmp_path):
    data = tmp_path / 'observations.bin'
    data.write_bytes(b'synthetic observation input')
    checksum = checksum_path(data)
    identity = {'artifacts': [{'dataset_id': 'observations', 'checksum': checksum}]}
    release_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    payload = {'schema_version': '1', 'release_id': release_id, 'identity': identity,
               'inventory': [{'dataset_id': 'observations', 'path': data.name, 'checksum': checksum}]}
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(payload))
    assert resolve_sightings_release_artifact(manifest, 'observations').path == data
    data.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='checksum mismatch'):
        resolve_sightings_release_artifact(manifest, 'observations')
    payload['inventory'][0]['checksum'] = checksum_path(data)
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='identity does not bind'):
        resolve_sightings_release_artifact(manifest, 'observations')
