"""Physical port identity, original geometry, preservation and pipeline integration."""
import json

import geopandas as gpd
import pyarrow.parquet as pq
import pytest
import yaml
from shapely.geometry import Point

from human import get_human_data
from human.pipelines.delivery.finalize import digest, read_catalog
from human.pipelines.delivery.validation import validate_delivery
from human.pipelines.native.ports import port_features, migrate_delivery
from tests.test_pipeline import setup_pipeline

COUNT = 'has_port'


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


def test_narrow_migration_preserves_all_existing_values_and_source_selection(tmp_path):
    setup_pipeline(tmp_path)
    result = get_human_data(workspace=tmp_path)
    before = {name: pq.read_table(result.directory/name) for name in result.validation}
    entry = source_entry(tmp_path)
    migrate_delivery(result.directory, entry, metadata_dir=tmp_path/'port-receipt')
    assert validate_delivery(result.directory)
    assert {p.name for p in result.directory.iterdir()} == set(before)
    for name in ('static.parquet', 'dynamic-calendar.parquet'):
        old, new = before[name], pq.read_table(result.directory/name)
        columns=[c for c in old.column_names if name!='static.parquet' or c!=COUNT]
        assert new.select(columns).equals(old.select(columns), check_metadata=False)
    assert sum(pq.read_table(result.static)[COUNT].to_pylist()) == 1
    info = next(p for p in read_catalog(result.directory)['products'] if p['schema']=='human.study.static.v6')
    assert info['ports']['selected_source_feature_ids'] == ['1', '2', '3']
    assert not (result.directory/'static-native.parquet').exists()
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
    assert result.validation['static.parquet']['columns'] == 14
    static = pq.read_table(result.static)
    assert static[COUNT].to_pylist() == [1]
    catalog = read_catalog(result.directory)
    info = next(p for p in catalog['products'] if p['schema'] == 'human.study.static.v6')
    assert info['ports']['selected_source_feature_ids'] == ['1', '2', '3']
    assert info['ports']['sha256'] == recipe['native']['ports']['sha256']
    assert len(catalog['products']) == 2


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


def test_checksum_valid_port_count_tamper_is_rejected_without_native_export(tmp_path):
    recipe, config = setup_pipeline(tmp_path)
    recipe['native']['ports'] = source_entry(tmp_path)
    config.write_text(yaml.safe_dump(recipe))
    result = get_human_data(workspace=tmp_path, mode='offline')
    table = pq.read_table(result.static)
    column = table.column_names.index(COUNT)
    import pyarrow as pa
    pq.write_table(table.set_column(column, table.schema.field(COUNT), pa.array([0], type=pa.int64())), result.static)
    catalog = json.loads(result.receipt.read_text())
    next(p for p in catalog['products'] if p['schema']=='human.study.static.v6')['artifact']['sha256'] = digest(result.static)
    result.receipt.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match='transformation|Port counts differ'):
        validate_delivery(result.directory)
