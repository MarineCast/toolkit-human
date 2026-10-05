"""UTC-day summaries of retained WSF schedule records, never actual vessel exposure."""
from __future__ import annotations

import numpy as np
import pandas as pd


def summarize_utc_schedule(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return route/direction UTC-day records and an unresolved-time ledger.

    Counts describe only the supplied, uniquely identified, UTC-resolved records.
    Ridership keeps the normalized source's reported-total definition. Missing
    ridership is not zero; coverage measures populated values among included
    records, not regional completeness or actual departures. No spatial transfer.
    """
    required = ['sailing_id', 'segment_id', 'direction_id', 'departure_local',
                'departure_time_source', 'reported_total_riders']
    missing = set(required).difference(frame.columns)
    if missing:
        raise ValueError(f'Missing schedule columns: {sorted(missing)}')
    work = frame.copy()
    for key in ['sailing_id', 'segment_id', 'direction_id']:
        if work[key].isna().any() or work[key].astype(str).str.strip().eq('').any():
            raise ValueError(f'Null/empty schedule identity: {key}')
    if work.sailing_id.duplicated().any():
        raise ValueError('Duplicate sailing_id; resolve source identity before aggregation')
    if not work.departure_time_source.eq('scheduled').all():
        raise ValueError('Only explicitly scheduled timestamps are supported')
    times = pd.to_datetime(work.departure_local, errors='coerce')
    if times.dt.tz is not None:
        raise ValueError('departure_local must be naive America/Los_Angeles time')
    values = pd.to_numeric(work.reported_total_riders, errors='raise')
    known = values.dropna()
    if (known < 0).any() or not np.isfinite(known).all():
        raise ValueError('Reported totals must be finite nonnegative values or null')
    work['reported_total_riders'] = values
    work['scheduled_departure_utc'] = times.dt.tz_localize(
        'America/Los_Angeles', ambiguous='NaT', nonexistent='NaT').dt.tz_convert('UTC')
    unresolved = work.loc[work.scheduled_departure_utc.isna()].copy()
    unresolved['utc_time_status'] = np.where(
        times.loc[unresolved.index].isna(), 'missing_or_invalid_local_time',
        'ambiguous_or_nonexistent_local_time')
    valid = work.loc[work.scheduled_departure_utc.notna()].copy()
    valid['valid_start_utc'] = valid.scheduled_departure_utc.dt.floor('D')
    keys = ['segment_id', 'direction_id', 'valid_start_utc']
    result = valid.groupby(keys, observed=True, sort=True).agg(
        utc_resolved_schedule_record_count=('sailing_id', 'size'),
        reported_total_at_scheduled_departure_sum=('reported_total_riders', lambda s: s.sum(min_count=1)),
        reported_total_known_record_count=('reported_total_riders', 'count'),
    ).reset_index()
    result['valid_end_utc'] = result.valid_start_utc + pd.Timedelta(days=1)
    result['date_utc'] = result.valid_start_utc.dt.date
    result['utc_resolved_schedule_record_count_status'] = 'observed'
    result['reported_total_record_coverage_fraction'] = (
        result.reported_total_known_record_count / result.utc_resolved_schedule_record_count)
    result['reported_total_at_scheduled_departure_sum_status'] = np.select(
        [result.reported_total_known_record_count.eq(0),
         result.reported_total_record_coverage_fraction.lt(1)],
        ['unavailable', 'partial'], default='observed')
    result['regional_traffic_coverage_fraction'] = np.nan
    result['regional_traffic_coverage_status'] = 'unknown'
    return result, unresolved
