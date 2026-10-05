import pandas as pd
import pytest
from human.activity_and_effort.ferry.utc_schedule import summarize_utc_schedule


def records(times, values):
    return pd.DataFrame({'sailing_id':[str(i) for i in range(len(times))],
        'segment_id':'a_b','direction_id':'a_to_b','departure_time_source':'scheduled',
        'departure_local':times,'reported_total_riders':pd.Series(values, dtype='float64')})


def test_true_utc_midnight_and_directions():
    f=records(['2025-07-20 16:59','2025-07-20 17:00','2025-07-20 17:00'],[1,2,3])
    f.loc[2,'direction_id']='b_to_a'
    daily,excluded=summarize_utc_schedule(f)
    assert excluded.empty
    assert len(daily)==3
    assert str(daily.valid_start_utc.dt.tz)=='UTC'
    assert (daily.valid_end_utc-daily.valid_start_utc).eq(pd.Timedelta(days=1)).all()
    assert set(daily.date_utc.astype(str))=={'2025-07-20','2025-07-21'}
    assert daily.reported_total_at_scheduled_departure_sum.sum()==6


def test_unknown_zero_partial_are_distinct():
    f=records(['2025-01-01 12:00','2025-01-02 12:00','2025-01-03 12:00','2025-01-03 13:00'],[None,0,4,None])
    daily,_=summarize_utc_schedule(f)
    assert daily.reported_total_at_scheduled_departure_sum_status.tolist()==['unavailable','observed','partial']
    assert pd.isna(daily.reported_total_at_scheduled_departure_sum.iloc[0])
    assert daily.reported_total_at_scheduled_departure_sum.iloc[1]==0
    assert daily.reported_total_record_coverage_fraction.tolist()==[0,1,0.5]
    assert daily.regional_traffic_coverage_fraction.isna().all()


def test_dst_and_bad_times_are_retained_in_ledger():
    f=records(['2025-03-09 02:30','2025-11-02 01:30',None,'2025-11-02 03:00'],[1,2,3,4])
    daily,excluded=summarize_utc_schedule(f)
    assert excluded.sailing_id.tolist()==['0','1','2']
    assert excluded.scheduled_departure_utc.isna().all()
    assert daily.utc_resolved_schedule_record_count.sum()==1
    assert daily.reported_total_at_scheduled_departure_sum.sum()==4


@pytest.mark.parametrize('column,value',[('sailing_id','0'),('segment_id',None),('departure_time_source','actual'),('reported_total_riders',-1),('reported_total_riders',float('inf'))])
def test_invalid_contract_fails(column,value):
    f=records(['2025-01-01 12:00','2025-01-01 13:00'],[1,2]);f.loc[1,column]=value
    with pytest.raises(ValueError):summarize_utc_schedule(f)


def test_empty_is_typed_and_inputs_unchanged():
    f=records([],[]);before=f.copy(deep=True)
    daily,excluded=summarize_utc_schedule(f)
    assert daily.empty and excluded.empty
    assert str(daily.valid_start_utc.dt.tz)=='UTC'
    pd.testing.assert_frame_equal(f,before)


@pytest.mark.parametrize('labels', [[0, 0, 0, 0, 0, 0], [4, 4, 2, 4, 2, 4], ['b', 'b', 'a', 'b', 'a', 'b']])
def test_duplicate_indexes_preserve_ledger_order_and_conservation(labels):
    frame = records([
        '2025-03-09 02:30', '2025-03-09 03:30',
        None, '2025-11-02 01:30',
        '2025-03-09 04:30', '2025-03-09 05:30',
    ], [10, 20, 30, 40, 0, None])
    frame.index = labels
    before = frame.copy(deep=True)
    daily, ledger = summarize_utc_schedule(frame)
    assert ledger.sailing_id.tolist() == ['0', '2', '3']
    assert ledger.index.tolist() == [labels[i] for i in [0, 2, 3]]
    assert ledger.utc_time_status.tolist() == [
        'ambiguous_or_nonexistent_local_time',
        'missing_or_invalid_local_time',
        'ambiguous_or_nonexistent_local_time',
    ]
    assert ledger.scheduled_departure_utc.isna().all()
    pd.testing.assert_frame_equal(ledger[frame.columns], frame.iloc[[0, 2, 3]])
    assert daily.utc_resolved_schedule_record_count.sum() + len(ledger) == len(frame)
    assert daily.reported_total_known_record_count.sum() + ledger.reported_total_riders.notna().sum() == frame.reported_total_riders.notna().sum()
    assert daily.reported_total_at_scheduled_departure_sum.sum() + ledger.reported_total_riders.sum() == frame.reported_total_riders.sum()
    assert daily.reported_total_record_coverage_fraction.tolist() == [pytest.approx(2 / 3)]
    # Index labels cannot change the scientific result or ledger row ordering.
    reference, reference_ledger = summarize_utc_schedule(frame.reset_index(drop=True))
    pd.testing.assert_frame_equal(daily, reference)
    pd.testing.assert_frame_equal(ledger.reset_index(drop=True), reference_ledger.reset_index(drop=True))
    pd.testing.assert_frame_equal(frame, before)
