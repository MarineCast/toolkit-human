import h3
import pandas as pd
import pytest
from human.pipelines.native.summary import combine_frames


def test_union_preserves_absent_and_unknown_values():
    a, b = h3.latlng_to_cell(48, -123, 7), h3.latlng_to_cell(49, -123, 7)
    frames = {'population': pd.DataFrame({'H3_INDEX': [a], 'H3_RESOLUTION': [7], 'POPULATION': [0.0]}), 'access': pd.DataFrame({'H3_INDEX': [b], 'H3_RESOLUTION': [7], 'COUNT': [None]})}
    result, fields = combine_frames(frames)
    indexed = result.set_index('H3_INDEX')
    assert indexed.loc[a, 'population__POPULATION'] == 0
    assert pd.isna(indexed.loc[b, 'population__POPULATION'])
    assert pd.isna(indexed.loc[b, 'access__COUNT'])
    assert fields['population__POPULATION']['component'] == 'population'


@pytest.mark.parametrize('resolution,duplicate', [(6, False), (7, True)])
def test_rejects_incompatible_identity(resolution, duplicate):
    cell = h3.latlng_to_cell(48, -123, resolution)
    frame = pd.DataFrame({'H3_INDEX': [cell], 'H3_RESOLUTION': [resolution]})
    if duplicate:
        frame = pd.concat([frame, frame])
    with pytest.raises(ValueError):
        combine_frames({'access': frame})


def test_export_verifies_artifact_and_embeds_manifest(tmp_path):
    import hashlib
    import json
    import pyarrow.parquet as pq
    from human.pipelines.native.summary import export
    cell = h3.latlng_to_cell(48, -123, 7)
    artifact = tmp_path / 'boat_launch_access_h3_r7.parquet'
    pd.DataFrame({'H3_INDEX': [cell], 'H3_RESOLUTION': [7], 'COUNT': [2]}).to_parquet(artifact)
    manifest = tmp_path / 'manifest.json'
    record = {'stage': 'build', 'source_completeness': 'partial', 'sources': [{'license': 'ODbL'}], 'artifacts': [{'path': str(artifact), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
    manifest.write_text(json.dumps(record))
    output = tmp_path / 'matrix.parquet'
    export([manifest], output)
    table = pq.read_table(output)
    assert table['boat_launch_access__COUNT'].to_pylist() == [2]
    metadata = json.loads(table.schema.metadata[b'human_static_matrix'])
    assert metadata['sources'][0]['native_manifest']['sources'][0]['license'] == 'ODbL'
    artifact.write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        export([manifest], tmp_path / 'bad.parquet')


def test_cli_and_places_record_counts(tmp_path):
    import hashlib
    import json
    import pyarrow.parquet as pq
    from human.pipelines.native.summary import main
    catalog = tmp_path / 'places_of_interest.json'
    catalog.write_text(json.dumps([{'latitude': 48, 'longitude': -123, 'destination_score': 99}, {'latitude': 48, 'longitude': -123, 'destination_score': 7}]))
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'stage': 'build', 'artifacts': [{'path': str(catalog), 'sha256': hashlib.sha256(catalog.read_bytes()).hexdigest()}]}))
    output = tmp_path / 'out.parquet'
    assert main(['--manifest', str(manifest), '--output', str(output)]) == 0
    table = pq.read_table(output)
    assert table['places__CATALOG_RECORD_COUNT'].to_pylist() == [2]
    assert not any('score' in name for name in table.column_names)


def test_supplemental_input_checksum_is_required(tmp_path):
    import json
    from human.pipelines.native.summary import export
    source = tmp_path / 'external-water.parquet'
    source.write_bytes(b'changed')
    inventory = tmp_path / 'inputs.json'
    inventory.write_text(json.dumps({'records': [{'path': str(source), 'sha256': 'wrong'}]}))
    with pytest.raises(ValueError, match='Input inventory checksum'):
        export([], tmp_path / 'out.parquet', inventory)


def test_static_population_v1_adapter_retains_coverage_and_units(tmp_path):
    from human import get_population
    from human.pipelines.population.static import PopulationSources
    from human.pipelines.native.summary import export
    import geopandas as gpd
    from shapely.geometry import box
    import pyarrow.parquet as pq
    import json
    cell = h3.latlng_to_cell(48, -123, 7)
    sources = PopulationSources(gpd.GeoDataFrame({'source_id':['synthetic'], 'country':['US'],
        'jurisdiction':['synthetic'], 'vintage':[2020], 'population':[10.]},
        geometry=[box(-123.01,47.99,-122.99,48.01)],crs=4326))
    result = get_population(target_h3=[cell],sources=sources,workspace=tmp_path,allow_partial=True)
    result.to_parquet(tmp_path/'population.parquet')
    output=export([tmp_path/'population.metadata.json'],tmp_path/'static.parquet')
    table=pq.read_table(output)
    assert table['population_static_v1__population_count'].to_pylist()==[None]
    assert table['population_static_v1__population_count__coverage_status'].to_pylist()==['partial']
    metadata=json.loads(table.schema.metadata[b'human_static_matrix'])
    assert metadata['fields']['population_static_v1__population_density_per_km2']['units']=='persons/km2'
    assert metadata['sources'][0]['native_manifest']['method']=='area_weighted_original_polygons.v1.3'
