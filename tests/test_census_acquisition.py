"""Five-source empty-store acquisition uses tiny synthetic payloads, never providers."""
import hashlib
import io
import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box
import yaml

from human.utils.downloads import obtain
from human.pipelines.population.static.census import prepare_census_sources
from human.pipelines.population.static.sources import read_sources
from tests.test_pipeline import setup_pipeline


def _parquet(frame):
    stream = io.BytesIO()
    frame.to_parquet(stream, index=False)
    return stream.getvalue()


def five_sources(tmp_path, monkeypatch):
    payloads = {}
    def source(name, body, country='us'):
        host = 'www2.census.gov' if country == 'us' else 'www12.statcan.gc.ca'
        url = 'https://' + host + '/fixture/' + name
        payloads[url] = body
        return {'path': 'originals/' + name, 'url': url, 'sha256': hashlib.sha256(body).hexdigest()}
    us = []
    for i, state in enumerate(('53', '41')):
        x = -123.008 + i * .004
        raw = source(state + '.parquet', _parquet(gpd.GeoDataFrame(
            {'GEOID20': [state + 'fixture'], 'STATEFP20': [state], 'POP20': [100. + i]},
            geometry=[box(x, 47.995, x + .002, 48.005)], crs=4326)))
        us.append({'geography': raw['path'], 'geography_url': raw['url'], 'geography_sha256': raw['sha256'],
            'jurisdiction': 'US-' + ('WA' if state == '53' else 'OR'), 'state_fips': state, 'population_column': 'POP20'})
    ca = source('ca.parquet', _parquet(gpd.GeoDataFrame({'DGUID': ['2021S051259000001'], 'PRUID': ['59']},
        geometry=[box(-122.999, 47.995, -122.997, 48.005)], crs=4326)), 'ca')
    pop = source('ca.csv', b'DGUID,Population, 2021\n', 'ca')
    # Quoted count-column name is the actual fixture CSV header.
    payloads[pop['url']] = b'DGUID,"Population, 2021"\n2021S051259000001,200\n'
    pop['sha256'] = hashlib.sha256(payloads[pop['url']]).hexdigest()
    states = source('states.parquet', _parquet(gpd.GeoDataFrame({'STATEFP': ['53', '41']},
        geometry=[box(-125, 47, -122, 49), box(-124, 46, -121, 48.5)], crs=4326)))
    spec = {'max_downloads': 5, 'max_download_bytes': sum(map(len, payloads.values())),
        'us': us, 'canada': [{'jurisdiction': 'CA-BC', 'province_code': '59',
            'geography': ca['path'], 'geography_url': ca['url'], 'geography_sha256': ca['sha256'],
            'population': pop['path'], 'population_url': pop['url'], 'population_sha256': pop['sha256'],
            'population_id': 'DGUID', 'population_column': 'Population, 2021'}],
        'jurisdiction_inventories': [{**states, 'id_column': 'STATEFP', 'prefix': 'US-'}]}
    calls = []
    class Response:
        status_code = 200
        headers = {'Content-Type': 'application/octet-stream'}
        def __init__(self, url): self.url = url
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield payloads[self.url]
    def get(url, **kwargs):
        assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
        calls.append(url)
        return Response(url)
    monkeypatch.setattr('requests.get', get)
    return spec, calls


def test_empty_store_collects_five_pinned_sources_including_inventory(tmp_path, monkeypatch):
    spec, calls = five_sources(tmp_path, monkeypatch)
    path = prepare_census_sources(workspace=tmp_path, source_extent=(-123.02,47.98,-122.98,48.02),
        specification=spec, mode='live')
    bundle = read_sources(path)
    assert sorted(bundle.geographies.population) == [100.,101.,200.]
    assert len(calls) == 5 and len(list((tmp_path/'originals').glob('*.receipt.json'))) == 5
    assert bundle.metadata['missing_jurisdictions'] == []
    assert all(row['url'] and row['bytes'] > 0 for row in bundle.metadata['inputs'])
    before = {p:p.read_bytes() for p in (tmp_path/'originals').iterdir()}
    assert prepare_census_sources(workspace=tmp_path, source_extent=(-123.02,47.98,-122.98,48.02),
        specification=spec, mode='live') == path
    assert len(calls) == 5 and all(p.read_bytes() == content for p,content in before.items())
    (tmp_path/spec['us'][0]['geography']).write_bytes(b'corrupt original')
    with pytest.raises(ValueError, match='checksum'):
        prepare_census_sources(workspace=tmp_path, source_extent=(-123.02,47.98,-122.98,48.02), specification=spec)
    assert len(calls) == 5


@pytest.mark.parametrize('status,budget,pin,match', [(403,100,'good','403'), (302,100,'good','302'),
    (200,2,'good','max_download_bytes'), (200,100,'bad','checksum')])
def test_failed_download_cleans_partial_and_leaves_original_absent(tmp_path, monkeypatch, status,budget,pin,match):
    body=b'bounded source'
    class Response:
        status_code=status
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,size):yield body
    monkeypatch.setattr('requests.get',lambda *a,**k:Response())
    with pytest.raises(ValueError,match=match) as error:
        obtain(tmp_path/'source.zip','https://www2.census.gov/fixture.zip',mode='live',
            expected_sha256=hashlib.sha256(body).hexdigest() if pin=='good' else '0'*64,
            budget={'downloads':1,'bytes':budget})
    assert not list(tmp_path.iterdir())
    if status==403:
        assert str(tmp_path/'source.zip') in str(error.value) and hashlib.sha256(body).hexdigest() in str(error.value)
        assert 'unchanged provider original' in str(error.value)


def test_limits_and_official_host_are_checked_before_http(tmp_path,monkeypatch):
    monkeypatch.setattr('requests.get',lambda *a,**k:pytest.fail('HTTP called'))
    pin='0'*64
    for options,match in [({'expected_sha256':None},'SHA256'),
        ({'budget':{'downloads':0,'bytes':100}},'max_downloads'),
        ({'allowed_hosts':{'www12.statcan.gc.ca'}},'official HTTPS')]:
        kwargs={'expected_sha256':pin,'budget':{'downloads':1,'bytes':100},**options}
        with pytest.raises(ValueError,match=match):obtain(tmp_path/'new.zip','https://www2.census.gov/fixture.zip',mode='live',**kwargs)
    assert not list(tmp_path.iterdir())


def test_missing_manifest_falls_back_but_corrupt_present_bundle_does_not(tmp_path,monkeypatch):
    recipe,config=setup_pipeline(tmp_path)
    spec,calls=five_sources(tmp_path,monkeypatch)
    specpath=tmp_path/'five.json';specpath.write_text(json.dumps(spec))
    pointer=tmp_path/'manifest-pointer.txt';pointer.write_text(str(tmp_path/'missing-bundle.json'))
    recipe['population']={'manifest_pointer':str(pointer),'specification':str(specpath)}
    config.write_text(yaml.safe_dump(recipe))
    from human import get_human_data
    result=get_human_data(workspace=tmp_path)
    assert len(calls)==5 and set(result.validation)=={'static.parquet','dynamic-calendar.parquet','static-native.parquet'}
    corrupt=tmp_path/'missing-bundle.json';corrupt.write_text('not valid JSON')
    calls.clear()
    with pytest.raises(ValueError):get_human_data(workspace=tmp_path)
    assert not calls and not list((tmp_path/'work').iterdir())


def test_packaged_census_recipe_matches_checkout_and_has_all_five_pins():
    from importlib.resources import files
    spec=json.loads(files('human').joinpath('resources/config/census-sources.v1.json').read_text())
    entries=[(e,k) for country in ('us','canada') for e in spec[country] for k in ('geography','population') if k in e]
    pins=[e[k+'_sha256'] for e,k in entries]+[e['sha256'] for e in spec['jurisdiction_inventories']]
    assert len(pins)==5 and all(len(pin)==64 for pin in pins) and spec['max_downloads']==5
    assert spec['cross_border_overlap']=='retain_distinct_national_counts'
    for e,k in entries:assert not Path(e[k]).is_absolute() and e[k+'_url'].startswith('https://')


def test_orphan_receipt_is_preserved_before_any_request(tmp_path,monkeypatch):
    receipt=tmp_path/'original.zip.receipt.json';receipt.write_text('unique retained receipt')
    monkeypatch.setattr('requests.get',lambda *a,**kw:pytest.fail('HTTP called'))
    with pytest.raises(FileExistsError,match='receipt already exists'):
        obtain(tmp_path/'original.zip','https://www2.census.gov/original.zip',mode='live',
            expected_sha256='0'*64,budget={'downloads':1,'bytes':100})
    assert receipt.read_text()=='unique retained receipt' and not (tmp_path/'original.zip').exists()


@pytest.mark.parametrize('kind', ['geography', 'population'])
def test_verified_local_canadian_originals_precede_network_and_download_budgets(tmp_path, monkeypatch, kind):
    from importlib.resources import files
    spec=json.loads(files('human').joinpath('resources/config/census-sources.v1.json').read_text())
    entry=spec['canada'][0]
    assert Path(entry[kind]).parent.as_posix()=='data/raw/statcan/2021'
    path=tmp_path/entry[kind];path.parent.mkdir(parents=True)
    body=b'synthetic Canadian original';path.write_bytes(body)
    pin=hashlib.sha256(body).hexdigest();budget={'downloads':0,'bytes':0}
    monkeypatch.setattr('requests.get',lambda *a,**kw:pytest.fail('Local input attempted HTTP'))
    assert obtain(path,entry[kind+'_url'],expected_sha256=pin,mode='live',budget=budget)==path
    assert budget=={'downloads':0,'bytes':0} and path.read_bytes()==body
    assert not path.with_name(path.name+'.receipt.json').exists()  # No invented download timestamp.


def test_mismatching_local_original_is_preserved_without_network(tmp_path, monkeypatch):
    path=tmp_path/'original.zip';path.write_bytes(b'changed provider bytes')
    monkeypatch.setattr('requests.get',lambda *a,**kw:pytest.fail('Mismatching original attempted HTTP'))
    with pytest.raises(ValueError) as error:
        obtain(path,'https://www12.statcan.gc.ca/original.zip',expected_sha256='0'*64,
            mode='live',budget={'downloads':1,'bytes':100})
    assert 'expected '+('0'*64) in str(error.value) and 'got '+hashlib.sha256(path.read_bytes()).hexdigest() in str(error.value)
    assert 'preserved' in str(error.value) and path.read_bytes()==b'changed provider bytes'


def test_missing_offline_original_gives_exact_placement_and_pin_without_writes(tmp_path, monkeypatch):
    from human.utils.downloads import MissingHumanSources
    path=tmp_path/'data/raw/statcan/2021/lda_000b21a_e.zip'
    monkeypatch.setattr('requests.get',lambda *a,**kw:pytest.fail('Offline source attempted HTTP'))
    with pytest.raises(MissingHumanSources) as error:
        obtain(path,'https://www12.statcan.gc.ca/original.zip',expected_sha256='0'*64,
            mode='offline',budget={'downloads':0,'bytes':0})
    assert str(path) in str(error.value) and 'Expected SHA256: '+('0'*64) in str(error.value)
    assert 'unchanged provider original' in str(error.value) and not path.parent.exists()
