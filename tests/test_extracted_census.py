"""Small extracted-source fixtures; no official files or bulk source copies."""
from pathlib import Path
import hashlib,json,zipfile

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from human.pipelines.population.local_sources import register_extracted_sources,verify_provenance
from human.pipelines.population.tables import read_statcan_profile
from human.pipelines.population.static.census import prepare_census_sources
from human.pipelines.population.static.sources import read_sources
from tests.test_census_acquisition import five_sources
from tests.test_pipeline import setup_pipeline
import yaml


def extracted(tmp_path,year='2021'):
    geography=tmp_path/'downloads/boundaries';profile=tmp_path/'downloads/profile'
    geography.mkdir(parents=True);profile.mkdir()
    gpd.GeoDataFrame({'DGUID':['2021S051259000001'],'DAUID':['59000001'],'PRUID':['59']},
        geometry=[box(-123.005,47.995,-122.995,48.005)],crs=4326).to_crs(3347).to_file(geography/'lda_000b21a_e.shp')
    (geography/'lda_000b21a_e.xml').write_text('<metadata>Census 2021 cartographic DA</metadata>')
    data=pd.DataFrame({'CENSUS_YEAR':[year,year,year],'DGUID':['2021A000011124','2021S051259000001','2021S051259000001'],
        'GEO_LEVEL':['Country','Dissemination area','Dissemination area'],'CHARACTERISTIC_ID':[1,1,2],
        'CHARACTERISTIC_NAME':['Population, 2021']*3,'C1_COUNT_TOTAL':['1000','12','99']})
    data.to_csv(profile/'98-401-X2021006_English_CSV_data_BritishColumbia.csv',index=False)
    (profile/'README_meta.txt').write_text('Synthetic fixture; never official source data')
    return geography,profile


def test_extracted_registration_moves_one_copy_and_binds_every_companion(tmp_path,monkeypatch):
    geography,profile=extracted(tmp_path)
    monkeypatch.setattr('requests.get',lambda *a,**k:pytest.fail('Local registration attempted HTTP'))
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for source in (geography,profile) for p in source.iterdir()}
    root=tmp_path/'workspace';path=register_extracted_sources(geography,profile,workspace=root)
    assert not geography.exists() and not profile.exists()
    spec=json.loads(path.read_text());entry=spec['canada'][0]
    assert entry['population_format']=='statcan_profile_2021' and 'population_url' not in entry and 'geography_url' not in entry
    for kind in ('geography','population'):
        provenance={**entry[kind+'_provenance'],'receipt':str(root/entry[kind+'_provenance']['receipt'])}
        receipt=verify_provenance(provenance,root/entry[kind],entry[kind+'_sha256'])
        assert receipt['original_provider_zip_available'] is False
        assert all(before[Path(m['relative_path']).name]==m['sha256'] for m in receipt['members'])
    spec['us']=[];spec['jurisdiction_inventories']=[]
    source=prepare_census_sources(workspace=root,source_extent=(-123.01,47.99,-122.99,48.01),specification=spec)
    bundle=read_sources(source)
    assert bundle.geographies.population.tolist()==[12]
    assert len([p for p in bundle.metadata['inputs'] if p['role']=='extracted_source_receipt'])==2
    assert not list((root/'data/raw/statcan/2021').glob('*.zip'))
    companion=root/entry['geography'];companion=companion.with_suffix('.dbf')
    companion.write_bytes(companion.read_bytes()+b'tamper')
    with pytest.raises(ValueError,match='member missing or checksum'):
        prepare_census_sources(workspace=root,source_extent=(-123.01,47.99,-122.99,48.01),specification=spec)


def test_explicit_profile_csv_and_original_zip_stream_identical_rows(tmp_path):
    _,profile=extracted(tmp_path)
    source=next(profile.glob('*.csv'));archive=tmp_path/'original.zip'
    with zipfile.ZipFile(archive,'w') as z:z.write(source,arcname=source.name)
    a=read_statcan_profile(source);b=read_statcan_profile(archive)
    pd.testing.assert_frame_equal(a,b)
    assert a.DGUID.tolist()==['2021S051259000001'] and a['Population, 2021'].tolist()==['12']


def test_wrong_vintage_fails_before_any_move_or_recipe_write(tmp_path):
    geography,profile=extracted(tmp_path,year='2022');root=tmp_path/'workspace'
    with pytest.raises(ValueError,match='2021 comprehensive'):
        register_extracted_sources(geography,profile,workspace=root)
    assert geography.is_dir() and profile.is_dir() and not root.exists()


def test_local_recipe_preferred_but_present_corruption_never_falls_back(tmp_path,monkeypatch):
    recipe,config=setup_pipeline(tmp_path);spec,calls=five_sources(tmp_path,monkeypatch)
    local=tmp_path/'local.json';local.write_text(json.dumps(spec));official=tmp_path/'official.json';official.write_text('not JSON')
    recipe['population']={'source_manifest':str(tmp_path/'missing-normalized.json'),'specification':str(official),'local_specification':str(local)}
    config.write_text(yaml.safe_dump(recipe))
    from human import get_human_data
    assert len(get_human_data(workspace=tmp_path).validation)==3 and len(calls)==5
    calls.clear();local.write_text('corrupt local recipe')
    with pytest.raises(ValueError):get_human_data(workspace=tmp_path)
    assert not calls


def test_canonical_collision_preserves_both_inputs(tmp_path):
    geography,profile=extracted(tmp_path);root=tmp_path/'workspace'
    target=root/'data/raw/statcan/2021/lda_000b21a_e';target.mkdir(parents=True);(target/'user.txt').write_text('preserve')
    with pytest.raises(FileExistsError):register_extracted_sources(geography,profile,workspace=root)
    assert geography.is_dir() and profile.is_dir() and (target/'user.txt').read_text()=='preserve'
