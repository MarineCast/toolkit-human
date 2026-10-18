"""Streaming source table readers used by the supported census normalization."""
from __future__ import annotations
from contextlib import ExitStack
from pathlib import Path
import zipfile
import pandas as pd
from human.pipelines.population.errors import PopulationDataError

STATCAN_PROFILE_REQUIRED_COLUMNS = ("DGUID", "GEO_LEVEL", "CHARACTERISTIC_ID", "C1_COUNT_TOTAL")


def read_population_table(path: str | Path) -> pd.DataFrame:
    """Read a normalized CSV/Parquet table or comprehensive Profile ZIP."""
    resolved=Path(path).expanduser().resolve()
    suffix=resolved.suffix.lower()
    if suffix=='.zip':return read_statcan_profile(resolved)
    if suffix=='.csv':return pd.read_csv(resolved)
    if suffix in {'.parquet','.pq'}:return pd.read_parquet(resolved)
    raise ValueError(f'Unsupported population table format: {resolved}')


def read_statcan_profile(path: str | Path) -> pd.DataFrame:
    """Stream DA population from an explicitly selected comprehensive ZIP/CSV.

    The selected rows/columns and suppression behavior are identical for the
    original ZIP and verified extracted CSV; source representation is provenance.
    """
    resolved=Path(path).expanduser().resolve()
    with ExitStack() as stack:
        if resolved.suffix.lower()=='.zip':
            archive=stack.enter_context(zipfile.ZipFile(resolved))
            members=[n for n in archive.namelist() if n.lower().endswith('.csv') and '_csv_data_' in n.lower()]
            if len(members)!=1:
                raise PopulationDataError('Statistics Canada Census Profile ZIP must contain exactly one data CSV; '+f'found {members}.')
            source=stack.enter_context(archive.open(members[0]))
        elif resolved.suffix.lower()=='.csv':source=stack.enter_context(resolved.open('rb'))
        else:raise ValueError('Comprehensive Census Profile requires a ZIP or explicit CSV')
        frames=[]
        for chunk in pd.read_csv(source,usecols=list(STATCAN_PROFILE_REQUIRED_COLUMNS),dtype='string',encoding='latin-1',chunksize=250_000):
            characteristic_ids=pd.to_numeric(chunk['CHARACTERISTIC_ID'],errors='coerce')
            selected=chunk[chunk['GEO_LEVEL'].eq('Dissemination area') & characteristic_ids.eq(1)][['DGUID','C1_COUNT_TOTAL']]
            if not selected.empty:frames.append(selected)
    if not frames:raise PopulationDataError(f'Statistics Canada Census Profile contains no DA population rows: {resolved}')
    return pd.concat(frames,ignore_index=True).rename(columns={'C1_COUNT_TOTAL':'Population, 2021'})
