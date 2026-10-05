import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.special import expit

from human.activity_and_effort.land_reporting_opportunity.utc_context import (
    civil_calendar_segments, daylight_intervals, integrate_land_context,
    solar_altitude_deg, timezone_provenance,
)


def calendar(values=(1.,1.,1.), start='2025-07-19'):
    return pd.DataFrame({'local_date':pd.date_range(start,periods=len(values)), 'value':values})


def weather(start='2025-07-19T20:00Z', periods=9, **columns):
    return pd.DataFrame({'valid_time_utc':pd.date_range(start,periods=periods,freq='4h'),
                         'visibility_km':10.,'wind_speed_m_s':5.5,'precip_rate_mm_hr':0.,
                         'sample_status':'valid',**columns})


def integrate(start='2025-07-20T00:00Z',end='2025-07-21T00:00Z',**kwargs):
    args=dict(latitude=47.6062,longitude=-122.3321,calendar=calendar(),
              timezone='America/Los_Angeles',weather=weather(),distance_km=10.)
    args.update(kwargs)
    return integrate_land_context(start,end,**args)


def test_solar_events_against_independent_usno_reference():
    path=Path(__file__).resolve().parents[2]/'fixtures/usno_solar_utc.json'
    for case in json.loads(path.read_text()):
        start=pd.Timestamp(case['date'],tz='UTC');end=start+pd.Timedelta(days=1)
        intervals=daylight_intervals(start,end,case['latitude'],case['longitude'])
        rises=[a for a,b in intervals if a>start]
        sets=[b for a,b in intervals if b<end]
        for event in case['events']:
            reference=pd.Timestamp(case['date']+'T'+event['time']+'Z')
            values=rises if event['phen']=='Rise' else sets
            assert min(abs((t-reference).total_seconds()) for t in values)<90


@pytest.mark.parametrize('day,hours',[('2020-03-08',23),('2020-11-01',25),('2020-02-29',24)])
def test_civil_day_length_and_interval_conservation(day,hours):
    a=pd.Timestamp(day).tz_localize('America/Los_Angeles').tz_convert('UTC')
    b=(pd.Timestamp(day)+pd.Timedelta(days=1)).tz_localize('America/Los_Angeles').tz_convert('UTC')
    c=calendar((.75,),start=day)
    segments=civil_calendar_segments(c,a,b,timezone='America/Los_Angeles')
    assert segments.seconds.sum()==hours*3600
    mid=a+(b-a)/2
    pieces=[civil_calendar_segments(c,x,y,timezone='America/Los_Angeles') for x,y in [(a,mid),(mid,b)]]
    assert sum((p.seconds*p.value).sum() for p in pieces)==pytest.approx(hours*3600*.75)


def test_utc_local_year_boundary_and_missing_previous_date():
    c=calendar((1.,0.),start='2020-12-31')
    s=civil_calendar_segments(c,'2021-01-01T00:00Z','2021-01-02T00:00Z',timezone='America/Los_Angeles')
    assert s.seconds.tolist()==[8*3600,16*3600]
    assert (s.seconds*s.value).sum()/86400==pytest.approx(1/3)
    missing=civil_calendar_segments(c.iloc[1:],'2021-01-01T00:00Z','2021-01-02T00:00Z',timezone='America/Los_Angeles')
    assert pd.isna(missing.value.iloc[0])


def test_bc_and_wa_civil_rules_are_distinct_after_2026():
    c=calendar((1.,0.),start='2026-11-01')
    a,b='2026-11-02T00:00Z','2026-11-03T00:00Z'
    wa=civil_calendar_segments(c,a,b,timezone='America/Los_Angeles')
    bc=civil_calendar_segments(c,a,b,timezone='America/Vancouver')
    assert wa.seconds.iloc[0]==8*3600
    assert bc.seconds.iloc[0]==7*3600
    assert len(timezone_provenance('America/Vancouver')['tzif_sha256'])==64


def test_constant_factors_match_analytic_integral_and_preserve_inputs():
    c,w=calendar(),weather(precip_rate_mm_hr=1.)
    before_c,before_w=c.copy(deep=True),w.copy(deep=True)
    r=integrate(calendar=c,weather=w)
    day=r['daylight_fraction'];m=r['metrics']
    assert m['calendar_context']['value']==pytest.approx(1.)
    assert m['daylight_calendar_visibility']['value']==pytest.approx(day*.5)
    assert m['daylight_calendar_weather']['value']==pytest.approx(day*.5*.5**.7*(1+24/10)**(-.3))
    assert r['precipitation_estimate']['total_mm']==pytest.approx(24.)
    assert all(v['coverage_fraction']==pytest.approx(1.) for v in m.values())
    pd.testing.assert_frame_equal(c,before_c);pd.testing.assert_frame_equal(w,before_w)


def test_solar_calendar_and_rain_integrals_conserve_across_utc_split():
    a,mid,b='2025-07-20T00:00Z','2025-07-20T07:00Z','2025-07-21T00:00Z'
    c=calendar((.2,.9,.4));w=weather(precip_rate_mm_hr=np.arange(9,dtype=float))
    full=integrate(a,b,calendar=c,weather=w)
    parts=[integrate(x,y,calendar=c,weather=w) for x,y in [(a,mid),(mid,b)]]
    assert sum(p['daylight_seconds'] for p in parts)==pytest.approx(full['daylight_seconds'],abs=.02)
    for name in ['calendar_context','daylight_calendar','daylight_visibility','daylight_calendar_visibility_wind']:
        integral=sum(p['metrics'][name]['value']*p['interval_seconds'] for p in parts)
        assert integral==pytest.approx(full['metrics'][name]['value']*86400,abs=.03)
    assert sum(p['precipitation_estimate']['total_mm'] for p in parts)==pytest.approx(full['precipitation_estimate']['total_mm'])
    assert 'daylight_calendar_weather' not in parts[0]['metrics'] # fixed UTC-day rain response


def test_joint_calendar_daylight_is_not_product_of_daily_means():
    r=integrate(calendar=calendar((0.,1.,1.)))
    assert abs(r['metrics']['daylight_calendar']['value']-r['daylight_fraction']*r['metrics']['calendar_context']['value'])>.01


def test_unknown_weather_stays_unavailable_but_known_zero_is_preserved():
    w=weather(visibility_km=np.nan,wind_speed_m_s=np.nan,precip_rate_mm_hr=np.nan)
    r=integrate(weather=w)
    m=r['metrics']['daylight_calendar_weather']
    assert m['value'] is None and m['status']=='unavailable' and m['coverage_fraction']==0
    zero=integrate(calendar=calendar((0.,0.,0.)),weather=w)['metrics']['daylight_calendar_weather']
    assert zero['value']==0 and zero['status']=='derived' and zero['coverage_fraction']==1
    assert zero['upper_bound']==0


def test_missing_sample_breaks_support_no_extrapolation_or_available_time_normalization():
    w=weather();w.loc[5,'visibility_km']=np.nan
    r=integrate(weather=w);m=r['metrics']['daylight_calendar_visibility']
    assert 0<m['coverage_fraction']<1 and m['status']=='partial'
    assert m['value']<integrate()['metrics']['daylight_calendar_visibility']['value']
    assert m['upper_bound']>m['lower_bound']
    short=weather(start='2025-07-20T12:00Z',periods=2)
    assert integrate(weather=short)['metrics']['daylight_visibility']['coverage_fraction']<1
    gap=weather().iloc[[0,-1]]
    assert integrate(weather=gap)['metrics']['daylight_visibility']['value'] is None


def test_rain_missing_is_not_zero_daily_accumulation():
    w=weather();w.loc[2,'precip_rate_mm_hr']=np.nan
    r=integrate(weather=w)
    assert r['precipitation_estimate']['total_mm'] is None
    assert r['precipitation_estimate']['available_integral_mm']==0
    assert r['precipitation_estimate']['status']=='partial'
    assert r['metrics']['daylight_calendar_weather']['value'] is None
    assert r['metrics']['daylight_calendar_visibility_wind']['status']=='derived'


def test_zero_visibility_remains_known_when_calendar_and_wind_missing():
    r=integrate(calendar=calendar((np.nan,np.nan,np.nan)),weather=weather(visibility_km=0.,wind_speed_m_s=np.nan))
    m=r['metrics']['daylight_calendar_visibility_wind']
    assert m['value']==0 and m['coverage_fraction']==1
    assert r['metrics']['calendar_context']['value'] is None


def test_interpolation_is_before_nonlinearity_and_quadrature_converges():
    w=weather(visibility_km=np.linspace(0,20,9),wind_speed_m_s=np.linspace(1,10,9))
    coarse=integrate(weather=w,quadrature_seconds=120)
    fine=integrate(weather=w,quadrature_seconds=15)
    for name in fine['metrics']:
        assert coarse['metrics'][name]['value']==pytest.approx(fine['metrics'][name]['value'],abs=2e-5)
        assert fine['metrics'][name]['quadrature_refinement_delta']<2e-6
    assert fine['metrics']['daylight_calendar_visibility']['value']!=pytest.approx(fine['daylight_fraction']*expit((10-10)/2),abs=.01)


@pytest.mark.parametrize('change',[{'distance_km':-1},{'latitude':70},{'maximum_gap_hours':8},{'quadrature_seconds':0}])
def test_invalid_domain_or_parameters_rejected(change):
    with pytest.raises(ValueError):integrate(**change)


def test_naive_times_and_duplicate_source_keys_rejected():
    with pytest.raises(ValueError):integrate('2025-07-20','2025-07-21')
    with pytest.raises(ValueError):integrate(calendar=pd.concat([calendar(),calendar()]))
    with pytest.raises(ValueError):integrate(weather=pd.concat([weather(),weather()]))
    w=weather();w['valid_time_utc']=w.valid_time_utc.dt.tz_localize(None)
    with pytest.raises(ValueError):integrate(weather=w)
