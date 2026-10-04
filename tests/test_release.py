import json
from concurrent.futures import ThreadPoolExecutor

import h3
import pandas as pd
import pytest

from human.release import audit, publish_static, read_json, verify_static
from human.static_matrix import _checksum


@pytest.fixture
def native(tmp_path):
    artifact = tmp_path / 'population_context_h3_r7.parquet'
    pd.DataFrame({'H3_INDEX': [h3.latlng_to_cell(x, -123, 7) for x in (48, 49)],
                  'H3_RESOLUTION': [7, 7], 'POPULATION': [0.0, None]}).to_parquet(artifact)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'stage': 'build', 'source_completeness': 'partial',
        'known_limitations': ['Fixture: unknown population remains null'],
        'artifacts': [{'path': str(artifact), 'rows': 2, 'sha256': _checksum(artifact)}]}))
    return manifest, artifact


def test_snapshot_roundtrip_immutability_and_source_independence(native, tmp_path):
    manifest, artifact = native
    output = tmp_path / 'release'
    publish_static([manifest], output, code_sha='a' * 40)
    before = (output / 'release.json').read_bytes()
    report = verify_static(output)
    assert report['qualification'] == 'research_only'
    assert report['fields']['population__POPULATION'] == {'null_count': 1, 'zero_count': 1}
    artifact.unlink()
    manifest.unlink()
    assert verify_static(output) == report
    with pytest.raises(FileExistsError):
        publish_static([manifest], output, code_sha='a' * 40)
    assert (output / 'release.json').read_bytes() == before
    assert not list(tmp_path.glob('.*.lock'))
    assert not list(tmp_path.glob('.*.staging-*'))


def test_audit_missing_and_checksum_mismatch(native):
    manifest, artifact = native
    artifact.write_bytes(b'changed')
    result = audit([manifest])
    assert not result['integrity_passed']
    assert 'checksum_mismatch' in result['manifests'][0]['records'][0]['problems']
    artifact.unlink()
    assert audit([manifest])['manifests'][0]['records'][0]['problems'] == ['missing_file']


def test_failed_publish_leaves_no_visible_release(native, tmp_path, monkeypatch):
    import human.release as module
    manifest, _ = native
    output = tmp_path / 'release'
    def fail(*args, **kwargs):
        raise RuntimeError('injected post-export validation failure')
    monkeypatch.setattr(module, 'verify_static', fail)
    with pytest.raises(RuntimeError, match='injected'):
        publish_static([manifest], output, code_sha='a' * 40)
    assert not output.exists()
    assert not list(tmp_path.glob('.release*'))


def test_concurrent_publish_has_exactly_one_winner(native, tmp_path):
    manifest, _ = native
    output = tmp_path / 'release'
    def attempt(_):
        try:
            publish_static([manifest], output, code_sha='a' * 40)
            return True
        except FileExistsError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]
    verify_static(output)


@pytest.mark.parametrize('value', ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_reject_ambiguous_json(tmp_path, value):
    path = tmp_path / 'bad.json'
    path.write_text(value)
    with pytest.raises(ValueError):
        read_json(path)


def test_tamper_detection(native, tmp_path):
    manifest, _ = native
    output = tmp_path / 'release'
    publish_static([manifest], output, code_sha='a' * 40)
    (output / 'native/0/0/population_context_h3_r7.parquet').write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        verify_static(output)


def test_relative_native_paths_and_input_rows(native, tmp_path):
    manifest, artifact = native
    data = read_json(manifest)
    data['artifacts'][0]['path'] = artifact.name
    data['inputs'] = [dict(data['artifacts'][0], rows=3)]
    manifest.write_text(json.dumps(data))
    assert not audit([manifest])['integrity_passed']
    data['inputs'][0]['rows'] = 2
    manifest.write_text(json.dumps(data))
    output = tmp_path / 'release'
    publish_static([manifest], output, code_sha='a' * 40)
    verify_static(output)
