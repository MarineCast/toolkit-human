"""Physical port identity, original geometry, preservation and pipeline integration."""
import json

import geopandas as gpd
import pyarrow.parquet as pq
import pytest
import yaml
from shapely.geometry import Point

from human import get_human_data
from human.pipelines.delivery.finalize import digest, native_payload, read_catalog
from human.pipelines.delivery.validation import validate_delivery
from human.pipelines.native.ports import COUNT, port_features, migrate_delivery
from tests.test_pipeline import setup_pipeline


def source_entry(tmp_path):
    path = tmp_path/'ports.parquet'
    # Different NGA facilities sharing a point must remain distinct, including a
    # source point coincident with the existing synthetic launch/destination.
    frame = gpd.GeoDataFrame({'portNumber': [1, 2, 3], 'portName': ['One', 'Two', 'Outside'],
                             'harborUse': ['UNK', None, 'FISH'], 'globalId': ['a', 'b', 'c']},
                            geometry=[Point(-123, 48), Point(-123, 48), Point(-130, 55)], crs=4326)
    frame.to_parquet(path)
    table = pq.read_table(path)
    provenance = {'receipt': {'source_id': 'nga_world_port_index', 'retrieved_at_utc': '2026-10-14T00:00:00Z'},
                  'source': {'license': 'Synthetic local fixture rights', 'attribution': 'NGA fixture'}}
    pq.write_table(table.replace_schema_metadata({**table.schema.metadata, b'governance_original': json.dumps(provenance).encode()}), path)
    return {'path': str(path), 'sha256': digest(path), 'source_feature_ids': ['1', '2', '3']}


def test_source_ids_original_geometry_nulls_and_no_proximity_merge(tmp_path):
    entry = source_entry(tmp_path)
    features, receipt = port_features(entry)
    assert [f['properties']['record_id'] for f in features] == ['nga_wpi:1', 'nga_wpi:2', 'nga_wpi:3']
    assert features[0]['geometry'] == features[1]['geometry'] == {'type': 'Point', 'coordinates': [-123., 48.]}
    assert features[1]['properties']['attributes']['harborUse'] is None
    assert receipt['original_provenance']['source']['license'] == 'Synthetic local fixture rights'
    with pytest.raises(ValueError, match='missing'):
        port_features({**entry, 'source_feature_ids': ['missing']})
    with pytest.raises(ValueError, match='Duplicate selected'):
        port_features({**entry, 'source_feature_ids': ['1', '1']})
    with pytest.raises(ValueError, match='checksum'):
        port_features({**entry, 'sha256': '0'*64})


def test_narrow_migration_preserves_all_existing_values_and_outside_points(tmp_path):
    setup_pipeline(tmp_path)
    result = get_human_data(workspace=tmp_path)
    before = {name: pq.read_table(result.directory/name) for name in result.validation}
    entry = source_entry(tmp_path)
    migrate_delivery(result.directory, entry, metadata_dir=tmp_path/'port-receipt')
    assert validate_delivery(result.directory)
    assert {p.name for p in result.directory.iterdir()} == set(before)
    for name in ('static.parquet', 'dynamic-calendar.parquet'):
        old, new = before[name], pq.read_table(result.directory/name)
        assert new.select(old.column_names).equals(old, check_metadata=False)
    old, new = before['static-native.parquet'], pq.read_table(result.native)
    assert new.slice(0, old.num_rows).equals(old, check_metadata=False)
    assert new.num_rows == old.num_rows+3
    assert sum(pq.read_table(result.static)[COUNT].to_pylist()) == 2
    assert 'nga_wpi:3' in {f['properties']['record_id'] for f in native_payload(result.native)['features']}
    assert not list(result.directory.parent.glob('.Human-ports-*'))
    assert not (result.directory.parent/'.Human.human.lock').exists()
    assert entry['sha256'] == digest(entry['path'])
    with pytest.raises(ValueError, match='already present'):
        migrate_delivery(result.directory, entry, metadata_dir=tmp_path/'repeat-receipt')


def test_supported_pipeline_configured_port_source_offline(tmp_path):
    recipe, config = setup_pipeline(tmp_path)
    recipe['native']['ports'] = source_entry(tmp_path)
    config.write_text(yaml.safe_dump(recipe))
    result = get_human_data(workspace=tmp_path, mode='offline')
    assert result.validation['static.parquet']['columns'] == 67
    static = pq.read_table(result.static)
    assert static[COUNT].to_pylist() == [2]
    catalog = read_catalog(result.directory)
    native = next(p for p in catalog['products'] if p['schema'] == 'human.native_features.v1')
    assert native['groups']['ports']['records'] == 3
    assert native['groups']['places']['records'] == 1
    assert native['groups']['ports']['regional_completeness'] == 'partial'


def test_migration_readback_failure_restores_current(tmp_path, monkeypatch):
    setup_pipeline(tmp_path)
    result = get_human_data(workspace=tmp_path)
    before = {p.name: digest(p) for p in result.directory.iterdir()}
    import human.pipelines.delivery.validation as validation
    original = validation.validate_delivery
    calls = 0
    def reject_published(root, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ValueError('Synthetic published readback failure')
        return original(root, **kwargs)
    monkeypatch.setattr(validation, 'validate_delivery', reject_published)
    with pytest.raises(ValueError, match='published readback'):
        migrate_delivery(result.directory, source_entry(tmp_path), metadata_dir=tmp_path/'port-receipt')
    assert {p.name: digest(p) for p in result.directory.iterdir()} == before
    assert not (result.directory.parent/'.Human.human.lock').exists()
