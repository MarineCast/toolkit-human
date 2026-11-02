"""End-to-end small source fixtures for the supported two-product pipeline."""
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
from human.pipelines.native.collection import MissingHumanSources, collect_assets
from human.pipelines.delivery.finalize import digest, read_catalog, finalize_delivery
from human.pipelines.delivery.validation import validate_delivery, product_contract
from human.pipelines.population.static import PopulationSources
from human.pipelines.population.static.sources import write_sources
from tests.test_static_delivery import fixture_study
from tests.test_native_features import specification


def setup_pipeline(tmp_path):
    cfg,cell=fixture_study(tmp_path)
    sources=PopulationSources(gpd.GeoDataFrame({'source_id':['fixture'],'country':['US'],'vintage':[2020],
        'jurisdiction':['US-WA'],'population':[100.]},geometry=[box(-123.01,47.99,-122.99,48.01)],crs=4326),None,{'synthetic':True})
    manifest=write_sources(sources,tmp_path/'sources/population')
    spec=specification(tmp_path);specpath=tmp_path/'native.json';specpath.write_text(json.dumps(spec))
    columns=product_contract(version=2)['products']['static.parquet']['columns']
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


def test_pipeline_runs_both_products_cleans_intermediates_and_preserves_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr("human.pipelines.native.features.compose_native_features", lambda *a, **kw: pytest.fail("Native export composition must not run"))
    recipe,config=setup_pipeline(tmp_path)
    before={p:digest(p) for p in tmp_path.rglob('*') if p.is_file()}
    result=get_human_data(workspace=tmp_path)
    assert {p.name for p in result.directory.iterdir()}=={'static.parquet','dynamic-calendar.parquet'}
    assert set(result.validation)==set(product_contract()['products'])
    assert not list((tmp_path/'work').iterdir())
    assert not list(tmp_path.glob('.Human*'))
    assert all(digest(p)==old for p,old in before.items())
    assert result.receipt.is_file() and not result.receipt.is_relative_to(result.directory)
    catalog=read_catalog(result.directory)
    static=next(p for p in catalog['products'] if p['schema']=='human.study.static.v6')
    assert static['retained_static_provenance']['source_metadata']['source_method']=='original_qualified_snapshot'
    assert catalog['pipeline']['runtime']['human_source_sha256']
    assert len(pd.read_parquet(result.calendar))==4
    assert not hasattr(result, 'native')
    note=result.directory/'REVIEW_RESULTS.txt';note.write_text('User review stays here.\n')
    first={name:pq.read_table(result.directory/name) for name in result.validation}
    receipt=result.receipt
    again=get_human_data(workspace=tmp_path)
    assert again.receipt!=receipt and receipt.is_file()
    assert note.read_text()=='User review stays here.\n'
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
    import human.pipelines.population.delivery as population_delivery
    monkeypatch.setattr(population_delivery,'get_population',lambda **kw:pytest.fail('Population must not run without native sources'))
    with pytest.raises(MissingHumanSources,match='Unresolved native'):get_human_data(workspace=tmp_path)
    assert {p:digest(p) for p in result.directory.iterdir()}==before
    assert not list((tmp_path/'work').iterdir()) and not list(tmp_path.glob('.Human*'))


def test_validation_failure_leaves_current_and_receipts_intact(tmp_path,monkeypatch):
    recipe,config=setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    before={p:digest(p) for p in result.directory.iterdir()};receipts=set((tmp_path/'receipts').iterdir())
    import human.main as pipeline
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
    import human.main as pipeline
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
    table=pq.read_table(result.static);frame=table.to_pandas();frame.h3_index='bad';frame.population_count=-1.
    altered=pa.Table.from_pandas(frame,preserve_index=False).replace_schema_metadata(table.schema.metadata)
    pq.write_table(altered,result.static)
    catalog=json.loads(result.receipt.read_text());next(p for p in catalog['products'] if p['artifact']['path']=='static.parquet')['artifact']['sha256']=digest(result.static);result.receipt.write_text(json.dumps(catalog))
    with pytest.raises(ValueError,match='H3 membership|valid R7'):validate_delivery(result.directory)


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
    from human.pipelines.calendar.lookup import build_calendar_lookup
    with files('tzdata.zoneinfo').joinpath('America','Vancouver').open('rb') as handle:zone=ZoneInfo.from_file(handle,key='America/Vancouver')
    frame=build_calendar_lookup('2026-12-01','2026-12-02',as_of='2026-10-16')
    expected=pd.Timestamp(datetime(2026,12,1,tzinfo=zone).astimezone(timezone.utc))
    assert frame.loc[frame.jurisdiction.eq('CA-BC'),'local_day_start_utc'].iloc[0]==expected


def test_live_domain_collection_preserves_originals_and_uses_run_owned_outputs(tmp_path,monkeypatch):
    from human.pipelines.native.collection import collect_native
    import human.pipelines.native.collection as collection
    from types import SimpleNamespace
    calls=[]
    def module(name):
        group='launches' if 'boat_launches' in name else 'shore_access' if 'shore_access' in name else 'places'
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
    assert pd.read_parquet(result.static).shape==(1,14)
    static=next(p for p in read_catalog(result.directory)['products'] if p['schema']=='human.study.static.v6')
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
    import human.pipelines.population.delivery as population_delivery
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
    monkeypatch.setattr(population_delivery,'get_population',require_read_only)
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


def historical_delivery(tmp_path):
    """Checksum-bound historical layout, using synthetic static/source fixtures."""
    from human.pipelines.calendar.lookup import build_calendar_lookup
    setup_pipeline(tmp_path)
    result=get_human_data(workspace=tmp_path)
    catalog=read_catalog(result.directory)
    static=next(p for p in catalog['products'] if p['schema']=='human.study.static.v6')
    from human.pipelines.delivery.static import restore_static
    full_static, static=restore_static(pq.read_table(result.static), static)
    calendar=next(p for p in catalog['products'] if p['schema']=='human.study.calendar_lookup.v2')
    calendar.update(schema='human.study.calendar_lookup.v1', primary_key=['date','jurisdiction'])
    native={'schema':'human.native_features.v1','source_receipts':[{'source_id':'synthetic-native','sha256':'fixture'}]}
    common={'schema':'human.delivery.final_only.v1','receipt_path':str(result.receipt),'catalog':{}}
    native_table=pa.table({'feature_id':['synthetic:one'], 'properties_json':['{"source_id":"synthetic-native"}']})
    calendar_table=pa.Table.from_pandas(build_calendar_lookup('2021-07-04','2021-07-06',as_of='2021-07-04'),preserve_index=False)
    def write(name, table, info):
        info={k:v for k,v in info.items() if k!='artifact'}
        pq.write_table(table.replace_schema_metadata({b'human_study':json.dumps(info).encode(),b'human_delivery':json.dumps(common).encode()}),result.directory/name)
        return {**info,'artifact':{'path':name,'sha256':digest(result.directory/name),'rows':table.num_rows}}
    native=write('static-native.parquet',native_table,native)
    static['native_features']=native['artifact']
    static=write('static.parquet',full_static,static)
    calendar=write('dynamic-calendar.parquet',calendar_table,calendar)
    result.receipt.write_text(json.dumps({**catalog,'products':[static,calendar,native]}))
    (result.directory/'REVIEW_RESULTS.txt').write_text('Keep my review notes.\n')
    return result


def test_review_migration_preserves_static_and_notes_and_removes_native(tmp_path):
    from human.pipelines.delivery.migration import migrate_reviewed_delivery
    result=historical_delivery(tmp_path)
    static=pq.read_table(result.static)
    receipt_hash=digest(result.receipt)
    report=migrate_reviewed_delivery(result.directory,metadata_dir=tmp_path/'review-receipt')
    assert set(report)=={'static.parquet','dynamic-calendar.parquet'}
    assert {p.name for p in result.directory.iterdir()}=={*report,'REVIEW_RESULTS.txt'}
    from human.pipelines.delivery.static import restore_static
    compact_info=next(p for p in read_catalog(result.directory)['products'] if p['schema']=='human.study.static.v6')
    assert restore_static(pq.read_table(result.static),compact_info)[0].equals(static,check_metadata=False)
    assert (result.directory/'REVIEW_RESULTS.txt').read_text()=='Keep my review notes.\n'
    assert digest(result.receipt)==receipt_hash
    assert not list(tmp_path.glob('.Human*'))
    info=read_catalog(result.directory)['products'][0]
    assert 'native_features' not in info and info['infrastructure_source_provenance']['source_receipts'][0]['source_id']=='synthetic-native'


def test_review_migration_readback_failure_rolls_back_all_three_files_and_notes(tmp_path,monkeypatch):
    from human.pipelines.delivery import migration
    result=historical_delivery(tmp_path)
    before={p.name:digest(p) for p in result.directory.iterdir()}
    real=migration.validate_delivery
    def reject_final(path,**kw):
        if Path(path)==result.directory:raise ValueError('Injected migration readback failure')
        return real(path,**kw)
    monkeypatch.setattr(migration,'validate_delivery',reject_final)
    with pytest.raises(ValueError,match='readback'):
        migration.migrate_reviewed_delivery(result.directory,metadata_dir=tmp_path/'review-receipt')
    assert before=={p.name:digest(p) for p in result.directory.iterdir()}
    assert not list(tmp_path.glob('.Human*'))


def test_compact_static_external_audit_reconstructs_every_source_column(tmp_path):
    from human.pipelines.delivery.static import restore_static, read_audit
    from human.pipelines.delivery.names import TRANSFORMED_SOURCE_COLUMNS
    setup_pipeline(tmp_path)
    result=get_human_data(workspace=tmp_path)
    table=pq.read_table(result.static)
    info=next(p for p in read_catalog(result.directory)['products'] if p['schema']=='human.study.static.v6')
    assert table.num_columns==14 and table.column_names==[c['name'] for c in product_contract()['products']['static.parquet']['columns']]
    assert table['has_port'].to_pylist()==[0]*table.num_rows
    full, old_info=restore_static(table,info)
    assert full.num_columns==64 and old_info['schema']=='human.study.static.v1'
    audit, _=read_audit(table,info)
    assert audit.column_names==['h3_index',*[c for c in full.column_names if c not in info['column_name_mapping'] or c in TRANSFORMED_SOURCE_COLUMNS]]
    assert audit['population_within_100km_collected__zero_imputed'].to_pylist()==[True]
    assert 'population_within_100km_collected__zero_imputed' not in table.column_names
    assert info['population']['fields']['population_density_per_km2']['audit_fields']['coverage_status']=='population_count__coverage_status'
    assert info['population']['fields']['population_density_per_km2']['audit_fields']['cell_area_km2']=='cell_area_km2'
    assert not Path(info['static_audit']['artifact']['path']).is_relative_to(result.directory)
    receipt_hash=digest(result.receipt)
    other=tmp_path/'other'
    finalize_delivery(result.directory,other,tmp_path/'another-receipt')
    new_info=next(p for p in read_catalog(other)['products'] if p['schema']=='human.study.static.v6')
    assert restore_static(pq.read_table(other/'static.parquet'),new_info)[0].equals(full,check_metadata=False)
    assert digest(result.receipt)==receipt_hash
    assert read_catalog(other)['default_population']['audit_fields']==read_catalog(result.directory)['default_population']['audit_fields']


def test_compact_audit_missing_or_tampered_is_rejected(tmp_path):
    setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    info=next(p for p in read_catalog(result.directory)['products'] if p['schema']=='human.study.static.v6')
    path=Path(info['static_audit']['artifact']['path'])
    with path.open('ab') as stream:stream.write(b'changed')
    with pytest.raises(ValueError,match='audit checksum'):validate_delivery(result.directory)
    path.unlink()
    with pytest.raises(ValueError,match='audit checksum'):validate_delivery(result.directory)


def test_compact_audit_identity_and_semantics_checked_after_checksum_rebinding(tmp_path):
    from human.pipelines.delivery.static import restore_static
    setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    catalog=read_catalog(result.directory)
    info=next(p for p in catalog['products'] if p['schema']=='human.study.static.v6')
    path=Path(info['static_audit']['artifact']['path']);audit=pq.read_table(path)
    wrong=audit.set_column(0,audit.schema.field('h3_index'),pa.array(['8728d555effffff'],type=pa.large_string()))
    pq.write_table(wrong,path)
    altered=copy.deepcopy(info);altered['static_audit']['artifact']['sha256']=digest(path)
    with pytest.raises(ValueError,match='H3 membership'):restore_static(pq.read_table(result.static),altered)
    name='population_count_collected__zero_imputed';idx=audit.column_names.index(name)
    pq.write_table(audit.set_column(idx,audit.schema.field(name),pa.array([False])),path)
    info['static_audit']['artifact']['sha256']=digest(path)
    table=pq.read_table(result.static)
    embedded={k:v for k,v in info.items() if k!='artifact'}
    pq.write_table(table.replace_schema_metadata({**table.schema.metadata,b'human_study':json.dumps(embedded,sort_keys=True).encode()}),result.static)
    info['artifact']['sha256']=digest(result.static)
    result.receipt.write_text(json.dumps(catalog))
    with pytest.raises(ValueError,match='zero-imputation flag'):validate_delivery(result.directory)


def test_current_two_file_compaction_migration_preserves_audit_calendar_and_notes(tmp_path):
    from human.pipelines.delivery.migration import migrate_reviewed_delivery
    from human.pipelines.delivery.static import restore_static
    setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    info=next(p for p in read_catalog(result.directory)['products'] if p['schema']=='human.study.static.v6')
    original,_=restore_static(pq.read_table(result.static),info)
    calendar=pq.read_table(result.calendar)
    note=result.directory/'REVIEW_RESULTS.txt';note.write_text('Keep my notes.')
    migrate_reviewed_delivery(result.directory,metadata_dir=tmp_path/'compact-migration')
    new_info=next(p for p in read_catalog(result.directory)['products'] if p['schema']=='human.study.static.v6')
    assert restore_static(pq.read_table(result.static),new_info)[0].equals(original,check_metadata=False)
    assert calendar.equals(pq.read_table(result.calendar),check_metadata=False)
    assert note.read_text()=='Keep my notes.'
    assert validate_delivery(result.directory)['static.parquet']['columns']==14


def test_retained_compact_infrastructure_reuses_external_audit_without_losing_details(tmp_path):
    from human.pipelines.delivery.static import restore_static
    recipe,config=setup_pipeline(tmp_path)
    first=get_human_data(workspace=tmp_path)
    info=next(p for p in read_catalog(first.directory)['products'] if p['schema']=='human.study.static.v6')
    original,_=restore_static(pq.read_table(first.static),info)
    recipe['infrastructure']={'path':str(first.static),'sha256':digest(first.static)}
    config.write_text(yaml.safe_dump(recipe))
    second=get_human_data(workspace=tmp_path,output_dir='second')
    new_info=next(p for p in read_catalog(second.directory)['products'] if p['schema']=='human.study.static.v6')
    retained,_=restore_static(pq.read_table(second.static),new_info)
    assert original.equals(retained,check_metadata=False)


def test_external_audit_aligns_by_h3_and_preserves_varying_source_diagnostics(tmp_path):
    from human.pipelines.delivery.static import compact_static, restore_static
    setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    info=next(p for p in read_catalog(result.directory)['products'] if p['schema']=='human.study.static.v6')
    source,original_info=restore_static(pq.read_table(result.static),info)
    source=pa.concat_tables([source,source])
    source=source.set_column(0,source.schema.field('h3_index'),pa.array(['8728d554effffff','8728d555effffff'],type=pa.large_string()))
    diagnostic='boat_launch_access__SOURCE_DATASET_COUNT';idx=source.column_names.index(diagnostic)
    source=source.set_column(idx,source.schema.field(diagnostic),pa.array([1.,2.]))
    receipt=tmp_path/'alignment-receipt';receipt.mkdir()
    compact,compact_info=compact_static(source,original_info,receipt)
    audit_path=Path(compact_info['static_audit']['artifact']['path'])
    audit=pq.read_table(audit_path);pq.write_table(audit.take(pa.array([1,0])),audit_path)
    compact_info['static_audit']['artifact']['sha256']=digest(audit_path)
    restored,_=restore_static(compact,compact_info)
    assert restored.equals(source,check_metadata=False)
    assert restored[diagnostic].to_pylist()==[1.,2.]


@pytest.mark.parametrize("old_schema", ["human.study.static.v2", "human.study.static.v3", "human.study.static.v4"])
def test_static_snake_case_mapping_and_v2_migration_are_lossless(tmp_path, old_schema):
    import re
    from human.pipelines.delivery.names import STATIC_NAMES, STATIC_NAMES_V3, STATIC_NAMES_V4, AUDIT_ONLY_COLUMNS, PRESENCE_FROM_COUNTS
    from human.pipelines.delivery.static import restore_static
    from human.pipelines.delivery.migration import migrate_reviewed_delivery
    from human.pipelines.native.features import summary_fields
    setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    catalog=read_catalog(result.directory)
    info=next(p for p in catalog['products'] if p['artifact']['path']=='static.parquet')
    table=pq.read_table(result.static)
    assert all(re.fullmatch(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*', name) for name in table.column_names)
    assert [*STATIC_NAMES.values(),*PRESENCE_FROM_COUNTS]==table.column_names
    assert {c['source_name']:c['name'] for c in product_contract()['products']['static.parquet']['columns'] if 'source_name' in c}==STATIC_NAMES
    original, original_info=restore_static(table,info)
    wrong=copy.deepcopy(info)
    wrong['column_name_mapping']['ports__SOURCE_RECORD_COUNT']='place_record_count'
    with pytest.raises(ValueError,match='column name mapping'):restore_static(table,wrong)
    assert not set(table.column_names) & AUDIT_ONLY_COLUMNS
    # Emulate an actual preceding 27-column delivery and its original audit partition.
    import base64
    old_spec=product_contract(version={'human.study.static.v2':3,'human.study.static.v3':4,'human.study.static.v4':5}[old_schema])['products']['static.parquet']
    old_arrow=pa.ipc.read_schema(pa.BufferReader(base64.b64decode(old_spec['arrow_schema_base64'])))
    source=original
    old_mapping=STATIC_NAMES_V4 if old_schema.endswith('v4') else STATIC_NAMES_V3
    for raw,new_name in old_mapping.items():
        if raw not in source.column_names:
            kind=old_arrow.field(raw if old_schema.endswith('v2') else new_name).type
            source=source.append_column(raw,pa.nulls(source.num_rows,type=kind))
    old=source.select(list(old_mapping)).rename_columns(old_arrow.names).cast(old_arrow)
    info['schema']=old_schema
    info.pop('column_name_mapping')
    if not old_schema.endswith('v2'):info['column_name_mapping']=old_mapping.copy()
    info['infrastructure_fields']=summary_fields(old.column_names)
    audit_path=Path(info['static_audit']['artifact']['path'])
    audit=pq.read_table(audit_path)
    selected=['h3_index',*[c for c in original.column_names if c not in old_mapping]]
    audit=audit.select(selected)
    pq.write_table(audit,audit_path)
    info['static_audit']['columns']=selected
    info['static_audit']['artifact']['sha256']=digest(audit_path)
    info['population']['fields']=copy.deepcopy(original_info['population']['fields'])
    for metric,definition in info['population']['fields'].items():
        coverage=original_info['population']['fields'][metric]['base_metric']+'__coverage_status'
        definition['coverage_status']=coverage if old_schema.endswith('v2') else STATIC_NAMES_V3[coverage]
        definition.pop('unverified_geographic_support',None)
    meta={**table.schema.metadata,b'human_study':json.dumps({k:v for k,v in info.items() if k!='artifact'},sort_keys=True).encode()}
    pq.write_table(old.replace_schema_metadata(meta),result.static)
    info['artifact']['sha256']=digest(result.static)
    result.receipt.write_text(json.dumps(catalog))
    calendar=pq.read_table(result.calendar)
    migrate_reviewed_delivery(result.directory,metadata_dir=tmp_path/'renamed-receipt')
    new_info=next(p for p in read_catalog(result.directory)['products'] if p['artifact']['path']=='static.parquet')
    renamed=pq.read_table(result.static)
    assert renamed.equals(table,check_metadata=False)
    assert restore_static(renamed,new_info)[0].equals(original,check_metadata=False)
    assert pq.read_table(result.calendar).equals(calendar,check_metadata=False)


def test_removed_cell_area_stays_audit_evidence_and_density_is_still_validated(tmp_path):
    from human.pipelines.delivery.names import AUDIT_ONLY_COLUMNS
    setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    table=pq.read_table(result.static);catalog=read_catalog(result.directory)
    info=next(p for p in catalog['products'] if p['artifact']['path']=='static.parquet')
    assert table.num_columns==14 and not set(table.column_names)&AUDIT_ONLY_COLUMNS
    assert read_catalog(result.directory)['default_population']['support_location']=='external_static_audit'
    assert 'coverage_status' not in read_catalog(result.directory)['default_population']
    path=Path(info['static_audit']['artifact']['path']);audit=pq.read_table(path)
    assert 'cell_area_km2' in audit.column_names
    assert all(base+suffix in audit.column_names for base in ('population_count','population_within_25km','population_within_50km','population_within_100km') for suffix in ('__coverage_status','__unverified_geographic_support'))
    idx=audit.column_names.index('cell_area_km2')
    pq.write_table(audit.set_column(idx,audit.schema.field('cell_area_km2'),pa.array([0.])),path)
    info['static_audit']['artifact']['sha256']=digest(path)
    embedded={k:v for k,v in info.items() if k!='artifact'}
    pq.write_table(table.replace_schema_metadata({**table.schema.metadata,b'human_study':json.dumps(embedded,sort_keys=True).encode()}),result.static)
    info['artifact']['sha256']=digest(result.static);result.receipt.write_text(json.dumps(catalog))
    with pytest.raises(ValueError,match='Full cell area'):validate_delivery(result.directory)


def test_reviewed_integer_totals_and_port_presence_preserve_original_masks(tmp_path):
    from human.pipelines.delivery.static import compact_static, restore_static, project_static
    setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    info=next(p for p in read_catalog(result.directory)['products'] if p['artifact']['path']=='static.parquet')
    original,original_info=restore_static(pq.read_table(result.static),info)
    original=pa.concat_tables([original,original,original])
    original=original.set_column(0,original.schema.field('h3_index'),pa.array(['8728d554effffff','8728d555effffff','8728d1915ffffff'],type=pa.large_string()))
    values={'boat_launch_access__BOAT_LAUNCH_COUNT':[None,0.,4.],
        'boat_launch_access__VERIFIED_PUBLIC_BOAT_LAUNCH_COUNT':[None,0.,3.],
        'public_shore_access__PUBLIC_ACCESS_SITE_COUNT':[None,0.,7.],
        'public_shore_access__VERIFIED_PUBLIC_ACCESS_SITE_COUNT':[None,0.,2.],
        'public_shore_access__MAPPED_ACCESS_UNKNOWN_SITE_COUNT':[None,0.,5.],
        'places__CATALOG_RECORD_COUNT':[None,0.,9.]}
    for name,items in values.items():
        original=original.set_column(original.column_names.index(name),pa.field(name,pa.float64()),pa.array(items,type=pa.float64()))
    original=original.append_column('ports__SOURCE_RECORD_COUNT',pa.array([None,0,3],type=pa.int64()))
    receipt=tmp_path/'encoded';receipt.mkdir()
    compact,metadata=compact_static(original,original_info,receipt)
    assert compact['boat_launch_count'].to_pylist()==[0,0,4]
    assert compact['shore_access_site_count'].to_pylist()==[0,0,7] # existing total, not total plus categories
    assert compact['place_record_count'].to_pylist()==[0,0,9]
    assert compact['has_port'].to_pylist()==[0,0,1]
    for presence in ('has_boat_launch','has_shore_access_site','has_place','has_port'):
        assert compact[presence].type==pa.int64() and compact[presence].null_count==0
        assert compact[presence].to_pylist()==[0,0,1]
    assert all(compact[c].type==pa.int64() and compact[c].null_count==0 for c in ('boat_launch_count','shore_access_site_count','place_record_count'))
    assert 'mapped_marine_shoreline_length_m' not in compact.column_names
    assert restore_static(compact,metadata)[0].equals(original,check_metadata=False)
    for column in ('boat_launch_count','has_port','has_boat_launch','has_shore_access_site','has_place'):
        idx=compact.column_names.index(column)
        replacement=pa.array([1,0,4],type=pa.int64()) if column=='boat_launch_count' else pa.array([1,0,1],type=pa.int64())
        with pytest.raises(ValueError,match='transformation'):
            restore_static(compact.set_column(idx,compact.schema.field(column),replacement),metadata)
    name='boat_launch_access__BOAT_LAUNCH_COUNT';idx=original.column_names.index(name)
    for value in (1.5,-1.,float('inf')):
        invalid=original.set_column(idx,pa.field(name,pa.float64()),pa.array([value,0.,4.]))
        with pytest.raises((ValueError,pa.ArrowInvalid)):project_static(invalid)


def test_previous_boolean_port_schema_migrates_to_four_integer_presence_fields(tmp_path):
    from human.pipelines.delivery.static import restore_static,project_static
    from human.pipelines.delivery.migration import migrate_reviewed_delivery
    setup_pipeline(tmp_path);result=get_human_data(workspace=tmp_path)
    catalog=read_catalog(result.directory);info=next(p for p in catalog['products'] if p['artifact']['path']=='static.parquet')
    current=pq.read_table(result.static);original,_=restore_static(current,info)
    legacy=project_static(original,version=6)
    assert legacy.num_columns==11 and legacy['has_port'].type==pa.bool_()
    info['schema']='human.study.static.v5'
    info['infrastructure_fields']={k:v for k,v in info['infrastructure_fields'].items() if k in ('boat_launch_count','shore_access_site_count','place_record_count','has_port')}
    info['infrastructure_fields']['has_port'].update(units='boolean',null_fill_value=False,derivation='fill_null(source_count, 0) > 0')
    metadata={**current.schema.metadata,b'human_study':json.dumps({k:v for k,v in info.items() if k!='artifact'},sort_keys=True).encode()}
    pq.write_table(legacy.replace_schema_metadata(metadata),result.static)
    info['artifact']['sha256']=digest(result.static);result.receipt.write_text(json.dumps(catalog))
    assert restore_static(pq.read_table(result.static),info)[0].equals(original,check_metadata=False)
    calendar=pq.read_table(result.calendar)
    migrate_reviewed_delivery(result.directory,metadata_dir=tmp_path/'presence-migration')
    updated=pq.read_table(result.static)
    assert updated.equals(current,check_metadata=False)
    assert pq.read_table(result.calendar).equals(calendar,check_metadata=False)
