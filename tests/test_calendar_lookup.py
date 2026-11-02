from datetime import date
import pandas as pd
import pytest
import tzdata

from human.pipelines.calendar.lookup import build_calendar_lookup


def test_actual_observed_and_future():
    frame = build_calendar_lookup('2021-07-04', '2021-07-06',
        jurisdictions=[('US', 'WA', 'America/Los_Angeles')], as_of='2021-07-04').set_index('date')
    assert frame.loc['2021-07-04', 'is_actual_holiday']
    assert not frame.loc['2021-07-04', 'is_observed_holiday']
    assert frame.loc['2021-07-05', 'is_observed_holiday']
    assert not frame.loc['2021-07-05', 'is_actual_holiday']
    assert frame.loc['2021-07-05', 'calendar_basis'] == 'rule_based_future'
    assert not any('effort' in c or 'viewing' in c for c in frame)


def test_dst_jurisdictions_and_history():
    frame = build_calendar_lookup('2009-01-01', '2027-01-01', as_of='2026-10-16')
    assert len(frame) == 6574 * 5
    assert not frame.duplicated(['date', 'jurisdiction']).any()
    assert set(frame.jurisdiction) == {'US', 'US-WA', 'US-OR', 'CA', 'CA-BC'}
    duration = (frame.local_day_end_utc-frame.local_day_start_utc).dt.total_seconds()/3600
    assert set(duration) == {23, 24, 25}
    assert frame.timezone_rules_version.eq(tzdata.__version__).all()
    bc = frame[frame.jurisdiction.eq('CA-BC')].set_index('date')
    assert bc.loc['2023-09-30', 'is_actual_holiday']
    assert not bc.loc['2023-10-02', 'is_observed_holiday']
    assert not bc.loc['2009-09-30', 'is_holiday']


def test_invalid_calendar_identity():
    with pytest.raises(ValueError):
        build_calendar_lookup('2026-01-02', '2026-01-01')
    with pytest.raises(ValueError, match='Duplicate'):
        build_calendar_lookup('2026-01-01', '2026-01-02',
            jurisdictions=[('US', None, 'UTC'), ('US', None, 'UTC')])


def test_active_catalog_advertises_lookup_without_effort_scores():
    from importlib.resources import files
    import yaml
    catalog = yaml.safe_load(files('human').joinpath('resources/feature_catalog.yaml').read_text())
    assert catalog['collections']['dynamic_calendar']['primary_key'] == ['date', 'country', 'timezone']
    for name in ['dynamic_calendar']:
        assert not any('effort' in f['name'] for f in catalog['collections'][name]['features'])


def test_compact_union_preserves_regional_and_coincident_observed_holidays():
    from human.pipelines.calendar.lookup import compact_calendar
    raw = build_calendar_lookup('2023-09-30', '2023-10-01', as_of='2023-09-30')
    compact = compact_calendar(raw).set_index('country')
    assert len(compact) == 2
    assert compact.loc['CA', 'is_holiday']
    assert 'National Day for Truth and Reconciliation' in compact.loc['CA', 'holiday_names']
    assert not compact.loc['US', 'is_holiday']
    assert compact.loc['US', 'holiday_names'] == ''
    raw.loc[raw.jurisdiction.eq('CA-BC'), 'observed_holiday_names'] = 'Synthetic coincident shifted day'
    names = compact_calendar(raw).set_index('country').loc['CA', 'holiday_names']
    assert 'Synthetic coincident shifted day' in names and 'National Day for Truth and Reconciliation' in names


def test_compact_iso_year_leap_day_and_deterministic_country_zone_identity():
    from human.pipelines.calendar.lookup import compact_calendar, COMPACT_KEY
    raw = build_calendar_lookup('2020-01-01', '2021-01-05', as_of='2020-01-01')
    frame = compact_calendar(raw)
    pd.testing.assert_frame_equal(frame, compact_calendar(raw.sample(frac=1, random_state=4)))
    assert len(frame)==len(raw)//5*2 and not frame.duplicated(COMPACT_KEY).any()
    day=frame[frame.date.eq('2021-01-01')].iloc[0]
    assert (day.iso_year, day.iso_week, day.year, day.month, day.day_of_year)==(2020,53,2021,1,1)
    leap=frame[frame.date.eq('2020-02-29')].iloc[0]
    assert leap.day_of_year==60 and leap.is_weekend
