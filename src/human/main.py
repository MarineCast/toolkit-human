"""Collect, build, validate and publish exactly the three Human products."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from importlib import metadata
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

import yaml

from human.pipelines.native.collection import collect_native
from human.utils.downloads import collect_assets, MissingHumanSources
from human.pipelines.delivery.finalize import finalize_delivery, digest, read_catalog
from human.utils.study import load_static_study
from human.pipelines.delivery.validation import validate_delivery
from human.pipelines.delivery.settings import preflight, log

PRODUCTS = ('static.parquet','dynamic-calendar.parquet','static-native.parquet')


@dataclass(frozen=True)
class HumanData:
    """The final product paths, external receipt and validation result."""
    directory: Path
    receipt: Path
    validation: dict

    @property
    def static(self): return self.directory/'static.parquet'
    @property
    def calendar(self): return self.directory/'dynamic-calendar.parquet'
    @property
    def native(self): return self.directory/'static-native.parquet'


def _recipe(config, root):
    path = Path(config) if config is not None else root/'config/human.yaml'
    if not path.is_absolute():path=root/path
    if not path.is_file():
        raise FileNotFoundError(f'Human pipeline configuration is missing: {path}. Run human init and configure its source inputs.')
    data=yaml.safe_load(path.read_text())
    allowed={'schema','study_config','output_dir','work_dir','source_store','metadata_dir','calendar','population','native','infrastructure','assets','max_download_bytes','expected_rows'}
    if not isinstance(data,dict) or data.get('schema')!='human.pipeline.v1' or set(data)-allowed:
        raise ValueError('Invalid human.pipeline.v1 configuration or unknown keys')
    required={'study_config','output_dir','work_dir','source_store','metadata_dir','calendar','population','native'}
    if not required.issubset(data):raise ValueError('Missing pipeline settings: '+str(sorted(required-set(data))))
    calendar_keys={'as_of','holidays_version','tzdata_version'}
    if not calendar_keys.issubset(data['calendar']) or set(data['calendar'])-calendar_keys-{'start_date','end_date'}:
        raise ValueError('Calendar requires explicit as_of, holidays_version and tzdata_version; only start_date/end_date are optional')
    data['calendar']['as_of']=date.fromisoformat(str(data['calendar']['as_of'])).isoformat()
    for package,key in [('holidays','holidays_version'),('tzdata','tzdata_version')]:
        if metadata.version(package)!=str(data['calendar'][key]):
            raise ValueError(f'Calendar requires {package}=={data["calendar"][key]}; installed {metadata.version(package)}')
    return data, {'path':str(path.resolve()),'sha256':digest(path),'settings':data}


def _population(config, root, run, study, source_store, mode):
    from human.pipelines.population.static.sources import bundle_inventory
    pointer=config.get('manifest_pointer')
    manifest=config.get('source_manifest')
    if pointer:
        pointer=(root/pointer).resolve()
        if pointer.is_file():
            value=Path(pointer.read_text().strip());manifest=str(value if value.is_absolute() else pointer.parent/value)
    if manifest:
        path=(root/manifest).resolve()
        if path.exists():
            # A present corrupt bundle must fail; only a missing location falls back.
            bundle_inventory(path)
            return path
        if not config.get('specification') and not config.get('local_specification'):raise MissingHumanSources(f'Unresolved normalized census manifest: {path}')
    local=config.get('local_specification')
    local_path=(root/local).resolve() if local else None
    spec=local_path if local_path is not None and local_path.exists() else config.get('specification')
    if not spec:raise ValueError('population requires source_manifest, manifest_pointer or specification')
    from human.pipelines.population.collection import prepare_study_sources
    path=(root/spec).resolve()
    if not path.is_file():raise MissingHumanSources(f'Unresolved census acquisition specification: {path}')
    # Normalization and footprint files are intermediates owned by this run.
    # Explicit original source locations in the specification remain retained.
    definition=json.loads(path.read_text())
    for country in ('us','canada'):
        for entry in definition.get(country,[]):
            for key in ('geography','population'):
                if entry.get(key):entry[key]=str((root/entry[key]).resolve())
                provenance=entry.get(key+'_provenance')
                if provenance:provenance['receipt']=str((root/provenance['receipt']).resolve())
    for entry in definition.get('jurisdiction_inventories',[]):entry['path']=str((root/entry['path']).resolve())
    resolved=run/'census-specification.json';resolved.write_text(json.dumps(definition))
    plan=prepare_study_sources(study_config=study['identity']['config_path'],workspace=run/'population',specification=resolved,mode=mode)
    return Path(json.loads(plan.read_text())['source_manifest'])


def _infrastructure(entry, root, run, native):
    if entry:
        path=(root/entry['path']).resolve()
        if not path.is_file():raise MissingHumanSources(f'Unresolved retained infrastructure snapshot: {path}')
        if not entry.get('sha256') or digest(path)!=entry['sha256']:raise ValueError('Retained infrastructure requires its exact SHA256')
        return path
    # New collections derive the same summary column structure from domain builders.
    from human.pipelines.native.summary import export
    import pyarrow.parquet as pq
    manifests=[Path(native[group]['manifest']) for group in ('launches','shore_access','places')]
    matrix=run/'infrastructure-native.parquet';export(manifests,matrix)
    table=pq.read_table(matrix);names=['h3_index' if c=='H3_INDEX' else 'h3_resolution' if c=='H3_RESOLUTION' else c for c in table.column_names]
    table=table.rename_columns(names)
    path=run/'infrastructure.parquet';pq.write_table(table,path,compression='zstd')
    return path


def _canonical_columns(staging):
    """Retain the approved column order/types across supported pandas versions."""
    import base64
    import pyarrow as pa
    import pyarrow.parquet as pq
    from human.pipelines.delivery.validation import product_contract
    catalog_path=staging/'catalog.json'
    catalog=json.loads(catalog_path.read_text())
    specs={item['schema']:item for item in product_contract()['products'].values()}
    for product in catalog['products']:
        if product['schema']=='human.native_features.v1':continue
        path=staging/product['artifact']['path'];table=pq.read_table(path)
        spec=specs[product['schema']]
        columns=[c['name'] for c in spec['columns']]
        if set(table.column_names)!=set(columns):raise ValueError('Source inputs do not supply the full approved schema')
        schema=pa.ipc.read_schema(pa.BufferReader(base64.b64decode(spec['arrow_schema_base64'])))
        table=table.select(columns).cast(schema).replace_schema_metadata(table.schema.metadata)
        pq.write_table(table,path,compression='zstd')
        product['artifact']['sha256']=digest(path)
        path.with_suffix('.metadata.json').write_text(json.dumps(product,indent=2,sort_keys=True))
    catalog_path.write_text(json.dumps(catalog,indent=2,sort_keys=True))


def _runtime():
    import platform
    names=('numpy','pandas','pyarrow','geopandas','shapely','pyproj','h3','holidays','tzdata')
    code=hashlib.sha256()
    root=Path(__file__).resolve().parent
    for path in sorted(root.rglob('*.py')):
        code.update(str(path.relative_to(root)).encode());code.update(path.read_bytes())
    return {'python':platform.python_version(),'packages':{name:metadata.version(name) for name in names},'human_source_sha256':code.hexdigest()}


def get_human_data(config=None, *, workspace=None, output_dir=None, mode='live', workers=1, overwrite=True,
                   start_date=None, end_date=None, env_file=None, verbosity=None):
    """Run source collection and produce only static, calendar and native Parquets.

    Paths in config/human.yaml are relative to the explicit workspace (or
    HUMAN_WORKSPACE/cwd). Offline reuses verified original inputs. Live also runs
    configured domain acquisition or checksum-pinned HTTPS asset collection.
    Sources/receipts stay outside output; run-owned intermediates are removed on
    success and failure. Existing unrelated files and pre-existing caches are never
    swept. Current output is replaced only after complete validation, with rollback.
    """
    if mode not in {'offline','live'}:raise ValueError('mode must be offline or live')
    if isinstance(workers,bool) or not isinstance(workers,int) or not 1<=workers<=32:raise ValueError('workers must be an integer in [1,32]')
    root=Path(workspace or os.environ.get('HUMAN_WORKSPACE',Path.cwd())).expanduser().resolve()
    cfg,recipe=_recipe(config,root)
    calendar=cfg['calendar']
    if 'start_date' in calendar and 'end_date' in calendar:
        first,last=calendar['start_date'],calendar['end_date']
    else:
        # Backwards-compatible defaults from the small study configuration, before
        # reading its registry or contacting any provider. New recipes state these dates.
        time=json.loads((root/cfg['study_config']).read_text())['time']
        first=time['start'];last=(date.fromisoformat(time['end_exclusive'])-timedelta(days=1)).isoformat()
    settings=preflight(start_date,end_date,output_dir,verbosity,env_file,workspace=root,
        defaults={'START_DATE':first,'END_DATE':last,'DATA_DIR':cfg['output_dir']})
    output=settings.output_dir
    expected_rows={**cfg.get('expected_rows',{}),'dynamic-calendar.parquet':
        ((date.fromisoformat(settings.end_date)-date.fromisoformat(settings.start_date)).days+1)*5}
    log(settings.verbosity,'Preflight passed: calendar '+settings.start_date+' through '+settings.end_date+'; output '+str(output))
    work=(root/cfg['work_dir']).resolve();sources=(root/cfg['source_store']).resolve();receipts=(root/cfg['metadata_dir']).resolve()
    paths=[output,work,sources,receipts]
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(paths) for b in paths[i+1:]):
        raise ValueError('Output, processing, original sources and receipts must be disjoint directories')
    # Check existing destination before any collection; never sweep unknown files.
    before=None
    if output.exists() and any(p.name!='.DS_Store' for p in output.iterdir()):
        if not overwrite:raise FileExistsError(output)
        validate_delivery(output)
        before={name:digest(output/name) for name in PRODUCTS}
    study=load_static_study(root/cfg['study_config'])
    if study['identity']['h3_resolution']!=7:raise ValueError('Supported Human delivery requires the approved R7 grain')
    # Acquire the lock before mutable source/processing work, and always release it.
    output.parent.mkdir(parents=True,exist_ok=True)
    lock=output.parent/('.'+output.name+'.human.lock')
    try:lock.mkdir()
    except FileExistsError:raise RuntimeError(f'Another Human run owns {lock}; no files were changed') from None
    token=uuid.uuid4().hex
    receipt=receipts/token
    try:
        for asset in cfg.get('assets',[]):
            target=(root/asset['path']).resolve()
            if any(target.is_relative_to(directory) for directory in (output,work,receipts)):
                raise ValueError('Source assets must stay outside final output, processing and receipts')
        work.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='human-run-',dir=work) as temporary:
            run=Path(temporary)
            log(settings.verbosity,'Collecting/verifying native originals and configured assets')
            assets=collect_assets(cfg.get('assets',[]),root,mode=mode,byte_budget=cfg.get('max_download_bytes',0))
            spec,native_collection=collect_native(cfg['native'],root,run,sources/'native',mode=mode)
            # Verify actual native identities/checksums before the expensive census calculation.
            from human.pipelines.native.features import compose_native_features
            compose_native_features(spec)
            infrastructure=_infrastructure(cfg.get('infrastructure'),root,run,spec)
            log(settings.verbosity,'Preparing/verifying original-census inputs')
            manifest=_population(cfg['population'],root,run,study,sources,mode)
            # Reuse a matching retained calculation as a read-only input. A cache
            # miss calculates only in this run's disposable workspace.
            cache_workspace=None
            configured_cache=cfg['population'].get('cache_workspace')
            if configured_cache:
                from human import get_population
                candidate_cache=(root/configured_cache).resolve()
                plan=get_population(target_h3=study['cells'],h3_res=7,radii_km=(25,50,100),
                    include_bands=False,workspace=candidate_cache,source_manifest=manifest,
                    mode='offline',allow_partial=True,resource_limits={'workers':workers,'buffer_tolerance_m':0.25},dry_run=True)
                if plan['cache_available']:cache_workspace=candidate_cache
            log(settings.verbosity,'Building static census/infrastructure, civil calendar and native geometry products'+(' with a verified population cache' if cache_workspace else ' with uncached population calculation'))
            from human.pipelines.population.delivery import _export_static_study
            staging=run/'products'
            catalog_path=_export_static_study(study_config=root/cfg['study_config'],workspace=run/'calculation',source_manifest=manifest,
                output_dir=staging,retained_static=infrastructure,workers=workers,as_of=cfg['calendar']['as_of'],native_source_specification=spec,
                population_cache_workspace=cache_workspace,infrastructure_rebuilt=not bool(cfg.get('infrastructure')),
                calendar_start=settings.start_date,calendar_end=settings.end_date)
            _canonical_columns(staging)
            catalog=json.loads(catalog_path.read_text())
            pipeline={'schema':'human.pipeline.run.v1','recipe':recipe,'runtime':_runtime(),'mode':mode,'resolved_settings':settings.public(),
                'started_from_sources':{'population_manifest':{'path':str(manifest),'sha256':digest(manifest),'manifest':json.loads(manifest.read_text()),'temporary_derivation':manifest.is_relative_to(run)},'native':native_collection,'assets':assets},
                'population_cache_input_workspace':str(cache_workspace) if cache_workspace else None,
                'created_at_utc':datetime.now(timezone.utc).isoformat(),
                'cleanup':'All human-run processing files are disposable derivations and removed after success/failure. Original inputs and this receipt are retained.'}
            catalog['pipeline']=pipeline;catalog_path.write_text(json.dumps(catalog,indent=2,sort_keys=True))
            # Stage final files on the destination filesystem so publication uses renames.
            with tempfile.TemporaryDirectory(prefix='.'+output.name+'-staging-',dir=output.parent) as publication:
                candidate=Path(publication)/'final'
                published=False
                try:
                    log(settings.verbosity,'Validating the complete staged three-product delivery')
                    finalize_delivery(staging,candidate,receipt,final_data_root=output)
                    validation=validate_delivery(candidate,expected_rows=expected_rows)
                    if load_static_study(root/cfg['study_config'])['identity']!=study['identity']:raise ValueError('Study changed during Human run')
                    if before is not None:
                        read_catalog(output)
                        if {name:digest(output/name) for name in PRODUCTS}!=before:raise ValueError('Current Human data changed during processing')
                    elif output.exists() and any(p.name!='.DS_Store' for p in output.iterdir()):
                        raise ValueError('Another output appeared during Human processing')
                    backup=Path(publication)/'previous'
                    existed=output.exists()
                    if existed:output.rename(backup)
                    try:
                        candidate.rename(output)
                        validation=validate_delivery(output,expected_rows=expected_rows)
                    except BaseException:
                        if output.exists():output.rename(candidate)
                        if existed:backup.rename(output)
                        raise
                    published=True
                    # TemporaryDirectory removes the superseded run-owned backup only
                    # after the replacement has passed readback. Original sources remain.
                except BaseException:
                    if not published and receipt.exists():shutil.rmtree(receipt)
                    raise
            log(settings.verbosity,'Published three validated Parquets; run-owned processing and staging cleaned')
            return HumanData(output,receipt/'catalog.json',validation)
    finally:
        lock.rmdir()
