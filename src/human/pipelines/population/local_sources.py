"""Register and verify user-supplied extracted 2021 StatCan inputs without repacking."""
from __future__ import annotations

from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
import argparse
import csv
import json
import os

from human.utils.artifacts import sha256_file


def verify_provenance(provenance, primary, expected_sha256):
    """Bind an extracted source and all companion bytes to one immutable receipt."""
    receipt=Path(provenance['receipt'])
    if not receipt.is_file() or sha256_file(receipt)!=provenance['receipt_sha256']:
        raise ValueError(f'Extracted census provenance receipt missing or checksum mismatch: {receipt}')
    record=json.loads(receipt.read_text())
    if record.get('schema')!='human.source.extracted.v1' or record.get('original_provider_zip_available') is not False:
        raise ValueError('Unsupported extracted-source provenance')
    found=False
    for item in record['members']:
        original=receipt.parent/item['relative_path']
        target=original.resolve()
        if not target.is_relative_to(receipt.parent.resolve()) or original.is_symlink():
            raise ValueError('Extracted census member escapes source store')
        if not target.is_file() or target.stat().st_size!=item['bytes'] or sha256_file(target)!=item['sha256']:
            raise ValueError(f'Extracted census member missing or checksum mismatch: {target}')
        if target==Path(primary).resolve():
            if item['sha256']!=expected_sha256:raise ValueError('Primary source pin differs from extracted provenance')
            found=True
    if not found:raise ValueError('Primary source is not bound by extracted provenance')
    return record


def register_extracted_sources(geography_dir, profile_dir, *, workspace):
    """Verify then move two extracted folders into one ignored canonical store.

    Original ZIP identity is unavailable and remains explicitly distinct. This
    records current member hashes, source format/vintage and user-supplied origin;
    it does not normalize census counts or publish final Human products.
    """
    root=Path(workspace).resolve()
    source_dirs=[Path(geography_dir).resolve(),Path(profile_dir).resolve()]
    if any(not p.is_dir() for p in source_dirs):raise FileNotFoundError('Both extracted source folders must exist before registration')
    if source_dirs[0]==source_dirs[1]:raise ValueError('Geography and Profile must be distinct folders')
    store=root/'data/raw/statcan/2021'
    spec_path=store/'census-sources.local.json'
    if spec_path.exists():raise FileExistsError(f'Local source recipe already exists; reuse it without re-dating provenance: {spec_path}')
    spec=json.loads(files('human').joinpath('resources/config/census-sources.v1.json').read_text())
    entry=spec['canada'][0]
    names=['lda_000b21a_e','98-401-X2021006_BC_CB_eng_CSV']
    primary_names=['lda_000b21a_e.shp','98-401-X2021006_English_CSV_data_BritishColumbia.csv']
    for source,name in zip(source_dirs,names):
        target=store/name
        if target.exists() and source!=target.resolve():raise FileExistsError(f'Canonical source folder already exists: {target}')
        if source.is_relative_to(store.resolve()) and source!=target.resolve():raise ValueError('Unexpected nested source location')
    geography=source_dirs[0]/primary_names[0]
    required={f'lda_000b21a_e{suffix}' for suffix in ('.shp','.shx','.dbf','.prj','.xml')}
    if not required.issubset({p.name for p in source_dirs[0].iterdir()}):raise ValueError('2021 cartographic DA shapefile companions are incomplete')
    if '2021' not in (source_dirs[0]/'lda_000b21a_e.xml').read_text(errors='replace'):raise ValueError('Boundary metadata does not identify 2021')
    import geopandas as gpd
    sample=gpd.read_file(geography,rows=2)
    if sample.empty or not {'DAUID','DGUID','PRUID'}.issubset(sample.columns) or sample.crs.to_epsg()!=3347:
        raise ValueError('Expected the 2021 cartographic DA geography in EPSG:3347')
    if not sample.DGUID.astype(str).str.startswith('2021S0512').all():raise ValueError('Unexpected geography vintage or level')
    profile=source_dirs[1]/primary_names[1]
    with profile.open(encoding='latin-1',newline='') as source:
        reader=csv.DictReader(source);first=next(reader)
    if first.get('CENSUS_YEAR')!='2021' or first.get('CHARACTERISTIC_NAME')!='Population, 2021' or 'C1_COUNT_TOTAL' not in first:
        raise ValueError('Expected the English 2021 comprehensive Census Profile CSV')
    inventories=[]
    for source in source_dirs:
        members=[]
        for p in sorted(source.iterdir()):
            if p.is_symlink() or not p.is_file():raise ValueError('Extracted source folders must contain only regular files')
            before=p.stat();digest=sha256_file(p);after=p.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed during verification')
            members.append({'name':p.name,'bytes':before.st_size,'sha256':digest})
        inventories.append(members)
    store.mkdir(parents=True,exist_ok=True)
    moved=[];created=[]
    try:
        for kind,source,name,primary_name,members in zip(('geography','population'),source_dirs,names,primary_names,inventories):
            target=store/name
            if source!=target.resolve():
                if source.stat().st_dev!=store.stat().st_dev:raise ValueError('Move must stay on one filesystem; no bulk copy is performed')
                os.rename(source,target);moved.append((source,target))
            primary=target/primary_name
            record={'schema':'human.source.extracted.v1','representation':'user_supplied_extracted_files',
                'original_provider_zip_available':False,'provider_url':entry[kind+'_url'],
                'provider_original_zip_sha256':entry[kind+'_sha256'],
                'source_vintage':'2021','observed_at_utc':datetime.now(timezone.utc).isoformat(),
                'origin':'User supplied expanded download; original ZIP unavailable; original archive hash/CRC not recoverable',
                'members':[{**{k:v for k,v in item.items() if k!='name'},'relative_path':name+'/'+item['name']} for item in members],
                'format_check':'Bounded geometry/CSV header and first-record inspection; no complete census normalization or count comparison'}
            receipt=store/(name+'.extracted.receipt.json')
            with receipt.open('x') as f:json.dump(record,f,indent=2,sort_keys=True);f.write('\n')
            created.append(receipt)
            pin=next(r['sha256'] for r in members if r['name']==primary_name)
            entry[kind]=primary.relative_to(root).as_posix();entry[kind+'_sha256']=pin
            entry.pop(kind+'_url')  # The provider URL serves a ZIP, not these bytes.
            entry[kind+'_provenance']={'representation':'user_supplied_extracted_files',
                'receipt':receipt.relative_to(root).as_posix(),'receipt_sha256':sha256_file(receipt),
                'provider_url':record['provider_url'],'provider_original_zip_sha256':record['provider_original_zip_sha256']}
            verify_provenance({**entry[kind+'_provenance'],'receipt':str(receipt)},primary,pin)
        entry['population_format']='statcan_profile_2021'
        spec['local_source_note']='Extracted user-supplied files; original upstream ZIP hashes remain unchanged in the packaged official recipe. Full numerical equivalence has not been rebuilt.'
        with spec_path.open('x') as f:json.dump(spec,f,indent=2);f.write('\n')
        created.append(spec_path)
        return spec_path
    except BaseException:
        for p in reversed(created):p.unlink(missing_ok=True)
        for source,target in reversed(moved):os.rename(target,source)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geography-dir',type=Path,required=True)
    parser.add_argument('--profile-dir',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,default=Path.cwd())
    args=parser.parse_args()
    print(register_extracted_sources(args.geography_dir,args.profile_dir,workspace=args.workspace))


if __name__=='__main__':main()
