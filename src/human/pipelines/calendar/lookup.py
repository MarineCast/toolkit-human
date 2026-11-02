"""Jurisdictional civil-date lookup; no inferred effort or viewing scores."""
from __future__ import annotations

from datetime import date, timezone as utc_timezone
from importlib.resources import files
from zoneinfo import ZoneInfo

import holidays
import pandas as pd
import tzdata

DEFAULT_JURISDICTIONS = (
    ('US', None, 'America/Los_Angeles'),
    ('US', 'WA', 'America/Los_Angeles'),
    ('US', 'OR', 'America/Los_Angeles'),
    ('CA', None, 'America/Vancouver'),
    ('CA', 'BC', 'America/Vancouver'),
)

COMPACT_SCHEMA = 'human.study.calendar_lookup.v2'
COMPACT_KEY = ['date', 'country', 'timezone']
COMPACT_COLUMNS = [*COMPACT_KEY, 'is_weekend', 'holiday_names', 'is_holiday',
                   'iso_year', 'iso_week', 'day_of_year', 'year', 'month']


def compact_calendar(frame):
    """Union actual and shifted holiday names across each country/reference zone.

    This is broad regional context, not a claim of nationwide observance. Keep
    every coincident holiday name; choosing only actual names can lose a shifted
    observance on the same date. Jurisdiction/rule provenance lives in metadata.
    """
    required = [*COMPACT_KEY, 'is_weekend', 'actual_holiday_names', 'observed_holiday_names', 'is_holiday']
    if frame[required].isna().any().any():
        raise ValueError('Calendar compaction requires known dates, zones, names and flags')
    records = []
    for key, group in frame.groupby(COMPACT_KEY, sort=True):
        if group.is_weekend.nunique() != 1:
            raise ValueError('Conflicting weekend flags within calendar group')
        names = {name for column in ('actual_holiday_names', 'observed_holiday_names')
                 for value in group[column] for name in value.split('; ') if name}
        if bool(names) != bool(group.is_holiday.any()):
            raise ValueError('Calendar holiday names and flags differ')
        records.append(dict(zip(COMPACT_KEY, key), is_weekend=bool(group.is_weekend.iloc[0]),
                            holiday_names='; '.join(sorted(names)), is_holiday=bool(names)))
    result = pd.DataFrame.from_records(records)
    dates = pd.to_datetime(result.date)
    iso = dates.dt.isocalendar()
    for name, values in [('iso_year', iso.year), ('iso_week', iso.week),
                         ('day_of_year', dates.dt.dayofyear), ('year', dates.dt.year), ('month', dates.dt.month)]:
        result[name] = values.astype('int64')
    return result[COMPACT_COLUMNS]


def compact_metadata(info):
    """Version the compact grain and keep rule pins outside repeated rows."""
    import copy
    result = copy.deepcopy(info)
    result.pop('artifact', None)
    result.update(schema=COMPACT_SCHEMA, primary_key=COMPACT_KEY,
        spatial_support='country/reference timezone; union of configured jurisdictions',
        jurisdictions=[{'country': c, 'subdivision': s, 'timezone': z} for c, s, z in DEFAULT_JURISDICTIONS],
        holiday_names_policy='Sorted distinct union of actual and shifted observed names across configured jurisdictions; empty string means no holiday.',
        calendar_fields={'iso_year': 'ISO week-numbering year', 'iso_week': 'ISO week number (1-53), paired with iso_year',
                         'day_of_year': 'Local civil day of year (1-366)', 'year': 'Civil year', 'month': 'Civil month (1-12)'},
        limitations=['Rule-based country/reference-zone context, not nationwide or employer-specific observance, attendance or measured activity.',
            'US combines federal, WA and OR; Canada combines federal and BC. Regional distinctions are intentionally condensed.',
            'Convert event timestamps to the applicable local civil date before joining on date, country and timezone.',
            'Reference zones do not cover every subregion (parts of Oregon use Mountain time). Local days may last 23, 24 or 25 hours.',
            'Dates after temporal_support.as_of are rule-based future context; rules may change.'])
    return result


def build_calendar_lookup(start, end_exclusive, *, jurisdictions=DEFAULT_JURISDICTIONS, as_of=None):
    """One row per local civil date/jurisdiction, including actual and shifted dates.

    National calendars are federal reference calendars, not a claim that every
    employer observes every listed holiday. Regional calendars follow applicable state/provincial
    rules; federal employment calendars remain separate. The timezone is explicit lookup support, not a jurisdiction-wide timezone
    map (notably parts of Oregon use Mountain time). Convert an event into its own
    local date before joining; do not join a UTC date label directly.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end_exclusive)
    if start.tzinfo is not None or end.tzinfo is not None or start != start.normalize() or end != end.normalize() or end <= start:
        raise ValueError('Calendar bounds must be ordered naive civil-date midnights')
    as_of = date.fromisoformat(str(as_of or date.today()))
    dates = pd.date_range(start, end, inclusive='left', freq='D')
    years = range(start.year - 1, end.year + 2)
    records, identities = [], set()
    for country, subdivision, timezone in jurisdictions:
        if country not in {'US', 'CA'}:
            raise ValueError('Calendar supports US and CA')
        identity = country + ('-' + subdivision if subdivision else '')
        if identity in identities:
            raise ValueError('Duplicate calendar jurisdiction')
        identities.add(identity)
        # Use the declared packaged rules, independent of the host OS database.
        with files('tzdata.zoneinfo').joinpath(*timezone.split('/')).open('rb') as handle:
            zone = ZoneInfo.from_file(handle, key=timezone)
        actual = holidays.country_holidays(country, subdiv=subdivision, years=years, observed=False, language='en_US')
        combined = holidays.country_holidays(country, subdiv=subdivision, years=years, observed=True, language='en_US')
        for ts in dates:
            day = ts.date()
            actual_names = set(actual.get_list(day))
            # Difference by name also preserves a shifted observance coinciding
            # with a different actual holiday on the same civil date.
            observed_names = set(combined.get_list(day)) - actual_names
            # pandas can re-resolve a ZoneInfo key against its host timezone cache.
            # Python's aware datetime conversion honors the actual loaded rule file.
            utc_start = pd.Timestamp(ts.to_pydatetime().replace(tzinfo=zone).astimezone(utc_timezone.utc))
            utc_end = pd.Timestamp((ts + pd.Timedelta(days=1)).to_pydatetime().replace(tzinfo=zone).astimezone(utc_timezone.utc))
            records.append({
                'date': day.isoformat(), 'jurisdiction': identity, 'country': country,
                'subdivision': subdivision, 'timezone': timezone,
                'local_day_start_utc': utc_start, 'local_day_end_utc': utc_end,
                'day_of_week': day.weekday(), 'is_weekend': day.weekday() >= 5,
                'is_actual_holiday': bool(actual_names), 'is_observed_holiday': bool(observed_names),
                'actual_holiday_names': '; '.join(sorted(actual_names)),
                'observed_holiday_names': '; '.join(sorted(observed_names)),
                'is_holiday': bool(actual_names or observed_names),
                'calendar_basis': 'rule_based_future' if day > as_of else 'rule_based_calendar',
                'rules_version': holidays.__version__, 'timezone_rules_version': tzdata.__version__,
                'as_of': as_of.isoformat(),
            })
    frame = pd.DataFrame.from_records(records)
    return frame.sort_values(['date', 'jurisdiction']).reset_index(drop=True)
