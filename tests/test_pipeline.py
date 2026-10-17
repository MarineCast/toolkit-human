"""End-to-end small source fixtures for the supported three-product pipeline."""
import copy
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml
from shapely.geometry import box

from human import get_human_data
from human.collection import MissingHumanSources, collect_assets
from human.final_delivery import digest, read_catalog, finalize_delivery
from human.validation import validate_delivery, product_contract
from human.demography_and_presence.population.static import PopulationSources
from human.demography_and_presence.population.static.sources import write_sources
from tests.test_static_delivery import fixture_study
from tests.test_native_features import specification


def setup_pipeline(tmp_path):
    cfg,cell=fixture_study(tmp_path)
    sources=PopulationSources(gpd.GeoDataFrame({'source_id':['fixture'],'country':['US'],'vintage':[2020],
        'jurisdiction':['US-WA'],'population':[100.]},geometry=[box(-123.01,47.99,-122.99,48.01)],crs=4326),None,{'synthetic':True})
    manifest=write_sources(sources,tmp_path/'sources/population')
    spec=specification(tmp_path);specpath=tmp_path/'native.json';specpath.write_text(json.dumps(spec))
    columns=product_contract()['products']['static.parquet']['columns']
    infrastructure={'h3_index':[cell],'h3_resolution':[7]}
    for field in columns:
        name=field['name']
        if not name.startswith(('boat_launch_access__','public_shore_access__','places__')):continue
        if field['arrow_type']=='bool':value=False
        elif field['arrow_type'] in ('string','large_string'):value='derived' if name.endswith('STATUS') else 'unknown'
        else:value=None
        infrastructure[name]=[value]
    path=tmp_path/'infrastructure.parquet'
    table=pa.Table.from_pandas(pd.DataFrame(infrastructure),preserve_index=False)
    table=table.replace_schema_metadata({b'human_study':json.dumps({'source_method':'original_qualified_snapshot','note':'synthetic'}).encode()})
    pq.write_table(table,path)
    import holidays,tzdata
    recipe={'schema':'human.pipeline.v1','study_config':str(cfg),'output_dir':'Human','work_dir':'work',
        'source_store':'sources/originals','metadata_dir':'receipts','calendar':{'as_of':'2021-07-04','holidays_version':holidays.__version__,'tzdata_version':tzdata.__version__},
        'population':{'source_manifest':str(manifest)},'native':{'specification':str(specpath)},
        'infrastructure':{'path':str(path),'sha256':digest(path)}}
    config=tmp_path/'config/human.yaml';config.parent.mkdir();config.write_text(yaml.safe_dump(recipe))
    return recipe,config


def test_pipeline_runs_all_three_products_cleans_intermediates_and_preserves_inputs(tmp_path):
    recipe,config=setup_pipeline(tmp_path)
    before={p:digest(p) for p in tmp_path.rglob('*') if p.is_file()}
    result=get_human_data(workspace=tmp_path)
    assert {p.name for p in result.directory.iterdir()}=={'static.parquet','dynamic-calendar.parquet','static-native.parquet'}
    assert set(result.validation)==set(product_contract()['products'])
    assert not list((tmp_path/'work').iterdir())
    assert not list(tmp_path.glob('.Human*'))
    assert all(digest(p)==old for p,old in before.items())
    assert result.receipt.is_file() and not result.receipt.is_relative_to(result.directory)
    catalog=read_catalog(result.directory)
    static=next(p for p in catalog['products'] if p['schema']=='human.study.static.v1')
    assert static['retained_static_provenance']['source_metadata']['source_method']=='original_qualified_snapshot'
    assert catalog['pipeline']['runtime']['human_source_sha256']
    assert len(pd.read_parquet(result.calendar))==10
    assert pd.read_parquet(result.native).shape==(6,12)
    first={name:pq.read_table(result.directory/name) for name in result.validation}
    receipt=result.receipt
    again=get_human_data(workspace=tmp_path)
    assert again.receipt!=receipt and receipt.is_file()
    assert not list((tmp_path/'work').iterdir())
    assert all(first[name].equals(pq.read_table(again.directory/name),check_metadata=False) for name in first)
    # Harmless platform metadata does not disable the supported reader.
    (again.directory/'.DS_Store').write_bytes(b'finder')
    assert validate_delivery(again.directory)
    (again.directory/'unknown.parquet').write_bytes(b'unregistered')
    with pytest.raises(ValueError,match='non-product'):validate_delivery(again.directory)


def test_missing_inputs_fail_before_population_and_preserve_current(tmp_path,monkeypatch):
    recipe,config=setup_pipeline(tmp_path)
    result=get_human_data(workspace=tmp_path);before={p:digest(p) for p in result.directory.iterdir()}
    spec=json.loads((tmp_path/'native.json').read_text());Path(spec['launches']['facilities']).unlink()
    import human.static_delivery
    monkeypatch.setattr(human.static_delivery,'get_population',lambda **kw:pytest.fail('Population must not run without native sources'))
    with pytest.raises(MissingHumanSources,match='Unresolved native'):get_human_data(workspace=tmp_path)
    assert {p:digest(p) for p in result.directory.iterdir()}==before
    assert not list((tmp_path/'work').iterdir()) and not list(tmp_path.glob('.Human*'))


def test_validation_failure_leaves_current_and_receipts_intact(tmp_path,monkeypatch):
    recipe,config=setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    before={p:digest(p) for p in result.directory.iterdir()};receipts=set((tmp_path/'receipts').iterdir())
    import human.pipeline as pipeline
    real=pipeline.validate_delivery
    def fail_candidate(root,**kw):
        if Path(root)!=result.directory:raise ValueError('injected validation failure')
        return real(root,**kw)
    monkeypatch.setattr(pipeline,'validate_delivery',fail_candidate)
    with pytest.raises(ValueError,match='injected'):get_human_data(workspace=tmp_path)
    assert {p:digest(p) for p in result.directory.iterdir()}==before
    assert set((tmp_path/'receipts').iterdir())==receipts
    assert not list((tmp_path/'work').iterdir()) and not list(tmp_path.glob('.Human*'))


def test_publication_readback_failure_rolls_back_current(tmp_path,monkeypatch):
    recipe,config=setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    before={p:digest(p) for p in result.directory.iterdir()}
    import human.pipeline as pipeline
    real=pipeline.validate_delivery;calls=0
    def fail_final(root,**kw):
        nonlocal calls
        calls+=1
        if calls==3:raise ValueError('readback failure')
        return real(root,**kw)
    monkeypatch.setattr(pipeline,'validate_delivery',fail_final)
    with pytest.raises(ValueError,match='readback'):get_human_data(workspace=tmp_path)
    assert {p:digest(p) for p in result.directory.iterdir()}==before
    assert not list(tmp_path.glob('.Human*'))


def test_receipt_reuse_is_rejected_without_changing_receipt(tmp_path):
    recipe,config=setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    before=digest(result.receipt)
    with pytest.raises(FileExistsError,match='immutable'):finalize_delivery(result.directory,tmp_path/'other',result.receipt.parent)
    assert digest(result.receipt)==before and not (tmp_path/'other').exists()


def test_semantic_validator_rejects_checksum_valid_bad_h3_and_negative_count(tmp_path):
    recipe,config=setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    table=pq.read_table(result.static);frame=table.to_pandas();frame.h3_index='bad';frame.population_count_collected=-1.
    altered=pa.Table.from_pandas(frame,preserve_index=False).replace_schema_metadata(table.schema.metadata)
    pq.write_table(altered,result.static)
    catalog=json.loads(result.receipt.read_text());next(p for p in catalog['products'] if p['artifact']['path']=='static.parquet')['artifact']['sha256']=digest(result.static);result.receipt.write_text(json.dumps(catalog))
    with pytest.raises(ValueError,match='valid R7'):validate_delivery(result.directory)


def test_collection_pins_download_and_cleans_partial_bytes(tmp_path,monkeypatch):
    import hashlib,requests
    payload=b'official retained source fixture';record={'path':'source.bin','url':'https://example.test/source','sha256':hashlib.sha256(payload).hexdigest()}
    class Response:
        status_code=200
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,size):yield payload
    calls=[]
    monkeypatch.setattr(requests,'get',lambda *a,**kw:(calls.append(a) or Response()))
    with pytest.raises(MissingHumanSources):collect_assets([record],tmp_path,mode='offline',byte_budget=100)
    assert not calls
    with pytest.raises(ValueError,match='max_download_bytes'):collect_assets([record],tmp_path,mode='live',byte_budget=2)
    assert not list(tmp_path.iterdir())
    receipts=collect_assets([record],tmp_path,mode='live',byte_budget=100)
    assert receipts[0]['sha256']==record['sha256'] and len(calls)==2
    collect_assets([record],tmp_path,mode='offline',byte_budget=0);assert len(calls)==2
    bad={**record,'sha256':'0'*64}
    with pytest.raises(ValueError,match='checksum'):collect_assets([bad],tmp_path,mode='offline',byte_budget=0)


def test_rule_versions_and_directory_overlap_fail_without_outputs(tmp_path):
    recipe,config=setup_pipeline(tmp_path);recipe['calendar']['tzdata_version']='0.invalid';config.write_text(yaml.safe_dump(recipe))
    with pytest.raises(ValueError,match='tzdata'):get_human_data(workspace=tmp_path)
    assert not (tmp_path/'Human').exists()


def test_packaged_timezone_conversion_ignores_host_rules():
    from datetime import datetime,timezone
    from importlib.resources import files
    from zoneinfo import ZoneInfo
    from human.temporal_context.calendar.lookup import build_calendar_lookup
    with files('tzdata.zoneinfo').joinpath('America','Vancouver').open('rb') as handle:zone=ZoneInfo.from_file(handle,key='America/Vancouver')
    frame=build_calendar_lookup('2026-12-01','2026-12-02',as_of='2026-10-16')
    expected=pd.Timestamp(datetime(2026,12,1,tzinfo=zone).astimezone(timezone.utc))
    assert frame.loc[frame.jurisdiction.eq('CA-BC'),'local_day_start_utc'].iloc[0]==expected


def test_live_domain_collection_preserves_originals_and_uses_run_owned_outputs(tmp_path,monkeypatch):
    from human.collection import collect_native
    import human.collection as collection
    from types import SimpleNamespace
    calls=[]
    def module(name):
        group='launches' if 'boat_launch_access' in name else 'shore_access' if 'public_shore_access' in name else 'places'
        def download(config,overwrite=False):
            cfg=yaml.safe_load(Path(config).read_text());raw=Path(cfg['raw']['manifest_path']);raw.parent.mkdir(parents=True,exist_ok=True)
            original=raw.parent/'original.bin';original.write_bytes(b'unique source')
            raw.write_text(json.dumps({'artifacts':[{'path':str(original),'sha256':digest(original)}],'sources':[{'name':group,'sha256':digest(original)}]}))
            if group=='places':Path(cfg['raw']['inventory_path']).write_text(json.dumps({'wa_state_parks':str(original)}))
            calls.append(('download',group))
        def build(config,**kwargs):
            cfg=yaml.safe_load(Path(config).read_text());out=Path(cfg['output']['manifest_path'])
            assert out.is_relative_to(tmp_path/'run') and 'app_export' not in cfg
            out.write_text(json.dumps({'artifacts':[],'inputs':[]}));calls.append(('build',group))
        return SimpleNamespace(download=download,build=build)
    monkeypatch.setattr(collection,'import_module',module)
    run=tmp_path/'run';run.mkdir();store=tmp_path/'originals'
    spec,receipt=collect_native({'acquire':True},tmp_path,run,store,mode='live')
    assert len(calls)==6 and set(spec)=={'schema','launches','shore_access','places'}
    assert len(receipt['acquisition_receipts'])==3
    hashes={p:digest(p) for p in store.rglob('*') if p.is_file()}
    collect_native({'acquire':True},tmp_path,run,store,mode='live')
    assert len(calls)==9  # valid original acquisition receipts are reused, never re-dated
    assert all(digest(p)==value for p,value in hashes.items())


def test_fresh_infrastructure_derivation_uses_full_contract(tmp_path):
    recipe,config=setup_pipeline(tmp_path)
    spec=json.loads((tmp_path/'native.json').read_text())
    original=pd.read_parquet(recipe['infrastructure']['path'])
    for group,prefix,filename in [('launches','boat_launch_access__','boat_launch_access_h3_r7.parquet'),('shore_access','public_shore_access__','public_shore_access_h3_r7.parquet')]:
        columns=['h3_index','h3_resolution']+[c for c in original if c.startswith(prefix)]
        frame=original[columns].rename(columns={c:c.removeprefix(prefix) for c in columns if c.startswith(prefix)})
        frame=frame.rename(columns={'h3_index':'H3_INDEX','h3_resolution':'H3_RESOLUTION'})
        path=tmp_path/filename;frame.to_parquet(path)
        prior=json.loads(Path(spec[group]['manifest']).read_text())
        prior['stage']='build';prior['artifacts'].append({'path':str(path),'sha256':digest(path)})
        manifest=tmp_path/(group+'-build.json');manifest.write_text(json.dumps(prior));spec[group]['manifest']=str(manifest)
    places=tmp_path/'places_of_interest.json';places.write_bytes(Path(spec['places']['catalog']).read_bytes())
    prior=json.loads(Path(spec['places']['manifest']).read_text());prior['stage']='build';prior['artifacts'].append({'path':str(places),'sha256':digest(places)})
    manifest=tmp_path/'places-build.json';manifest.write_text(json.dumps(prior));spec['places'].update(manifest=str(manifest),catalog=str(places))
    (tmp_path/'native.json').write_text(json.dumps(spec));recipe.pop('infrastructure');config.write_text(yaml.safe_dump(recipe))
    result=get_human_data(workspace=tmp_path)
    assert pd.read_parquet(result.static).shape==(1,64)
    static=next(p for p in read_catalog(result.directory)['products'] if p['schema']=='human.study.static.v1')
    assert static['retained_static_provenance']['qualification']=='source_build_summaries_rebuilt_from_verified_originals'
    assert not list((tmp_path/'work').iterdir())


def test_cli_runs_only_the_supported_pipeline(tmp_path,capsys):
    setup_pipeline(tmp_path)
    from human.cli import main
    assert main(['--workspace',str(tmp_path),'get-human-data'])==0
    assert set(json.loads(capsys.readouterr().out)['products'])==set(product_contract()['products'])
    for command in ('build','download','families','export-static-matrix'):
        with pytest.raises(SystemExit):main([command])


def test_retained_population_cache_is_read_only_and_identity_verified(tmp_path,monkeypatch):
    recipe,config=setup_pipeline(tmp_path)
    from human import get_population
    import human.static_delivery
    cell=pd.read_parquet(recipe['infrastructure']['path']).h3_index.iloc[0]
    cache=tmp_path/'retained-calculation'
    result=get_population(target_h3=[cell],h3_res=7,radii_km=(25,50,100),include_bands=False,
        workspace=cache,source_manifest=recipe['population']['source_manifest'],mode='offline',
        allow_partial=True,resource_limits={'workers':1,'buffer_tolerance_m':0.25})
    recipe['population']['cache_workspace']=str(cache);config.write_text(yaml.safe_dump(recipe))
    hashes={p:digest(p) for p in cache.rglob('*') if p.is_file()}
    def require_read_only(**kwargs):
        assert kwargs['cache_only'] and kwargs['workspace']==cache
        return get_population(**kwargs)
    monkeypatch.setattr(human.static_delivery,'get_population',require_read_only)
    delivery=get_human_data(workspace=tmp_path)
    assert all(digest(p)==sha for p,sha in hashes.items())
    before={p:digest(p) for p in delivery.directory.iterdir()}
    metadata=result.path.with_name('metadata.json');payload=json.loads(metadata.read_text())
    payload['cache_key']='incorrect';metadata.write_text(json.dumps(payload))
    with pytest.raises(ValueError,match='cache identity'):get_human_data(workspace=tmp_path)
    assert {p:digest(p) for p in delivery.directory.iterdir()}==before
    assert not list((tmp_path/'work').iterdir())
    # A cache-only reader cannot create a missing calculation outside run ownership.
    missing=tmp_path/'missing-cache'
    with pytest.raises(FileNotFoundError,match='cache is unavailable'):
        get_population(target_h3=[cell],h3_res=7,workspace=missing,
            source_manifest=recipe['population']['source_manifest'],cache_only=True,allow_partial=True)
    assert not missing.exists()
