import json
import h3
import pandas as pd
import pytest
from jsonschema import ValidationError
from human.release import publish_static, read_json, write_json
from human.static_matrix import _checksum
from human.static_profile import record_counts, verify_profile


def test_record_counts_preserve_valid_zero_and_unknown():
    native = pd.DataFrame({'H3_INDEX': ['a', 'b'], 'places__CATALOG_RECORD_COUNT': [0, None]})
    result = record_counts(native)
    assert result.place_catalog_record_count.tolist() == [0, pd.NA]
    assert result.place_catalog_record_count_status.tolist() == ['observed', 'unknown']
    assert result.boat_launch_catalog_record_count.isna().all()


@pytest.fixture
def profile(tmp_path):
    artifact = tmp_path / 'boat_launch_access_h3_r7.parquet'
    pd.DataFrame({'H3_INDEX': [h3.latlng_to_cell(48, -123, 7)], 'H3_RESOLUTION': [7],
                  'BOAT_LAUNCH_COUNT': [0]}).to_parquet(artifact)
    manifest = tmp_path / 'native.json'
    write_json(manifest, {'stage': 'build', 'sources': [{'name': 'synthetic', 'retrieved_at_utc': '2026-01-01T00:00:00Z', 'license': 'CC0', 'attribution': 'Synthetic fixture'}],
                         'artifacts': [{'path': str(artifact), 'sha256': _checksum(artifact)}]})
    output = tmp_path / 'release'
    publish_static([manifest], output, code_sha='a' * 40, record_count_profile=True)
    return output / 'catalog-record-counts.manifest.json'


def test_profile_roundtrip_and_strict_schema(profile):
    result = verify_profile(profile)
    assert result['contract_version'] == '0.1'
    result['unapproved_core_field'] = 'no'
    write_json(profile, result)
    with pytest.raises(ValidationError):
        verify_profile(profile)


def test_profile_rejects_rehashed_invalid_status(profile):
    result = read_json(profile)
    artifact = profile.parent / result['artifact']['path']
    frame = pd.read_parquet(artifact)
    frame['boat_launch_catalog_record_count_status'] = 'unknown'
    frame.to_parquet(artifact, index=False)
    result['artifact']['sha256'] = _checksum(artifact)
    write_json(profile, result)
    with pytest.raises(ValueError, match='count/status'):
        verify_profile(profile)


def test_profile_checks_native_mapping_not_just_checksums(profile):
    result = read_json(profile)
    artifact = profile.parent / result['artifact']['path']
    frame = pd.read_parquet(artifact)
    frame['boat_launch_catalog_record_count'] = pd.Series([100], dtype='Int64')
    frame.to_parquet(artifact, index=False)
    result['artifact']['sha256'] = _checksum(artifact)
    write_json(profile, result)
    with pytest.raises(AssertionError):
        verify_profile(profile)


@pytest.mark.parametrize('value', [-1, float('inf'), 1.5])
def test_invalid_counts_rejected(value):
    with pytest.raises(ValueError):
        record_counts(pd.DataFrame({'H3_INDEX': ['a'], 'places__CATALOG_RECORD_COUNT': [value]}))
