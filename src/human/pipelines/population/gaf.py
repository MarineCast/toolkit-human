"""Stream the official 2021 Geographic Attribute File without inventing counts."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import zipfile

import pandas as pd

COLUMNS = ['PRUID_PRIDU','DAUID_ADIDU','DADGUID_ADIDUGD','DBUID_IDIDU',
           'DBDGUID_IDIDUGD','DBPOP2021_IDPOP2021','DBIR2021_IDRI2021']


@contextmanager
def _csv_source(path):
    path=Path(path)
    if path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as archive:
            members=[x for x in archive.namelist() if x.lower().endswith('.csv')]
            if len(members)!=1:
                raise ValueError('GAF ZIP requires exactly one CSV')
            if archive.testzip() is not None:
                raise ValueError('GAF ZIP integrity failure')
            with archive.open(members[0]) as source:
                yield source
    elif path.suffix.lower()=='.csv':
        with path.open('rb') as source:
            yield source
    else:
        raise ValueError('GAF requires CSV or ZIP')


def read_gaf_da_counts(path, *, province_code=None):
    """Aggregate complete DB counts to DA; any incomplete/missing block stays null.

    Identity and duplicate checks cover the whole supplied file, including rows
    outside an optional province selection. Latin-1 preserves the official CSV.
    """
    totals={};seen=set();national_population=0;incomplete=0
    with _csv_source(path) as source:
        for chunk in pd.read_csv(source,usecols=COLUMNS,dtype='string',
                                 encoding='latin-1',chunksize=100000):
            for pr,da,dguid,db,dbdguid,count,flag in chunk.itertuples(index=False,name=None):
                if (pd.isna(pr) or pd.isna(da) or pd.isna(dguid) or pd.isna(db)
                        or pd.isna(dbdguid) or pd.isna(flag)
                        or not re.fullmatch(r'\d{2}',pr)
                        or not re.fullmatch(r'\d{8}',da)
                        or not re.fullmatch(r'\d{11}',db)
                        or not da.startswith(pr) or not db.startswith(da)
                        or dguid!='2021S0512'+da or dbdguid!='2021S0513'+db
                        or flag not in {'T','F'}):
                    raise ValueError('Invalid 2021 GAF geography identity or enumeration flag')
                if db in seen:
                    raise ValueError('Duplicate GAF dissemination block: '+db)
                seen.add(db)
                absent=pd.isna(count) or str(count).strip()==''
                if not absent and not re.fullmatch(r'\d+',str(count)):
                    raise ValueError('Invalid GAF population count: '+str(count))
                value=None if absent else int(count)
                if value is not None and flag=='F':national_population+=value
                incomplete+=int(flag=='T')
                if province_code is not None and pr!=str(province_code):continue
                total=totals.setdefault(dguid,{'DGUID':dguid,'blocks':0,'available_block_population':0,
                    'incompletely_enumerated_blocks':0,'missing_block_counts':0})
                total['blocks']+=1
                total['available_block_population']+=value or 0
                total['incompletely_enumerated_blocks']+=int(flag=='T')
                total['missing_block_counts']+=int(absent)
    if not totals:
        raise ValueError('No GAF areas selected')
    records=[]
    for key in sorted(totals):
        row=totals[key]
        row['Population, 2021']=None if row['incompletely_enumerated_blocks'] or row['missing_block_counts'] else row['available_block_population']
        records.append(row)
    result=pd.DataFrame.from_records(records)
    result['Population, 2021']=pd.array(result['Population, 2021'],dtype='Int64')
    result.attrs['gaf_audit']={'national_blocks':len(seen),'national_complete_block_population':national_population,
        'national_incompletely_enumerated_blocks':incomplete,'selected_areas':len(result),
        'selected_unknown_areas':int(result['Population, 2021'].isna().sum())}
    return result
