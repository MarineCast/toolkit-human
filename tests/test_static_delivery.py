import hashlib
import json

import geopandas as gpd
import h3
import pandas as pd
import pytest
from shapely.geometry import box

from human.pipelines.population.static import PopulationSources
from human.pipelines.population.static.sources import write_sources
from human.utils.study import load_static_study
from human.pipelines.population.delivery import export_static_study


def fixture_study(tmp_path):
    data = tmp_path / 'Data'
    data.mkdir()
    cell = h3.latlng_to_cell(48, -123, 7)
    def artifact(name, payload):
        (data / name).write_bytes(payload)
        return {'relative_path': 'Data/' + name, 'sha256': hashlib.sha256(payload).hexdigest()}
    mask = artifact('mask.parquet', b'pinned fixture mask')
    member = {**artifact('r7.txt', (cell+'\n').encode()), 'role': 'water_reporting', 'resolution': 7, 'count': 1}
    manifest = {'mask_revision': 'fixture', 'mask_sha256': mask['sha256'],
        'mask_hash_policy': 'sha256_exact_file_bytes', 'memberships': [member]}
    binding = artifact('manifest.json', json.dumps(manifest).encode())
    cfg = {'schema_version': 1, 'domain': {'status': 'approved', 'approval': {'statement': 'fixture'}},
        'storage': {'data_root': 'Data'}, 'time': {'start': '2021-07-04', 'end_exclusive': '2021-07-06'},
        'grid_registry': {**manifest, 'status': 'validated', 'mask_relative_path': mask['relative_path'],
            'manifest_relative_path': binding['relative_path'], 'manifest_sha256': binding['sha256']}}
    path = tmp_path / 'study.json'
    path.write_text(json.dumps(cfg))
    return path, cell


def test_exact_registry_and_tamper(tmp_path):
    path, cell = fixture_study(tmp_path)
    assert load_static_study(path)['cells'] == [cell]
    (tmp_path / 'Data/r7.txt').write_text(cell.upper()+'\n')
    with pytest.raises(ValueError, match='checksum'):
        load_static_study(path)


def test_static_delivery_no_proxies_and_compatible_binding(tmp_path):
    path, cell = fixture_study(tmp_path)
    sources = PopulationSources(gpd.GeoDataFrame({'source_id': ['fixture'], 'country': ['US'],
        'vintage': [2020], 'jurisdiction': ['US-WA'], 'population': [100.]},
        geometry=[box(-123.01,47.99,-122.99,48.01)], crs=4326),
        gpd.GeoDataFrame(geometry=[box(-130,40,-115,56)], crs=4326), {'synthetic': True})
    manifest = write_sources(sources, tmp_path/'sources')
    retained = tmp_path / 'old.parquet'
    pd.DataFrame({'h3_index': [cell], 'h3_resolution': [7],
        'boat_launch_access__BOAT_LAUNCH_COUNT': [None], 'population__POPULATION': [999.],
        'land_opportunity__score': [1.]}).to_parquet(retained)
    output = tmp_path / 'final'
    result = export_static_study(study_config=path, workspace=tmp_path/'work', source_manifest=manifest,
        output_dir=output, retained_static=retained, as_of='2021-07-04')
    catalog = json.loads(result.read_text())
    assert len(catalog['products']) == 2
    assert {p.name for p in output.iterdir()} == {'static.parquet','dynamic-calendar.parquet'}
    assert not result.is_relative_to(output)
    static = pd.read_parquet(output/'static.parquet')
    assert static.h3_index.tolist() == [cell]
    assert {'population_count_collected', 'population_density_per_km2_collected',
        'population_within_25km_collected', 'population_within_50km_collected',
        'population_within_100km_collected'} <= set(static.columns)
    assert static.population_within_100km_collected.iloc[0] == pytest.approx(100.)
    for radius in (25, 50, 100):
        assert f'population_within_{radius}km' not in static
    assert not any('250km' in column for column in static)
    assert not any(column.startswith('population_0_') for column in static)
    assert static['boat_launch_access__BOAT_LAUNCH_COUNT'].isna().all()
    assert 'population__POPULATION' not in static
    assert not any('opportunity' in c for c in static)
    meta = next(product for product in catalog['products'] if product['schema']=='human.study.static.v1')
    assert meta['schema'] == 'human.study.static.v1'
    assert meta['population']['radii_km'] == [25., 50., 100.]
    assert meta['population']['method_parameters']['buffer_tolerance_m'] == 0.25
    assert not any('250km' in field for field in meta['population']['fields'])
    for radius in (25, 50, 100):
        assert f'population_within_{radius}km' not in meta['population']['fields']
    assert meta['population']['distance_delivery']['fields'] == [f'population_within_{r}km_collected' for r in (25, 50, 100)]
    from human.pipelines.population.collected import DISPLAY_LABELS, PUBLIC_BASES
    assert not set(PUBLIC_BASES) & set(static)
    assert set(meta['population']['fields']) == set(DISPLAY_LABELS)
    assert {k: v['label'] for k, v in meta['population']['fields'].items()} == DISPLAY_LABELS
    assert {v['family'] for v in meta['population']['fields'].values()} == {'Population'}
    assert catalog['default_population']['label'] == 'Population Within 100km'
    assert meta['artifact'] == catalog['products'][0]['artifact']
    assert meta['artifact']['sha256'] == hashlib.sha256((output/'static.parquet').read_bytes()).hexdigest()
    calendar = pd.read_parquet(output/'dynamic-calendar.parquet')
    assert len(calendar) == 10
    assert not any('effort' in c for c in calendar)


def test_collected_default_exports_partial_sources_without_nulling_sum(tmp_path):
    path,cell=fixture_study(tmp_path)
    sources=PopulationSources(gpd.GeoDataFrame({'source_id':['fixture'],'country':['US'],
        'vintage':[2020],'jurisdiction':['US-WA'],'population':[100.]},
        geometry=[box(-123.01,47.99,-122.99,48.01)],crs=4326),None,{'synthetic':True})
    manifest=write_sources(sources,tmp_path/'sources')
    output=tmp_path/'collected'
    result=export_static_study(study_config=path,workspace=tmp_path/'work',source_manifest=manifest,
        output_dir=output,as_of='2021-07-04')
    table=pd.read_parquet(output/'static.parquet')
    assert 'population_within_100km' not in table
    assert table.population_within_100km_collected.iloc[0]==pytest.approx(100.)
    assert table.population_within_100km_collected__zero_imputed.iloc[0]
    assert 'population_within_100km_collected__coverage_status' not in table
    assert table.population_within_100km__coverage_status.iloc[0]=='partial'
    assert json.loads(result.read_text())['default_population']['field']=='population_within_100km_collected'
    assert 'population_within_25km_collected' in table and 'population_count_collected' in table
    assert 'population_count' not in table and 'population_density_per_km2' not in table
    assert table.population_within_100km__unverified_geographic_support.iloc[0]
    assert table.population_within_100km__available_source_subtotal.iloc[0] == pytest.approx(100.)
