"""Bounded source collection for the supported Human pipeline.

Original source snapshots are retained outside final output. Per-run normalized
artifacts live in the caller's temporary processing directory and can be rebuilt.
"""
from __future__ import annotations

from importlib import import_module
from importlib.resources import files
import json
from pathlib import Path

import yaml

from human.utils.artifacts import sha256_file as digest


from human.utils.downloads import MissingHumanSources, collect_assets


def native_specification(entry, root):
    """Resolve configured retained inputs, including explicit legacy root mappings."""
    path=(root/entry['specification']).resolve()
    if not path.is_file(): raise MissingHumanSources(f'Unresolved native source specification: {path}')
    if entry.get('sha256') and digest(path)!=entry['sha256']:
        raise ValueError('Native source specification checksum mismatch')
    spec=json.loads(path.read_text())
    mappings=entry.get('path_map',{})
    missing=[]
    for group in ('launches','shore_access','places'):
        for key,value in list(spec[group].items()):
            if key not in {'manifest','facilities','summary','catalog','access_lines','park_polygons'}:continue
            resolved=value
            for old,new in sorted(mappings.items(),key=lambda pair:-len(pair[0])):
                if value==old or value.startswith(old+'/'):
                    resolved=str((root/new).resolve())+value[len(old):];break
            target=Path(resolved)
            if not target.is_absolute():target=path.parent/target
            spec[group][key]=str(target.resolve())
            if key!='summary' and not target.is_file():missing.append(str(target.resolve()))
    if missing:
        raise MissingHumanSources('Unresolved native input locations (not evidence of source loss):\n'+'\n'.join(sorted(set(missing))))
    return spec, {'path':str(path),'sha256':digest(path),'path_map':mappings}


def _verify_raw_manifest(path):
    if not path.is_file():raise MissingHumanSources(f'Unresolved source acquisition receipt: {path}')
    manifest=json.loads(path.read_text())
    for item in manifest['artifacts']:
        source=Path(item['path'])
        if not source.is_file():raise MissingHumanSources(f'Unresolved original source snapshot: {source}')
        if digest(source)!=item['sha256']:raise ValueError(f'Original source checksum mismatch: {source}')
    return manifest


def collect_native(entry, root, run, source_store, *, mode):
    """Acquire native source families and normalize with the existing domain adapters.

    Optional provider failures retain their source availability records. None of
    this invokes app exports, ferry activity, terrain, routing or modeling.
    """
    if 'specification' in entry:
        return native_specification(entry,root)
    if entry.get('acquire') is not True:
        raise ValueError('native requires specification or acquire: true')
    families={
        'launches':('accessibility/boat_launch_access','boat_launch_facilities_r7.parquet','boat_launch_access_h3_r7.parquet','human.pipelines.native.boat_launches'),
        'shore_access':('accessibility/public_shore_access','public_shore_facilities_r7.parquet','public_shore_access_h3_r7.parquet','human.pipelines.native.shore_access'),
        'places':('demography_and_presence/places','places_of_interest.json',None,'human.pipelines.native.places'),
    }
    spec={'schema':'human.native_sources.v1'};sources=[]
    for group,(module,primary,summary,adapter) in families.items():
        configured=entry.get('configs',{}).get(group)
        if configured:
            text=(root/configured).read_text()
        else:
            text=files('human').joinpath('resources/config/data/human/'+module+'.yaml').read_text()
        cfg=yaml.safe_load(text)
        raw=source_store/group;output=run/'native'/group
        raw.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
        for key,value in cfg['raw'].items():
            cfg['raw'][key]=str(raw/(Path(value).name if key not in ('snapshot_dir','cache_dir') else 'snapshots'))
        for key,value in cfg['output'].items():cfg['output'][key]=str(output/Path(value).name)
        for key,value in cfg.get('inspection',{}).items():cfg['inspection'][key]=str(output/Path(value).name)
        cfg.pop('app_export',None)
        config=run/(group+'.yaml');config.write_text(yaml.safe_dump(cfg))
        receipt=Path(cfg['raw']['manifest_path'])
        if receipt.exists():_verify_raw_manifest(receipt)
        if mode=='live' and not receipt.exists():import_module(adapter+'.download').download(config,overwrite=False)
        raw_manifest=_verify_raw_manifest(receipt)
        import_module(adapter+'.build').build(config,allow_partial=True,overwrite=False)
        spec[group]={'manifest':cfg['output']['manifest_path'],'catalog' if group=='places' else 'facilities':str(output/primary)}
        if summary:spec[group]['summary']=str(output/summary)
        sources.append({'path':str(receipt),'sha256':digest(receipt),'original_sources':raw_manifest['sources']})
    spec['shore_access']['access_lines']=str(Path(yaml.safe_load((run/'shore_access.yaml').read_text())['raw']['snapshot_dir'])/'wa_public_access_lines.parquet')
    inventory=json.loads(Path(yaml.safe_load((run/'places.yaml').read_text())['raw']['inventory_path']).read_text())
    spec['places']['park_polygons']=inventory['wa_state_parks']
    return spec, {'method':'domain_source_adapters.v1','acquisition_receipts':sources,
        'derived_artifacts':'Facility/catalog/build files are temporary derivations; original source snapshots and acquisition receipts are retained.'}


__all__ = ["collect_native", "native_specification", "collect_assets", "MissingHumanSources"]
