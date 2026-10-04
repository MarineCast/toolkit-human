import pandas as pd
import pytest
from human.activity_and_effort.ferry.wsf_vessel_history import normalize_vessel_history


def epoch(text):
    return f'/Date({pd.Timestamp(text).value // 1_000_000}-0700)/'


def record(departure, arrival):
    return {'VesselId': 1, 'Vessel': 'Test', 'Departing': 'A', 'Arriving': 'B',
            'ScheduledDepart': epoch(departure), 'ActualDepart': epoch(departure),
            'EstArrival': epoch(arrival), 'Date': epoch(arrival)}


@pytest.mark.parametrize('departure,arrival,day', [
    ('2025-03-09T09:30:00Z', '2025-03-09T10:30:00Z', '2025-03-09'),
    ('2025-11-02T08:30:00Z', '2025-11-02T09:30:00Z', '2025-11-02'),
    ('2025-07-20T23:30:00Z', '2025-07-21T00:30:00Z', '2025-07-20'),
])
def test_duration_uses_elapsed_utc_across_dst_and_midnight(departure, arrival, day):
    frame = normalize_vessel_history([record(departure, arrival)], requested_start=day, requested_end=day)
    assert frame.operational_duration_minutes.tolist() == [60.0]
    assert frame.operational_duration_is_valid.tolist() == [True]
    assert str(frame.actual_departure_utc.dt.tz) == 'UTC'
    assert frame.actual_departure_local.dt.tz is None
    assert frame.service_date.iloc[0] == pd.Timestamp(day)


def test_fall_back_sailings_remain_distinct():
    rows = [record('2025-11-02T08:30:00Z', '2025-11-02T09:00:00Z'),
            record('2025-11-02T09:30:00Z', '2025-11-02T10:00:00Z')]
    frame = normalize_vessel_history(rows, requested_start='2025-11-02', requested_end='2025-11-02')
    assert len(frame) == 2
    assert frame.scheduled_departure_local.nunique() == 1
    assert frame.scheduled_departure_utc.nunique() == 2


def test_invalid_timestamp_stays_unavailable():
    row = record('2025-07-20T10:00:00Z', '2025-07-20T11:00:00Z')
    row['ActualDepart'] = None
    frame = normalize_vessel_history([row], requested_start='2025-07-20', requested_end='2025-07-20')
    assert frame.actual_departure_utc.isna().all()
    assert frame.operational_duration_minutes.isna().all()
    assert not frame.operational_duration_is_valid.any()


def test_empty_history_has_typed_utc_columns():
    frame = normalize_vessel_history([], requested_start='2025-01-01', requested_end='2025-01-02')
    assert frame.empty
    assert str(frame.actual_departure_utc.dt.tz) == 'UTC'
